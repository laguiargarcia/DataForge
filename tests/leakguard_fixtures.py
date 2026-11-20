"""Made-up values, scratch repositories and workspaces shared by the leak guard's tests (df-n8ac.36).

Every PII-shaped or "real" value is made up and assembled at run time, so this file never holds
one and the guard passes over it like any other committed file. Valid CPFs and CNPJs come from
the check-digit rule, written apart from the guard's own. `repo` and `workspace` are plain functions here: each test file makes them its
own fixtures (`repo = pytest.fixture(leakguard_fixtures.repo)`).
"""

import json
import os
import re
import sqlite3
import subprocess

import polars as pl

from scripts import leakguard, leakguard_checkout, leakguard_patterns

ACCT = "1234" + "-5"
EMAIL = "someone" + "@" + "gmail.com"
OWNER = "Fulana Exemplo" + " da Silva"
NUMBER = "98765" + "-4"
NUMBER_BARE = NUMBER.replace("-", "")   # the real number written without its dash: no ACCT shape
TRANSFER = "901/0001/" + NUMBER
RECEIVER = "Beltrano" + " Recebedor"
SECRET = "s3cr3t" + "-value"


def _with_check_digits(base, cycle):
    """`base` and its two check digits: each is (10 x sum) mod 11 mod 10, the sum weighting the digits
    before it 2, 3, 4... from the right, the weight going back to 2 after `cycle`. Written apart from the
    guard's own rule (scripts/leakguard_patterns.py), so the tests check that rule against another."""
    digits = base
    for _ in range(2):
        total = sum(int(d) * (2 + i % (cycle - 1)) for i, d in enumerate(reversed(digits)))
        digits += str(total * 10 % 11 % 10)
    return digits


def _cpf(base9):
    return _with_check_digits(base9, 11)   # weights 2..11: never wrap over the 10 digits of a CPF


def _cnpj(base12):
    return _with_check_digits(base12, 9)   # weights 2..9, then 2 again


CPF = _cpf("529982247")
CPF_PUNCT = f"{CPF[:3]}.{CPF[3:6]}.{CPF[6:9]}-{CPF[9:]}"
CNPJ = _cnpj("112223330001")
CNPJ_PUNCT = f"{CNPJ[:2]}.{CNPJ[2:5]}.{CNPJ[5:8]}/{CNPJ[8:12]}-{CNPJ[12:]}"


def _wrong(doc):
    """The same document with its last check digit off by one: a made-up value."""
    return doc[:-1] + str((int(doc[-1]) + 1) % 10)


NO_DATA = "no real values: LEAKGUARD_NO_REAL_DATA=1"   # how a waived run describes itself


def _clean_env(monkeypatch, waive=True):
    """No GETFINANCE_DATA, CI, name patterns or GIT_* (a git hook exports them) from the caller's shell.
    The run without real data is waived, as for a contributor who has none, unless `waive` is False."""
    for name in [n for n in os.environ if n.startswith("GIT_")]:
        monkeypatch.delenv(name)
    for name in ("GETFINANCE_DATA", "LEAKGUARD_NAME_PATTERNS", "CI", "LEAKGUARD_NO_REAL_DATA"):
        monkeypatch.delenv(name, raising=False)
    if waive:
        monkeypatch.setenv("LEAKGUARD_NO_REAL_DATA", "1")


def repo(tmp_path, monkeypatch):
    """Run the guard from a scratch git repository (with no repository above it), with one made-up
    owner name pattern, no real data and the run without it waived (LEAKGUARD_NO_REAL_DATA=1)."""
    _clean_env(monkeypatch)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    _git("init", "-q", cwd=tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(leakguard_patterns, "NAME_PATTERNS", [re.compile(r"fulano\s+de\s+tal", re.IGNORECASE)])
    monkeypatch.setattr(leakguard_checkout, "MAIN_ROOT", tmp_path)   # no .leakguard-workspaces unless a test writes one
    monkeypatch.setattr(leakguard_checkout, "MAIN_ROOT_ERROR", None)

    def write(rel, text):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
        return rel

    return write


def run(capsys, *args):
    code = leakguard.main(list(args))
    out, err = capsys.readouterr()
    return code, out, [line.strip() for line in err.splitlines()[1:]]


# ---------------------------------------------------------------------------- scratch git repositories


def _git(*args, cwd):
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "init.defaultBranch=main"]
    return subprocess.run([*git, *args], cwd=cwd, check=True, capture_output=True)


def _init(path):
    path.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", cwd=path)
    return path


# ---------------------------------------------------------------------------- a workspace with made-up real data


def _write_parquet(path, frame):
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(path)


def make_workspace(root, owner=OWNER):
    """A workspace with made-up "real" data: raw accounts and transactions, edicao.db and .env."""
    raw = root / "data" / "raw"
    _write_parquet(raw / "accounts" / "ingestion_date=2026-11-01" / "data.parquet", pl.DataFrame({
        "id": ["acc-uuid-0001-aaaa"], "itemId": ["item-uuid-0002-bbbb"], "number": [NUMBER],
        "owner": [owner], "taxNumber": ["***.982.247-**"], "name": ["Conta"], "balance": ["123456.78"],
        "bankData": [str({"transferNumber": TRANSFER, "closingBalance": 1.5, "x": None, "ok": True})],
        "creditData": [str({"balanceDueDate": "2026-11-20", "updatedAt": "2026-11-12T09:00:00.000Z"})],
    }))
    # A partition's temp file (landing2raw writes it, then renames it): never read.
    _write_parquet(raw / "accounts" / "ingestion_date=2026-11-02" / ".data.parquet.tmp",
                   pl.DataFrame({"owner": ["Temporario Nao Lido Jamais"]}))
    _write_parquet(raw / "transactions" / "ingestion_date=2026-11-01" / "data.parquet", pl.DataFrame({
        "id": ["txn-uuid-0003-cccc"], "accountId": ["acc-uuid-0001-aaaa"], "description": ["PADARIA"],
        "paymentData": [json.dumps({"receiver": {"name": RECEIVER, "documentNumber": {"value": "x"}},
                                    "ids": [7_654_321, 12]})],
        "merchant": [None],
    }))
    conn = sqlite3.connect(root / "data" / "edicao.db")
    conn.execute("CREATE TABLE mapeamento (padrao TEXT, subcategoria TEXT)")
    conn.executemany("INSERT INTO mapeamento VALUES (?, ?)", [("PADARIA DO ZE", "pao"), ("NETFLIX", "lazer")])
    conn.commit()
    conn.close()
    (root / ".env").write_text(f"DB_NAME=x\nCLIENT_ID=client-id-123\nCLIENT_SECRET='{SECRET}'\n"
                               "ITEM_IDS=item-uuid-0002-bbbb, item-uuid-0009-zzzz\n", encoding="utf-8")
    return root


def workspace(tmp_path):
    return make_workspace(tmp_path / "ws")


NO_SOURCE = ("REAL-VALUES: no real values to check against: neither GETFINANCE_DATA, .leakguard-workspaces "
             "nor a workspaces/GetFinance/data/ of the main checkout names any. Outside CI the guard needs the "
             "real data: set GETFINANCE_DATA=<workspace>/data on the command (GETFINANCE_DATA=<workspace>/data "
             "git commit ...); with no real data at all, declare it with LEAKGUARD_NO_REAL_DATA=1")
