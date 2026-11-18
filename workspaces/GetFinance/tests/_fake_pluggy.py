"""A fake Pluggy for the landing2raw tests (df-ahua.10), and the harness that runs the real script
against it. Every payload is invented here in Pluggy's shape (field names and types only); no value
comes from a recorded response or from data/.

Why in-process and not a subprocess against a local HTTP server: landing2raw.py has no base-URL
setting (functions.py hardcodes https://api.pluggy.ai) and calls requests.get / requests.post by
module attribute. So the tests exec the REAL script file, exactly as _delta_util.run_script runs every
task script, with requests.get / requests.post bound to this fake. The run fails closed:
- the fake serves https://api.pluggy.ai only, and raises UnexpectedCall on any URL or route it does
  not know (no call can fall through to a real request);
- sockets refuse to connect or resolve, and requests.Session refuses to send, so a code path that
  bypasses requests.get / requests.post errors instead of reaching the network;
- dotenv.load_dotenv is a no-op, so the real .env is never read; credentials are fake env values;
- every parquet write and mkdir outside the test's temp root raises, so data/raw cannot be touched.
"""
import contextlib
import copy
import datetime as _datetime
import hashlib
import json
import os
import pathlib
import shutil
import socket
import sys
from urllib.parse import parse_qs, urlsplit

import dotenv
import polars as pl
import requests

from tests import _delta_util

HOST = "api.pluggy.ai"
TOKEN = "fake-api-key"
FAKE_ENV = {"CLIENT_ID": "test-client", "CLIENT_SECRET": "test-secret"}
DEFAULT_NOW = _datetime.datetime(2030, 1, 15, 12, 0, tzinfo=_datetime.UTC)
ENTITIES = ("accounts", "transactions", "bills", "investments")


class UnexpectedCall(AssertionError):
    """The script asked for something the fake does not serve."""


class RunawayLoop(AssertionError):
    """The script kept calling past the fake's call budget: a pagination loop that does not end."""


class NetworkBlocked(AssertionError):
    """A real network call was attempted."""


class Reply:
    """One HTTP answer: a status and a JSON body (None: no JSON, as an HTML error page)."""

    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self.body = body


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = "" if body is None else json.dumps(body)

    def json(self):
        if self._body is None:
            raise ValueError("no JSON body")
        return copy.deepcopy(self._body)   # the script mutates what it gets; serve a fresh copy


# --- payload builders, Pluggy's shape, made-up values -------------------------------------------

def account(acc_id, acc_type="BANK", item_id="item-a", **extra):
    row = {"id": acc_id, "itemId": item_id, "type": acc_type,
           "subtype": "CHECKING_ACCOUNT" if acc_type == "BANK" else "CREDIT_CARD",
           "name": f"Conta {acc_id}", "balance": 100.5, "currencyCode": "BRL", "number": "0000-0"}
    row.update(extra)
    return row


def txn(txn_id, amount=-12.34, **extra):
    row = {"id": txn_id, "date": "2030-01-10T03:00:00.000Z", "description": f"Compra {txn_id}",
           "descriptionRaw": None, "amount": amount, "amountInAccountCurrency": None,
           "currencyCode": "BRL", "category": "Shopping", "categoryId": "08000000",
           "type": "DEBIT" if amount < 0 else "CREDIT", "status": "POSTED", "merchant": None}
    row.update(extra)
    return row


def bill(bill_id, **extra):
    row = {"id": bill_id, "dueDate": "2030-02-10T00:00:00.000Z", "totalAmount": 250.0,
           "totalAmountCurrencyCode": "BRL", "minimumPaymentAmount": 25.0,
           "allowsInstallments": True, "financeCharges": [], "payments": []}
    row.update(extra)
    return row


def investment(inv_id, **extra):
    row = {"id": inv_id, "type": "FIXED_INCOME", "subtype": "CDB", "name": f"CDB {inv_id}",
           "balance": 1000.0, "amount": 1000.0, "amountOriginal": 950.0, "amountProfit": 50.0,
           "currencyCode": "BRL", "status": "ACTIVE", "date": "2030-01-14T00:00:00.000Z"}
    row.update(extra)
    return row


