"""landing2raw.py against a fake Pluggy (ADR 0004, df-ahua.10): the one script that writes data/raw,
which cannot be rebuilt, so a bug there is not undone by a rerun.

The real script file runs in this process against hand-built responses (tests/_fake_pluggy.py says
how, and why the network, the real .env and the real data/ are out of reach). Each test states one
rule. "Pins" marks current behaviour kept on purpose so a change to it is a decision, not an accident;
xfail(strict=True) marks a bug or suspicion with its bead: the test states the behaviour we want and
starts passing (so failing the strict xfail) when the bead is fixed.
"""
import ast
import os
import re
import shutil
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import PurePosixPath

import polars as pl
import pytest
import requests
from hypothesis import given, settings
from hypothesis import strategies as st

from tests import _delta_util
from tests import _fake_pluggy as fp
from tests import _landing_crash as crash
from tests._fake_pluggy import Reply, account, bill, investment, offset_pages, txn

TODAY = fp.DEFAULT_NOW.date().isoformat()


@pytest.fixture
def ws(tmp_path):
    return tmp_path / "ws"


@pytest.fixture
def raw(ws):
    return fp.make_workspace(ws)


@pytest.fixture
def pluggy(monkeypatch, ws, raw):
    """The fake, installed fail-closed, with one item (item-a) unless a test sets ITEM_IDS."""
    fake = fp.FakePluggy()
    for key, value in fp.FAKE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("ITEM_IDS", "item-a")
    fp.install(monkeypatch.setattr, fake, ws)
    return fake


def one_bank_account(fake, txns=("t-1",)):
    fake.accounts["item-a"] = {"results": [account("acc-1", "BANK")]}
    fake.transactions["acc-1"] = [{"results": [txn(t) for t in txns]}]
    fake.investments["item-a"] = offset_pages([], 500)


def today(raw, entity):
    return pl.read_parquet(raw / entity / f"ingestion_date={TODAY}" / "data.parquet")


def ids(raw, entity):
    return today(raw, entity)["id"].to_list()


def written(raw):
    return sorted({p.parts[-3] for p in fp.partition_files(raw)})


def outcome(ws):
    """Run the script; return the exception it raised, or None."""
    try:
        fp.run_landing(ws)
    except (Exception, SystemExit) as exc:  # noqa: BLE001 -- the test inspects whatever the script raised
        return exc
    return None


# --- the harness itself is closed --------------------------------------------------------------

def test_harness_blocks_real_network_and_unknown_urls(pluggy):
    """Any request the fake does not serve, and any real socket, raises instead of leaving the box."""
    with pytest.raises(fp.UnexpectedCall):
        requests.get("https://example.invalid/", timeout=1)
    with pytest.raises(fp.UnexpectedCall):
        requests.get("https://api.pluggy.ai/connectors", headers={"X-API-KEY": fp.TOKEN}, timeout=1)
    session = requests.Session()
    with pytest.raises(fp.NetworkBlocked):
        session.get("https://api.pluggy.ai/accounts", timeout=1)
    with pytest.raises(fp.NetworkBlocked):
        socket.create_connection(("127.0.0.1", 9))


def test_harness_refuses_writes_outside_the_temp_workspace(pluggy, tmp_path):
    """A parquet write or mkdir outside the test's workspace raises (data/raw is never reachable)."""
    frame = pl.DataFrame({"a": ["1"]})
    with pytest.raises(AssertionError, match="outside the test root"):
        frame.write_parquet(tmp_path / "elsewhere.parquet")
    never = _delta_util.ROOT / "data" / "raw" / "never"
    with pytest.raises(AssertionError, match="outside the test root"):
        never.mkdir(parents=True)


# --- auth ----------------------------------------------------------------------------------

def test_auth_posts_the_env_credentials_once_and_every_get_carries_the_key(pluggy, ws, raw):
    """One POST /auth per run with CLIENT_ID/CLIENT_SECRET; every GET sends its apiKey and a timeout."""
    pluggy.accounts["item-a"] = {"results": [account("acc-1", "BANK"), account("acc-2", "CREDIT")]}
    pluggy.transactions["acc-1"] = [{"results": [txn("t-1")]}]
    pluggy.transactions["acc-2"] = [{"results": [txn("t-2")]}]
    pluggy.bills["acc-2"] = offset_pages([bill("b-1")], 500)
    pluggy.investments["item-a"] = offset_pages([investment("i-1")], 500)
    fp.run_landing(ws)
    posts = [c for c in pluggy.calls if c[0] == "POST"]
    gets = [c for c in pluggy.calls if c[0] == "GET"]
    assert len(posts) == 1
    assert pluggy.auth_body == {"clientId": "test-client", "clientSecret": "test-secret"}
    assert len(gets) == 5
    assert all(h.get("X-API-KEY") == fp.TOKEN for _, _, h, _ in gets)
    assert {t for *_, t in pluggy.calls} == {60}


def test_auth_failure_fails_the_run_and_writes_nothing(pluggy, ws, raw):
    """A refused auth raises (df-ahua.10.1): one POST, no GET, nothing written."""
    pluggy.auth = Reply(401, {"message": "unauthorized"})
    with pytest.raises(RuntimeError, match=r"POST /auth answered HTTP 401"):
        fp.run_landing(ws)
    assert [m for m, *_ in pluggy.calls] == ["POST"]
    assert not (ws / "data").exists()


# --- pagination ----------------------------------------------------------------------------

def test_transactions_follow_the_cursor_through_short_and_empty_pages(pluggy, ws, raw):
    """v2 transactions: follow `next` until null, keep every row in order, across an empty page."""
    pluggy.accounts["item-a"] = {"results": [account("acc-1", "BANK")]}
    pluggy.transactions["acc-1"] = [
        {"results": [txn("t-1"), txn("t-2"), txn("t-3")]},
        {"results": []},
        {"results": [txn("t-4"), txn("t-5"), txn("t-6")]},
        {"results": [txn("t-7")]},
    ]
    pluggy.investments["item-a"] = offset_pages([], 500)
    fp.run_landing(ws)
    assert ids(raw, "transactions") == [f"t-{n}" for n in range(1, 8)]
    urls = pluggy.urls("/v2/transactions")
    assert len(urls) == 4
    assert all("pageSize" not in u for u in urls)


def test_transactions_follow_a_full_url_next(pluggy, ws, raw):
    """A `next` that is a full https://api.pluggy.ai URL is requested as is."""
    nxt = "https://api.pluggy.ai/v2/transactions?accountId=acc-1&cursor=1"
    pluggy.accounts["item-a"] = {"results": [account("acc-1", "BANK")]}
    pluggy.transactions["acc-1"] = [{"results": [txn("t-1")], "next": nxt}, {"results": [txn("t-2")]}]
    pluggy.investments["item-a"] = offset_pages([], 500)
    fp.run_landing(ws)
    assert pluggy.urls("/v2/transactions")[1] == nxt
    assert ids(raw, "transactions") == ["t-1", "t-2"]


@pytest.mark.parametrize("entity", ["bills", "investments"])
def test_page_numbered_endpoints_read_every_page_up_to_total(pluggy, ws, raw, entity):
    """Bills and investments: request page=1,2,... with pageSize=500 until `total` rows are in."""
    rows = [bill(f"x-{n}") if entity == "bills" else investment(f"x-{n}") for n in range(7)]
    pluggy.accounts["item-a"] = {"results": [account("acc-1", "CREDIT")]}
    pluggy.transactions["acc-1"] = [{"results": []}]
    pluggy.bills["acc-1"] = offset_pages(rows if entity == "bills" else [], 3)
    pluggy.investments["item-a"] = offset_pages(rows if entity == "investments" else [], 3)
    fp.run_landing(ws)
    assert ids(raw, entity) == [f"x-{n}" for n in range(7)]
    path = f"/{entity}"
    assert [u.rsplit("page=", 1)[1] for u in pluggy.urls(path)] == ["1", "2", "3"]
    assert all("pageSize=500" in u for u in pluggy.urls(path))


def test_total_pages_is_ignored_and_total_decides(pluggy, ws, raw):
    """Pins: a `totalPages` that lies (says 1 of 3) is ignored; `total` alone drives the loop."""
    one_bank_account(pluggy, txns=())
    pluggy.investments["item-a"] = offset_pages([investment(f"i-{n}") for n in range(5)], 2, totalPages=1)
    fp.run_landing(ws)
    assert ids(raw, "investments") == [f"i-{n}" for n in range(5)]
    assert len(pluggy.urls("/investments")) == 3


def test_total_lower_than_the_rows_drops_the_later_pages(pluggy, ws, raw):
    """Pins (df-ahua.10.2): a `total` below the real count stops early; later pages are never asked."""
    one_bank_account(pluggy, txns=())
    pages = offset_pages([investment(f"i-{n}") for n in range(4)], 2, total=2)
    pluggy.investments["item-a"] = pages
    fp.run_landing(ws)
    assert ids(raw, "investments") == ["i-0", "i-1"]
    assert len(pluggy.urls("/investments")) == 1


def test_missing_total_reads_one_page(pluggy, ws, raw):
    """Pins: a page without `total` counts as total 0, so only page 1 is read."""
    one_bank_account(pluggy, txns=())
    pluggy.investments["item-a"] = [{"results": [investment("i-1")]}, {"results": [investment("i-2")]}]
    fp.run_landing(ws)
    assert ids(raw, "investments") == ["i-1"]


def test_total_higher_than_the_rows_still_ends(pluggy, ws, raw):
    """Pagination ends when pages run empty, even if `total` promises more rows."""
    one_bank_account(pluggy, txns=())
    pluggy.investments["item-a"] = offset_pages([investment("i-1")], 500, total=600)
    assert not isinstance(outcome(ws), fp.RunawayLoop)


def test_an_empty_page_before_total_warns_and_keeps_the_rows_read(pluggy, ws, raw, capsys):
    """df-ahua.10.2: an empty page ends a page-number loop short of `total` with a WARNING line, and
    the rows read so far are landed (raising is for a broken loop, not for a count that may be stale)."""
    one_bank_account(pluggy, txns=())
    pluggy.investments["item-a"] = offset_pages([investment("i-1")], 500, total=600)
    assert outcome(ws) is None
    assert ids(raw, "investments") == ["i-1"]
    assert len(pluggy.urls("/investments")) == 2
    assert "WARNING investments of item item-a: page 2 came back empty with 1 of total=600" in capsys.readouterr().out


def test_total_lower_than_the_rows_warns_when_total_pages_says_more(pluggy, ws, raw, capsys):
    """df-ahua.10.2: the early stop of test_total_lower_than_the_rows_drops_the_later_pages is kept,
    but a `totalPages` past the page it stopped at is reported as a WARNING."""
    one_bank_account(pluggy, txns=())
    pluggy.investments["item-a"] = offset_pages([investment(f"i-{n}") for n in range(4)], 2, total=2)
    fp.run_landing(ws)
    assert ids(raw, "investments") == ["i-0", "i-1"]
    out = capsys.readouterr().out
    assert "WARNING investments of item item-a: Pluggy's counts disagree: 2 rows by page 1, total=2, totalPages=2" in out


def test_an_honest_run_prints_no_warning(pluggy, ws, raw, capsys):
    """Counts that agree with the pages print no WARNING."""
    _full_item(pluggy)
    pluggy.investments["item-a"] = offset_pages([investment(f"i-{n}") for n in range(5)], 2)
    fp.run_landing(ws)
    assert "WARNING" not in capsys.readouterr().out


def _functions(ws):
    """functions.py of the temp workspace, loaded the way the scripts load it."""
    path = ws / "scripts" / "functions" / "functions.py"
    ns = {"__file__": str(path)}
    exec(path.read_text(encoding="utf-8"), ns)
    return ns


@pytest.mark.parametrize("entity", ["bills", "investments"])
def test_the_page_cap_raises_on_page_numbered_endpoints(pluggy, ws, raw, entity):
    """df-ahua.10.2: a page-number loop still short of `total` at PLUGGY_MAX_PAGES raises (the task
    fails and its retry runs) instead of asking forever; no page past the cap is requested."""
    ns = _functions(ws)
    ns["PLUGGY_MAX_PAGES"] = 3
    make = bill if entity == "bills" else investment
    getattr(pluggy, entity)["key-1"] = offset_pages([make(f"x-{n}") for n in range(10)], 1)
    call = ns["list_bills"] if entity == "bills" else ns["list_investments"]
    with pytest.raises(RuntimeError, match=r"3 of total=10 rows after 3 pages"):
        call("key-1")
    assert len(pluggy.urls(f"/{entity}")) == 3