def offset_pages(rows, page_size, **override):
    """Honest page-number pages (bills, investments): `total` and `totalPages` tell the truth."""
    chunks = [rows[i:i + page_size] for i in range(0, len(rows), page_size)] or [[]]
    pages = [{"total": len(rows), "totalPages": len(chunks), "page": n, "results": chunk}
             for n, chunk in enumerate(chunks, start=1)]
    for page in pages:
        page.update(override)
    return pages


# --- the fake ---------------------------------------------------------------------------------

class FakePluggy:
    """Serves https://api.pluggy.ai from dicts the test fills in.

    accounts[item_id]      -> one entry
    transactions[acc_id]   -> list of entries, one per cursor page (v2: follow `next`)
    bills[acc_id], investments[item_id] -> list of entries, one per `page` number
    An entry is a dict (a 200 body), a Reply, or an exception instance (raised, as requests would).
    A cursor page without a "next" key gets one pointing at the following page (None on the last);
    a page number past the list gets an empty page with the first page's `total`, as Pluggy answers.
    """

    def __init__(self, max_calls=200):
        self.auth = Reply(200, {"apiKey": TOKEN})
        self.accounts = {}
        self.transactions = {}
        self.bills = {}
        self.investments = {}
        self.auth_body = None    # the JSON body of the last POST /auth
        self.calls = []          # (method, url, headers, timeout)
        self.max_calls = max_calls

    # requests.post / requests.get stand-ins -------------------------------------------------
    def post(self, url, json=None, headers=None, timeout=None, **kwargs):
        self._record("POST", url, headers, timeout, kwargs)
        if url != f"https://{HOST}/auth":
            raise UnexpectedCall(f"POST {url}")
        self.auth_body = json
        return self._serve(self.auth)

    def get(self, url, headers=None, timeout=None, **kwargs):
        self._record("GET", url, headers, timeout, kwargs)
        parts = urlsplit(url)
        if (parts.scheme, parts.netloc) != ("https", HOST):
            raise UnexpectedCall(f"GET {url}")
        if (headers or {}).get("X-API-KEY") != TOKEN:
            return FakeResponse(403, {"message": "invalid api key"})
        q = {k: v[0] for k, v in parse_qs(parts.query).items()}
        if parts.path == "/accounts":
            return self._serve(self._lookup(self.accounts, q.get("itemId"), url))
        if parts.path == "/v2/transactions":
            if "pageSize" in q:   # the real v2 endpoint rejects it (functions.py comment)
                return FakeResponse(400, {"message": "property pageSize should not exist"})
            return self._cursor_page(q, url)
        if parts.path in ("/bills", "/investments"):
            table, key = (self.bills, "accountId") if parts.path == "/bills" else (self.investments, "itemId")
            return self._numbered_page(self._lookup(table, q.get(key), url), int(q.get("page", "1")))
        raise UnexpectedCall(f"GET {url}")

    # helpers -------------------------------------------------------------------------------
    def _record(self, method, url, headers, timeout, kwargs):
        if kwargs:
            raise UnexpectedCall(f"{method} {url}: unexpected arguments {sorted(kwargs)}")
        self.calls.append((method, url, dict(headers or {}), timeout))
        if len(self.calls) > self.max_calls:
            raise RunawayLoop(f"more than {self.max_calls} calls; last {method} {url}")

    @staticmethod
    def _lookup(table, key, url):
        if key not in table:
            raise UnexpectedCall(f"GET {url}: no fake data for {key!r}")
        return table[key]

    @staticmethod
    def _serve(entry):
        if isinstance(entry, BaseException):
            raise entry
        if isinstance(entry, Reply):
            return FakeResponse(entry.status_code, entry.body)
        return FakeResponse(200, entry)

    def _cursor_page(self, q, url):
        acc = q.get("accountId")
        pages = self._lookup(self.transactions, acc, url)
        index = int(q.get("cursor", "0"))
        if index >= len(pages):
            raise UnexpectedCall(f"GET {url}: cursor past the last page")
        entry = pages[index]
        if isinstance(entry, dict) and "next" not in entry:
            following = f"accountId={acc}&cursor={index + 1}" if index + 1 < len(pages) else None
            entry = {**entry, "next": following}
        return self._serve(entry)

    def _numbered_page(self, pages, number):
        if number <= len(pages):
            return self._serve(pages[number - 1])
        first = pages[0] if pages and isinstance(pages[0], dict) else {}
        return FakeResponse(200, {"total": first.get("total", 0), "totalPages": first.get("totalPages", 0),
                                  "page": number, "results": []})

    def urls(self, path=None):
        return [u for _, u, _, _ in self.calls if path is None or urlsplit(u).path == path]