def test_the_page_cap_raises_on_the_cursor_endpoint(pluggy, ws, raw):
    """df-ahua.10.2: a `next` still present after PLUGGY_MAX_PAGES pages raises; no page past the cap."""
    ns = _functions(ws)
    ns["PLUGGY_MAX_PAGES"] = 3
    pluggy.transactions["acc-1"] = [{"results": [txn(f"t-{n}")]} for n in range(10)]
    with pytest.raises(RuntimeError, match=r"still a next cursor after 3 pages"):
        ns["list_transactions"]("acc-1")
    assert len(pluggy.urls("/v2/transactions")) == 3


def test_the_page_cap_is_far_above_a_real_history(ws, raw):
    """The cap must never cut a normal day: at 500 rows a page it allows 500k rows per call."""
    ns = _functions(ws)
    assert ns["PLUGGY_MAX_PAGES"] * ns["PLUGGY_PAGE_SIZE"] >= 500_000


def test_a_repeating_cursor_still_ends(pluggy, ws, raw):
    """Cursor pagination ends when `next` points back at a page already read."""
    pluggy.accounts["item-a"] = {"results": [account("acc-1", "BANK")]}
    pluggy.transactions["acc-1"] = [{"results": [txn("t-1")], "next": "accountId=acc-1&cursor=0"}]
    pluggy.investments["item-a"] = offset_pages([], 500)
    assert not isinstance(outcome(ws), fp.RunawayLoop)


def test_a_repeating_cursor_raises_without_asking_again(pluggy, ws, raw):
    """df-ahua.10.2: a `next` leading to a URL already requested raises before it is sent again, and
    the run writes nothing."""
    pluggy.accounts["item-a"] = {"results": [account("acc-1", "BANK")]}
    pluggy.transactions["acc-1"] = [{"results": [txn("t-1")], "next": "accountId=acc-1&cursor=0"}]
    pluggy.investments["item-a"] = offset_pages([], 500)
    with pytest.raises(RuntimeError, match=r"next cursor repeats at page 3"):
        fp.run_landing(ws)
    urls = pluggy.urls("/v2/transactions")
    assert len(urls) == len(set(urls)) == 2
    assert written(raw) == []


def test_a_next_on_another_host_is_not_followed(pluggy, ws, raw):
    """The API key only goes to api.pluggy.ai: a `next` URL on any other host is not requested."""
    pluggy.accounts["item-a"] = {"results": [account("acc-1", "BANK")]}
    pluggy.transactions["acc-1"] = [{"results": [txn("t-1")], "next": "https://elsewhere.invalid/v2/transactions?c=1"}]
    pluggy.investments["item-a"] = offset_pages([], 500)
    outcome(ws)
    assert {u.split("/")[2] for _, u, _, _ in pluggy.calls} == {fp.HOST}


@pytest.mark.parametrize("nxt", [
    "https://elsewhere.invalid/v2/transactions?accountId=acc-1&cursor=1",
    "http://api.pluggy.ai/v2/transactions?accountId=acc-1&cursor=1",
    "https://api.pluggy.ai.elsewhere.invalid/v2/transactions?accountId=acc-1&cursor=1",
    "https://api.pluggy.ai:8443/v2/transactions?accountId=acc-1&cursor=1",
    "https://someone@api.pluggy.ai/v2/transactions?accountId=acc-1&cursor=1",
    "https://api.pluggy.ai@elsewhere.invalid/v2/transactions?accountId=acc-1&cursor=1",
])
def test_a_next_url_that_is_not_https_pluggy_raises_unsent(pluggy, ws, raw, nxt):
    """df-ahua.10.4: a full-URL `next` is followed only when it is https on exactly api.pluggy.ai;
    anything else raises before the request (the key stays home) and the run writes nothing."""
    pluggy.accounts["item-a"] = {"results": [account("acc-1", "BANK")]}
    pluggy.transactions["acc-1"] = [{"results": [txn("t-1")], "next": nxt}, {"results": [txn("t-2")]}]
    pluggy.investments["item-a"] = offset_pages([], 500)
    with pytest.raises(RuntimeError, match=r"refusing a next URL that is not https://api\.pluggy\.ai") as exc:
        fp.run_landing(ws)
    assert fp.TOKEN not in str(exc.value)
    assert pluggy.urls("/v2/transactions") == ["https://api.pluggy.ai/v2/transactions?accountId=acc-1"]
    assert written(raw) == []