# --- fail-closed installation --------------------------------------------------------------

def _refuse(*args, **kwargs):
    raise NetworkBlocked("real network access attempted in a landing2raw test")


def _inside(path, root):
    return pathlib.Path(os.path.abspath(path)).is_relative_to(os.path.abspath(root))


def install(setattr_, fake, root):
    """Bind requests to `fake`, block the network and .env, and confine writes to `root`.
    `setattr_` is monkeypatch.setattr in a test, plain setattr in a crash-sweep child."""
    setattr_(requests, "get", fake.get)
    setattr_(requests, "post", fake.post)
    setattr_(requests.Session, "request", _refuse)
    setattr_(requests.Session, "send", _refuse)
    setattr_(socket.socket, "connect", _refuse)
    setattr_(socket.socket, "connect_ex", _refuse)
    setattr_(socket, "create_connection", _refuse)
    setattr_(socket, "getaddrinfo", _refuse)
    setattr_(dotenv, "load_dotenv", lambda *a, **k: False)

    write_parquet, mkdir = pl.DataFrame.write_parquet, pathlib.Path.mkdir

    def guarded_write_parquet(self, file, *args, **kwargs):
        if not _inside(file, root):
            raise AssertionError(f"parquet write outside the test root: {file}")
        return write_parquet(self, file, *args, **kwargs)

    def guarded_mkdir(self, *args, **kwargs):
        if not _inside(self, root):
            raise AssertionError(f"mkdir outside the test root: {self}")
        return mkdir(self, *args, **kwargs)

    setattr_(pl.DataFrame, "write_parquet", guarded_write_parquet)
    setattr_(pathlib.Path, "mkdir", guarded_mkdir)


@contextlib.contextmanager
def frozen_now(instant):
    """datetime.now() returns `instant` (converted to the zone asked for) inside the block."""
    real = _datetime.datetime

    class Frozen(real):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

    _datetime.datetime = Frozen
    try:
        yield
    finally:
        _datetime.datetime = real


def make_workspace(ws):
    """A temp workspace holding byte-for-byte copies of this checkout's landing2raw.py, functions.py
    and connections/: the script runs unmodified (its own exec of functions.py included), and the raw
    connection resolves through the real _workspace_root / connections.yaml to ws/data/raw."""
    for rel in ("functions/functions.py", "landing/landing2raw.py"):
        (ws / "scripts" / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_delta_util.SCRIPTS / rel, ws / "scripts" / rel)
    shutil.copytree(_delta_util.ROOT / "connections", ws / "connections")
    return ws / "data" / "raw"


def run_landing(ws, now=DEFAULT_NOW):
    """Run ws's landing2raw.py --target raw in this process, as `python landing2raw.py` would, with
    datetime.now() frozen at `now`. Exceptions and SystemExit propagate. The caller has set the env
    (FAKE_ENV, ITEM_IDS) and called install()."""
    script = ws / "scripts" / "landing" / "landing2raw.py"
    ns = {"__file__": str(script), "__name__": "__main__"}
    old_argv, sys.argv = sys.argv, [str(script), "--target", "raw"]
    try:
        with frozen_now(now):
            exec(compile(script.read_text(encoding="utf-8"), str(script), "exec"), ns)
    finally:
        sys.argv = old_argv
    return ns


# --- reading what a run left -----------------------------------------------------------------

def partition_files(raw, entity=None):
    """The files raw2cleansed reads: <entity>/ingestion_date=*/data.parquet."""
    pattern = f"{entity or '*'}/ingestion_date=*/data.parquet"
    return sorted(raw.glob(pattern))


def hashes(root):
    """{relative path: sha256} of every file under root."""
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def write_partition(raw, entity, day, rows):
    """A raw partition as landing2raw writes it (every column Utf8), for a starting state."""
    part = raw / entity / f"ingestion_date={day}"
    part.mkdir(parents=True, exist_ok=True)
    cols = list(dict.fromkeys(k for r in rows for k in r))
    pl.DataFrame({c: [None if r.get(c) is None else str(r[c]) for r in rows] for c in cols},
                 schema={c: pl.Utf8 for c in cols}).write_parquet(part / "data.parquet")
    return part / "data.parquet"