def test_pagination_property_every_row_once_in_order(pluggy, ws, raw):
    """For any row count and page size with an honest `total`, list_bills returns each row once, in order."""
    ns = {"__file__": str(ws / "scripts" / "functions" / "functions.py")}
    exec((ws / "scripts" / "functions" / "functions.py").read_text(encoding="utf-8"), ns)

    @settings(max_examples=60, deadline=None, database=None)   # no example DB: writes are confined to ws
    @given(n=st.integers(0, 25), size=st.integers(1, 8))
    def check(n, size):
        rows = [bill(f"b-{i}") for i in range(n)]
        pluggy.bills["acc-p"] = offset_pages(rows, size)
        pluggy.calls.clear()
        got = ns["list_bills"]("acc-p")
        assert [r["id"] for r in got["results"]] == [r["id"] for r in rows]
        assert len(pluggy.urls("/bills")) == max(1, -(-n // size))

    check()


# --- HTTP errors and retry -----------------------------------------------------------------

@pytest.mark.parametrize("status", [401, 404, 429, 500, 503])
def test_an_http_error_fails_the_run_without_retry_and_writes_nothing(pluggy, ws, raw, status):
    """df-ahua.10.1: a non-200 on one account's transactions raises at once (no retry inside the
    script: the engine's task retry reruns it), later accounts are not asked, nothing is written."""
    pluggy.accounts["item-a"] = {"results": [account("acc-bad", "CREDIT"), account("acc-ok", "BANK")]}
    pluggy.transactions["acc-bad"] = [Reply(status, {"message": "nope"})]
    pluggy.transactions["acc-ok"] = [{"results": [txn("t-ok")]}]
    pluggy.bills["acc-bad"] = offset_pages([bill("b-1")], 500)
    pluggy.investments["item-a"] = offset_pages([], 500)
    with pytest.raises(RuntimeError, match=rf"GET /v2/transactions \(page 1\) answered HTTP {status}"):
        fp.run_landing(ws)
    assert pluggy.urls("/v2/transactions") == ["https://api.pluggy.ai/v2/transactions?accountId=acc-bad"]
    assert pluggy.urls("/bills") == []
    assert written(raw) == []


def test_an_error_on_a_later_page_drops_the_pages_already_read(pluggy, ws, raw):
    """df-ahua.10.1: page 2 failing raises; page 1 of the same account and the accounts are not written."""
    pluggy.accounts["item-a"] = {"results": [account("acc-1", "BANK")]}
    pluggy.transactions["acc-1"] = [{"results": [txn("t-1")]}, Reply(500)]
    pluggy.investments["item-a"] = offset_pages([], 500)
    with pytest.raises(RuntimeError, match=r"GET /v2/transactions \(page 2\) answered HTTP 500"):
        fp.run_landing(ws)
    assert written(raw) == []


@pytest.mark.parametrize("error", [requests.exceptions.Timeout("read timed out"),
                                   requests.exceptions.ConnectionError("name resolution failed")])
def test_a_timeout_or_connection_error_aborts_the_run_before_any_write(pluggy, ws, raw, error):
    """Pins: a raised requests error is not caught; the run stops (so the task's retry runs) and,
    since every write comes after every fetch, no partition is written."""
    pluggy.accounts["item-a"] = {"results": [account("acc-1", "BANK"), account("acc-2", "BANK")]}
    pluggy.transactions["acc-1"] = [{"results": [txn("t-1")]}]
    pluggy.transactions["acc-2"] = [error]
    with pytest.raises(type(error)):
        fp.run_landing(ws)
    assert written(raw) == []


def test_a_failed_accounts_call_fails_the_run_before_the_next_item(pluggy, ws, raw, monkeypatch):
    """df-ahua.10.1: a failed accounts call raises; the item after it is not asked, nothing is written."""
    monkeypatch.setenv("ITEM_IDS", "item-a,item-b")
    pluggy.accounts["item-a"] = Reply(500)
    pluggy.accounts["item-b"] = {"results": [account("acc-b", "BANK", item_id="item-b")]}
    pluggy.transactions["acc-b"] = [{"results": [txn("t-b")]}]
    pluggy.investments["item-b"] = offset_pages([investment("i-b")], 500)
    with pytest.raises(RuntimeError, match=r"GET /accounts answered HTTP 500"):
        fp.run_landing(ws)
    assert pluggy.urls() == ["https://api.pluggy.ai/auth", "https://api.pluggy.ai/accounts?itemId=item-a"]
    assert written(raw) == []


def test_a_failed_investments_call_fails_the_run_and_writes_nothing(pluggy, ws, raw):
    """df-ahua.10.1: investments failing raises; the accounts and transactions already fetched are not written."""
    one_bank_account(pluggy)
    pluggy.investments["item-a"] = [Reply(500)]
    with pytest.raises(RuntimeError, match=r"GET /investments \(page 1\) answered HTTP 500"):
        fp.run_landing(ws)
    assert written(raw) == []


STAGES = ["auth", "accounts", "transactions", "bills", "investments"]


def _fail_at(fake, where, failing):
    if where == "auth":
        fake.auth = failing
    elif where == "accounts":
        fake.accounts["item-a"] = failing
    else:
        getattr(fake, where)["item-a" if where == "investments" else "acc-1"] = [failing]


@pytest.mark.parametrize("where", STAGES)
def test_a_failed_pluggy_call_fails_the_task(pluggy, ws, raw, where):
    """A Pluggy call that fails makes the fetch task fail, so its retry runs and the run is not 'success'."""
    pluggy.accounts["item-a"] = {"results": [account("acc-1", "CREDIT")]}
    pluggy.transactions["acc-1"] = [{"results": [txn("t-1")]}]
    pluggy.bills["acc-1"] = offset_pages([bill("b-1")], 500)
    pluggy.investments["item-a"] = offset_pages([investment("i-1")], 500)
    _fail_at(pluggy, where, Reply(500, {"message": "nope"}))
    exc = outcome(ws)
    assert exc is not None
    assert not (isinstance(exc, SystemExit) and exc.code in (0, None))


@pytest.mark.parametrize("where", STAGES)
def test_a_failed_call_names_route_and_status_only(pluggy, ws, raw, where):
    """df-ahua.10.1: the error names the route and the status; never the key, never the body."""
    pluggy.accounts["item-a"] = {"results": [account("acc-1", "CREDIT")]}
    pluggy.transactions["acc-1"] = [{"results": [txn("t-1")]}]
    pluggy.bills["acc-1"] = offset_pages([bill("b-1")], 500)
    pluggy.investments["item-a"] = offset_pages([investment("i-1")], 500)
    _fail_at(pluggy, where, Reply(503, {"message": "body-with-private-detail"}))
    route = {"auth": "POST /auth", "accounts": "GET /accounts", "transactions": "GET /v2/transactions (page 1)",
             "bills": "GET /bills (page 1)", "investments": "GET /investments (page 1)"}[where]
    with pytest.raises(RuntimeError) as exc:
        fp.run_landing(ws)
    assert str(exc.value) == f"Pluggy {route} answered HTTP 503"


@pytest.mark.parametrize("where", STAGES)
def test_a_failed_run_leaves_every_partition_byte_identical(pluggy, ws, raw, where):
    """df-ahua.10.1 with a same-day earlier run on disk: a run that fails at any Pluggy call writes
    nothing, so earlier days and today's earlier partitions of every entity stay byte for byte (a
    failing retry can never shrink today's data)."""
    _older_state(raw)
    for entity, row in (("accounts", account("acc-earlier")), ("transactions", txn("t-earlier")),
                        ("bills", bill("b-earlier")), ("investments", investment("i-earlier"))):
        fp.write_partition(raw, entity, TODAY, [{**row, "ingested_at": TODAY}])
    before = fp.hashes(raw)
    pluggy.accounts["item-a"] = {"results": [account("acc-1", "CREDIT")]}
    pluggy.transactions["acc-1"] = [{"results": [txn("t-1")]}]
    pluggy.bills["acc-1"] = offset_pages([bill("b-1")], 500)
    pluggy.investments["item-a"] = offset_pages([investment("i-1")], 500)
    _fail_at(pluggy, where, Reply(500))
    with pytest.raises(RuntimeError):
        fp.run_landing(ws)
    assert fp.hashes(raw) == before


def test_failed_transactions_fail_the_run_before_the_bills(pluggy, ws, raw):
    """df-ahua.10.3, settled by df-ahua.10.1: a CREDIT account whose transactions call fails no longer
    lands a day without its bills; the run raises before /bills is asked and writes nothing."""
    pluggy.accounts["item-a"] = {"results": [account("acc-1", "CREDIT")]}
    pluggy.transactions["acc-1"] = [Reply(500)]
    pluggy.bills["acc-1"] = offset_pages([bill("b-1")], 500)
    pluggy.investments["item-a"] = offset_pages([], 500)
    with pytest.raises(RuntimeError, match="answered HTTP 500"):
        fp.run_landing(ws)
    assert pluggy.urls("/bills") == []
    assert written(raw) == []


# --- items and accounts --------------------------------------------------------------------

def test_an_item_with_no_accounts_still_lands_its_investments(pluggy, ws, raw):
    """An item whose accounts list is empty writes no accounts/transactions/bills, but its investments."""
    pluggy.accounts["item-a"] = {"results": [], "total": 0}
    pluggy.investments["item-a"] = offset_pages([investment("i-1")], 500)
    fp.run_landing(ws)
    assert written(raw) == ["investments"]


def test_nothing_fetched_writes_no_partition(pluggy, ws, raw):
    """An entity with no rows writes no partition at all (no empty file)."""
    pluggy.accounts["item-a"] = {"results": []}
    pluggy.investments["item-a"] = offset_pages([], 500)
    fp.run_landing(ws)
    assert written(raw) == []


def test_bills_are_fetched_for_credit_accounts_only(pluggy, ws, raw):
    """Only type CREDIT gets a /bills call (not BANK, not an unknown or missing type); each bill
    carries its account's id and name."""
    no_type = account("acc-x", "BANK")
    del no_type["type"]
    pluggy.accounts["item-a"] = {"results": [account("acc-bank", "BANK"), account("acc-card", "CREDIT"),
                                             account("acc-odd", "UNKNOWN"), no_type]}
    for acc in ("acc-bank", "acc-card", "acc-odd", "acc-x"):
        pluggy.transactions[acc] = [{"results": []}]
    pluggy.bills["acc-card"] = offset_pages([bill("b-1"), bill("b-2")], 500)
    pluggy.investments["item-a"] = offset_pages([], 500)
    fp.run_landing(ws)
    assert pluggy.urls("/bills") == ["https://api.pluggy.ai/bills?accountId=acc-card&pageSize=500&page=1"]
    bills = today(raw, "bills")
    assert bills["accountId"].to_list() == ["acc-card", "acc-card"]
    assert bills["accountName"].to_list() == ["Conta acc-card"] * 2


def test_every_item_in_item_ids_lands_in_one_partition(pluggy, ws, raw, monkeypatch):
    """ITEM_IDS (comma list, blanks ignored) are fetched in order into one partition per entity;
    investments carry their itemId."""
    monkeypatch.setenv("ITEM_IDS", " item-a , ,item-b,")
    for item in ("item-a", "item-b"):
        pluggy.accounts[item] = {"results": [account(f"acc-{item}", "BANK", item_id=item)]}
        pluggy.transactions[f"acc-{item}"] = [{"results": [txn(f"t-{item}")]}]
        pluggy.investments[item] = offset_pages([investment(f"i-{item}")], 500)
    fp.run_landing(ws)
    assert pluggy.urls("/accounts") == [f"https://api.pluggy.ai/accounts?itemId={i}" for i in ("item-a", "item-b")]
    assert ids(raw, "accounts") == ["acc-item-a", "acc-item-b"]
    assert ids(raw, "transactions") == ["t-item-a", "t-item-b"]
    assert today(raw, "investments")["itemId"].to_list() == ["item-a", "item-b"]
    assert len(fp.partition_files(raw)) == 3


def test_empty_item_ids_calls_nothing(pluggy, ws, raw, monkeypatch):
    """Pins: no ITEM_IDS means no Pluggy call and no partition (and no error)."""
    monkeypatch.setenv("ITEM_IDS", "")
    assert outcome(ws) is None
    assert pluggy.calls == []
    assert written(raw) == []


# --- payload shape -------------------------------------------------------------------------

def test_columns_are_the_union_of_every_record_in_first_seen_order(pluggy, ws, raw):
    """Fields that differ between pages or accounts: the partition has every field ever seen, in
    first-seen order, null where a record lacks it; an extra field is kept."""
    pluggy.accounts["item-a"] = {"results": [account("acc-1", "BANK"), account("acc-2", "BANK")]}
    first = {"id": "t-1", "amount": -1.0, "date": "2030-01-01"}
    second = {"id": "t-2", "date": "2030-01-02", "brandNewField": "x"}
    third = {"id": "t-3", "amount": 2.5}
    pluggy.transactions["acc-1"] = [{"results": [first]}, {"results": [second]}]
    pluggy.transactions["acc-2"] = [{"results": [third]}]
    pluggy.investments["item-a"] = offset_pages([], 500)
    fp.run_landing(ws)
    df = today(raw, "transactions")
    assert df.columns == ["id", "amount", "date", "accountId", "accountName", "ingested_at", "brandNewField"]
    assert df.rows() == [
        ("t-1", "-1.0", "2030-01-01", "acc-1", "Conta acc-1", TODAY, None),
        ("t-2", None, "2030-01-02", "acc-1", "Conta acc-1", TODAY, "x"),
        ("t-3", "2.5", None, "acc-2", "Conta acc-2", TODAY, None),
    ]


def test_every_value_is_stored_as_python_str(pluggy, ws, raw):
    """Every column is Utf8 holding str(value): True as 'True', 0 as '0', a nested object as its
    Python repr (raw2cleansed reads it back with ast.literal_eval), JSON null as null."""
    merchant = {"name": "Loja Ficticia", "cnpj": None, "category": "x"}
    pluggy.accounts["item-a"] = {"results": [account("acc-1", "BANK", name=None)]}
    pluggy.transactions["acc-1"] = [{"results": [txn("t-1", amount=0, merchant=merchant,
                                                     creditCardMetadata={"installmentNumber": 2},
                                                     tags=["a", "b"], isPending=False)]}]
    pluggy.investments["item-a"] = offset_pages([], 500)
    fp.run_landing(ws)
    df = today(raw, "transactions")
    assert set(df.schema.values()) == {pl.Utf8}
    row = df.row(0, named=True)
    assert row["amount"] == "0"
    assert row["isPending"] == "False"
    assert row["descriptionRaw"] is None
    assert ast.literal_eval(row["merchant"]) == merchant
    assert ast.literal_eval(row["tags"]) == ["a", "b"]
    assert row["accountName"] is None   # name present but null: account.get("name", "") gives None


def test_an_account_without_a_name_tags_its_rows_with_an_empty_name(pluggy, ws, raw):
    """Pins: accountName is "" when the account has no name field."""
    acc = account("acc-1", "BANK")
    del acc["name"]
    pluggy.accounts["item-a"] = {"results": [acc]}
    pluggy.transactions["acc-1"] = [{"results": [txn("t-1")]}]
    pluggy.investments["item-a"] = offset_pages([], 500)
    fp.run_landing(ws)
    assert today(raw, "transactions")["accountName"].to_list() == [""]


def test_the_tags_the_script_adds_win_over_the_payload(pluggy, ws, raw):
    """Pins: accountId/accountName (transactions, bills), itemId (investments) and ingested_at
    are set by the script and overwrite a payload field of the same name."""
    pluggy.accounts["item-a"] = {"results": [account("acc-1", "CREDIT", ingested_at="1999-01-01")]}
    pluggy.transactions["acc-1"] = [{"results": [txn("t-1", accountId="other", ingested_at="x")]}]
    pluggy.bills["acc-1"] = offset_pages([bill("b-1", accountId="other")], 500)
    pluggy.investments["item-a"] = offset_pages([investment("i-1", itemId="other")], 500)
    fp.run_landing(ws)
    assert today(raw, "transactions")["accountId"].to_list() == ["acc-1"]
    assert today(raw, "bills")["accountId"].to_list() == ["acc-1"]
    assert today(raw, "investments")["itemId"].to_list() == ["item-a"]
    for entity in fp.ENTITIES:
        assert today(raw, entity)["ingested_at"].to_list() == [TODAY]


def test_duplicate_ids_across_pages_are_all_kept(pluggy, ws, raw):
    """Pins: raw does not dedupe; the same id on two pages lands twice (latest_by_key dedupes later)."""
    pluggy.accounts["item-a"] = {"results": [account("acc-1", "BANK")]}
    pluggy.transactions["acc-1"] = [{"results": [txn("t-1")]}, {"results": [txn("t-1")]}]
    pluggy.investments["item-a"] = offset_pages([], 500)
    fp.run_landing(ws)
    assert ids(raw, "transactions") == ["t-1", "t-1"]


CELL = st.none() | st.booleans() | st.integers() | st.text(max_size=5) | st.floats(allow_nan=False) \
    | st.lists(st.integers(), max_size=2)
RECORDS = st.lists(st.dictionaries(st.sampled_from(["id", "a", "b", "c"]), CELL), min_size=1, max_size=6)


def test_write_partition_property_union_columns_and_str_cells(pluggy, ws, raw):
    """For any records, the partition has the union of keys in first-seen order and each cell is
    str(value), or null where the value is None or the key is absent."""
    path = ws / "scripts" / "functions" / "functions.py"
    ns = {"__file__": str(path)}
    exec(path.read_text(encoding="utf-8"), ns)

    @settings(max_examples=40, deadline=None, database=None)   # no example DB: writes are confined to ws
    @given(records=RECORDS)
    def check(records):
        records = [{**r, "ingested_at": TODAY} for r in records]
        out = ns["write_parquet_partition"]("raw", "transactions", records, fp.DEFAULT_NOW.date())
        df = pl.read_parquet(out)
        keys = list(dict.fromkeys(k for r in records for k in r))
        assert df.columns == keys
        assert df.rows() == [tuple(None if r.get(k) is None else str(r[k]) for k in keys) for r in records]

    check()


# --- the partition rule --------------------------------------------------------------------

def _older_state(raw):
    for entity, row in (("accounts", account("acc-old")), ("transactions", txn("t-old")),
                        ("bills", bill("b-old")), ("investments", investment("i-old"))):
        for day in ("2030-01-13", "2030-01-14"):
            fp.write_partition(raw, entity, day, [{**row, "ingested_at": day}])
    (raw / "README.keep").write_text("not a partition\n", encoding="utf-8")


def _full_item(fake):
    fake.accounts["item-a"] = {"results": [account("acc-1", "BANK"), account("acc-2", "CREDIT")]}
    fake.transactions["acc-1"] = [{"results": [txn("t-1")]}]
    fake.transactions["acc-2"] = [{"results": [txn("t-2", 5.0)]}]
    fake.bills["acc-2"] = offset_pages([bill("b-1")], 500)
    fake.investments["item-a"] = offset_pages([investment("i-1")], 500)


def test_a_run_adds_only_its_own_partition_and_leaves_the_rest_byte_identical(pluggy, ws, raw):
    """A run creates <entity>/ingestion_date=<today>/data.parquet and nothing else; every file that
    was there is byte for byte the same."""
    _older_state(raw)
    before = fp.hashes(raw)
    _full_item(pluggy)
    fp.run_landing(ws)
    after = fp.hashes(raw)
    assert {k: v for k, v in after.items() if k in before} == before
    assert sorted(set(after) - set(before)) == [f"{e}/ingestion_date={TODAY}/data.parquet" for e in sorted(fp.ENTITIES)]


def test_ingested_at_is_the_utc_day_on_every_row_and_names_the_partition(pluggy, ws, raw):
    """Every row of every entity has ingested_at = the UTC date of the run, which also names the
    partition: 21:30 in Brasília (UTC-3) on the 15th lands as the 16th."""
    _full_item(pluggy)
    late_evening_brt = datetime(2030, 1, 16, 0, 30, tzinfo=UTC)
    fp.run_landing(ws, now=late_evening_brt)
    for entity in fp.ENTITIES:
        (part,) = fp.partition_files(raw, entity)
        assert part.parent.name == "ingestion_date=2030-01-16"
        assert pl.read_parquet(part)["ingested_at"].to_list() == ["2030-01-16"] * pl.read_parquet(part).height


def test_a_second_run_the_same_day_replaces_the_partition(pluggy, ws, raw):
    """Pins (df-fs58): a second run on the same UTC day rewrites today's files with its own rows;
    an entity it fetched nothing for keeps the first run's file."""
    _full_item(pluggy)
    fp.run_landing(ws)
    pluggy.transactions["acc-1"] = [{"results": [txn("t-3")]}]
    pluggy.bills["acc-2"] = offset_pages([], 500)   # no bills today (a 500 now fails the run, df-ahua.10.1)
    fp.run_landing(ws, now=fp.DEFAULT_NOW.replace(hour=20))
    assert ids(raw, "transactions") == ["t-3", "t-2"]
    assert ids(raw, "bills") == ["b-1"]
    assert len(fp.partition_files(raw, "transactions")) == 1


@pytest.mark.xfail(strict=True, reason="df-fs58: a same-day rerun overwrites today's raw partition with less")
def test_a_same_day_rerun_never_loses_rows_of_the_first_run(pluggy, ws, raw):
    """A second run on the same day keeps every row the first run landed, even when it fetches less."""
    _full_item(pluggy)
    fp.run_landing(ws)
    pluggy.transactions["acc-1"] = [{"results": []}]   # fewer rows on a 200 (a 500 now fails the run, df-ahua.10.1)
    fp.run_landing(ws, now=fp.DEFAULT_NOW.replace(hour=20))
    landed = pl.concat([pl.read_parquet(p) for p in fp.partition_files(raw, "transactions")], how="diagonal_relaxed")
    assert {"t-1", "t-2"} <= set(landed["id"].to_list())


# --- a failure in the middle -----------------------------------------------------------------

def _fail_write_of(monkeypatch, entity, torn=False):
    """The parquet write of `entity` fails (disk full); torn: after half the file is on disk."""
    write = pl.DataFrame.write_parquet

    def failing(self, file, *args, **kwargs):
        if f"/{entity}/" in str(file).replace("\\", "/"):
            if torn:
                write(self, file, *args, **kwargs)
                with open(file, "r+b") as f:
                    f.truncate(f.seek(0, 2) // 2)
            raise OSError(28, "No space left on device")
        return write(self, file, *args, **kwargs)

    monkeypatch.setattr(pl.DataFrame, "write_parquet", failing)


def _readable(raw):
    bad = []
    for part in fp.partition_files(raw):
        try:
            pl.read_parquet(part)
        except Exception:  # noqa: BLE001 -- any read error means raw2cleansed would fail on it
            bad.append(part.relative_to(raw).as_posix())
    return bad


def test_a_failed_write_of_entity_2_of_4_leaves_entity_1_and_nothing_else(pluggy, ws, raw, monkeypatch):
    """Pins: writes go accounts, transactions, bills, investments; the transactions write failing
    raises, accounts is already on disk, the others are not written, older partitions are intact."""
    _older_state(raw)
    before = fp.hashes(raw)
    _full_item(pluggy)
    _fail_write_of(monkeypatch, "transactions")
    with pytest.raises(OSError, match="No space"):
        fp.run_landing(ws)
    after = fp.hashes(raw)
    assert {k: v for k, v in after.items() if k in before} == before
    assert sorted(set(after) - set(before)) == [f"accounts/ingestion_date={TODAY}/data.parquet"]
    assert list((raw / "transactions" / f"ingestion_date={TODAY}").iterdir()) == []   # its dir was made
    assert _readable(raw) == []


def test_a_write_that_dies_half_way_leaves_no_partial_partition_visible(pluggy, ws, raw, monkeypatch):
    """A write that fails half way leaves no unreadable data.parquet where raw2cleansed globs."""
    _full_item(pluggy)
    _fail_write_of(monkeypatch, "transactions", torn=True)
    with pytest.raises(OSError):
        fp.run_landing(ws)
    assert _readable(raw) == []


def test_a_write_that_dies_half_way_keeps_todays_earlier_file(pluggy, ws, raw, monkeypatch):
    """df-ahua.10.5: a same-day rerun whose write fails half way leaves the earlier run's data.parquet
    byte for byte (it used to be truncated before the write)."""
    earlier = fp.write_partition(raw, "transactions", TODAY, [{**txn("t-earlier"), "ingested_at": TODAY}])
    before = earlier.read_bytes()
    _full_item(pluggy)
    _fail_write_of(monkeypatch, "transactions", torn=True)
    with pytest.raises(OSError):
        fp.run_landing(ws)
    assert earlier.read_bytes() == before


TEMP = ".data.parquet.tmp"


def test_the_temp_name_is_the_one_functions_py_writes(ws, raw):
    """The tests below hold the temp name functions.py uses."""
    assert _functions(ws)["RAW_PARTITION_TEMP"] == TEMP


def test_a_leftover_temp_from_a_crash_is_harmless_and_overwritten(pluggy, ws, raw):
    """df-ahua.10.5: a torn temp file a crashed run left beside today's data.parquet is invisible to the
    readers, and the next write of that partition replaces it; one left in an older day stays as it
    was (a run never touches another day's partition) and is just as invisible."""
    _older_state(raw)
    older_left = raw / "transactions" / "ingestion_date=2030-01-14" / TEMP
    older_left.write_bytes(b"PAR1 torn")
    today_dir = raw / "transactions" / f"ingestion_date={TODAY}"
    today_dir.mkdir(parents=True)
    (today_dir / TEMP).write_bytes(b"PAR1 torn")
    assert _readable(raw) == []
    _full_item(pluggy)
    fp.run_landing(ws)
    assert sorted(p.name for p in today_dir.iterdir()) == ["data.parquet"]
    assert ids(raw, "transactions") == ["t-1", "t-2"]
    assert older_left.read_bytes() == b"PAR1 torn"


def _glob_patterns(path):
    """(path, pattern) for every .glob/.rglob/.iglob call in a script: a literal pattern as written, or
    the literal parts of a `str(root / "a" / "b")` assigned to the name passed (a part that is not a
    literal becomes None, so the test can say it could not read it)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assigned = {t.id: node.value for node in ast.walk(tree) if isinstance(node, ast.Assign)
                for t in node.targets if isinstance(t, ast.Name)}

    def literal(node):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "str" and len(node.args) == 1:
            return literal(node.args[0])
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            left = literal(node.left)
            right = literal(node.right)
            return right if left in (None, "") else (None if right is None else f"{left}/{right}")
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name) and node.id in assigned:
            return literal(assigned[node.id])
        return None

    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("glob", "rglob", "iglob") and node.args):
            yield node.func.attr, literal(node.args[0])


def test_no_glob_in_the_scripts_sees_the_temp_file():
    """df-ahua.10.5: every glob in scripts/, read from the source: the readers of raw
    (`<entity>/ingestion_date=*/data.parquet`) match a partition's data.parquet and not its temp
    file; the one glob that matches any file is the backup's rglob("*"), which skips the temp by
    name (test_backup.py). DataForge's leak guard reads raw too (scripts/leakguard_values.py,
    tests/test_leakguard_values.py)."""
    found = {}
    for script in sorted(_delta_util.SCRIPTS.rglob("*.py")):
        for kind, pattern in _glob_patterns(script):
            found.setdefault(script.relative_to(_delta_util.ROOT).as_posix(), set()).add((kind, pattern))
    assert all(pattern is not None for pats in found.values() for _, pattern in pats), found
    readers = {(f, pattern) for f, pats in found.items() for _, pattern in pats if pattern.endswith("data.parquet")}
    assert {f for f, _ in readers} >= {"scripts/raw2cleansed/raw2cleansed.py", "scripts/raw2cleansed/raw2cleansed_accounts.py",
                                       "scripts/raw2cleansed/raw2cleansed_bills.py",
                                       "scripts/raw2cleansed/raw2cleansed_investments.py",
                                       "scripts/cleansed2curated/fact_balance_snapshot.py"}
    entities = "|".join(fp.ENTITIES)
    for f, pattern in readers:
        entity = pattern.split("/")[0] if pattern.split("/")[0] in fp.ENTITIES else "transactions"
        assert re.fullmatch(rf"(({entities})/)?(ingestion_date=)?\*/data\.parquet", pattern), (f, pattern)
        part = PurePosixPath(entity, f"ingestion_date={TODAY}")
        assert part.joinpath("data.parquet").match(pattern), (f, pattern)
        assert not part.joinpath(TEMP).match(pattern), (f, pattern)
    seeing_temp = {(f, kind, pattern) for f, pats in found.items() for kind, pattern in pats
                   if PurePosixPath("transactions", f"ingestion_date={TODAY}", TEMP).match(pattern)}
    assert seeing_temp == {("scripts/backup/backup_dados.py", "rglob", "*")}


# --- crash sweep: a real process death at every write point -----------------------------------

def _child(workdir, k, how):
    cmd = [sys.executable, "-m", "tests._landing_crash", str(workdir), str(k), how]
    return subprocess.run(cmd, cwd=_delta_util.ROOT, capture_output=True, text=True, timeout=180, check=False,
                          env={**os.environ, "PYTHONPATH": str(_delta_util.ROOT),
                               "PYTHONDONTWRITEBYTECODE": "1"})


@pytest.fixture(scope="module")
def sweep(tmp_path_factory):
    """Kill the scenario before, half way through (torn) and after each write point, one child each.
    Returns (start hashes, the dry run's end state, [(step, workdir)])."""
    def fresh(name):
        workdir = tmp_path_factory.mktemp(name)
        crash.start_state(workdir)
        return workdir

    dry_dir = fresh("dry")
    start = fp.hashes(dry_dir / "data" / "raw")
    dry = _child(dry_dir, 0, "before")
    assert dry.returncode == 0, dry.stdout + dry.stderr
    seen = [line[len(crash.REACHED):] for line in dry.stdout.splitlines() if line.startswith(crash.REACHED)]
    assert seen.count(crash.TORN) == 4, seen
    steps = [(k, point, how) for k, point in enumerate(seen, start=1)
             for how in ("before", "after", "torn") if how == "before" or point == crash.TORN]
    workdirs = [fresh(f"k{k}-{how}") for k, _, how in steps]
    with ThreadPoolExecutor(max_workers=4) as pool:
        deaths = list(pool.map(lambda s, w: _child(w, s[0], s[2]), steps, workdirs))
    for (k, point, how), died in zip(steps, deaths, strict=True):
        assert died.returncode == crash.CRASH_CODE, f"k={k} {how}: {died.stdout}{died.stderr}"
        assert f"{crash.DIED}{k} {point}" in died.stdout
    return start, dry_dir / "data" / "raw", list(zip(steps, workdirs, strict=True))


def test_crash_sweep_never_touches_older_partitions(sweep):
    """Whenever the run dies, every partition of an earlier day is byte for byte what it was."""
    start, _, runs = sweep
    older = {k: v for k, v in start.items() if "2030-01-14" in k}
    for step, workdir in runs:
        now = fp.hashes(workdir / "data" / "raw")
        assert {k: now.get(k) for k in older} == older, step


def test_crash_sweep_leaves_each_partition_whole(sweep):
    """Whenever the run dies, every data.parquet raw2cleansed can see is readable, and each of today's
    is either the earlier run's file untouched or this run's complete file."""
    start, dry_raw, runs = sweep
    for step, workdir in runs:
        wraw = workdir / "data" / "raw"
        assert _readable(wraw) == [], step
        for part in fp.partition_files(wraw):
            rel = part.relative_to(wraw).as_posix()
            if TODAY in rel:
                untouched = fp.hashes(wraw).get(rel) == start.get(rel)
                assert untouched or crash.read_ids(part) == crash.read_ids(dry_raw / rel), (step, rel)


def test_crash_sweep_reaches_the_publish_step(sweep):
    """The sweep kills before each os.replace too: the moment a whole temp file waits to be published."""
    _, _, runs = sweep
    assert sum(1 for (_, point, how), _ in runs if point == crash.REPLACE and how == "before") == 4


def test_crash_sweep_never_loses_todays_earlier_partition(sweep):
    """df-ahua.10.5, the same-day rerun case: whenever the run dies, today's partitions an earlier run
    wrote (accounts, transactions) are still there, whole: the earlier file or this run's."""
    start, dry_raw, runs = sweep
    earlier = [k for k in start if TODAY in k]
    assert sorted(earlier) == [f"{e}/ingestion_date={TODAY}/data.parquet" for e in ("accounts", "transactions")]
    for step, workdir in runs:
        wraw = workdir / "data" / "raw"
        for rel in earlier:
            assert (wraw / rel).exists(), (step, rel)
            same = fp.hashes(wraw)[rel] in (start[rel], fp.hashes(dry_raw)[rel])
            assert same, (step, rel)


def test_a_rerun_after_any_crash_ends_as_a_run_that_never_crashed(sweep, tmp_path):
    """After a crash at any write point, the next run (the task retry) leaves raw exactly as a run that
    never crashed: same files, same bytes, no temp file left behind."""
    _, dry_raw, runs = sweep
    expected = fp.hashes(dry_raw)
    copies = []
    for n, (step, workdir) in enumerate(runs):
        copy = tmp_path / f"rerun-{n}"
        shutil.copytree(workdir, copy)
        copies.append((step, copy))
    with ThreadPoolExecutor(max_workers=4) as pool:
        reruns = list(pool.map(lambda c: _child(c[1], 0, "before"), copies))
    for (step, copy), rerun in zip(copies, reruns, strict=True):
        assert rerun.returncode == 0, (step, rerun.stdout + rerun.stderr)
        assert fp.hashes(copy / "data" / "raw") == expected, step
