"""Tests for scripts/leakguard.py, the one guard against private files, personal data and real values.

Every PII-shaped or "real" value is made up and assembled at run time, so this file never holds
one and the guard passes over it like any other committed file. Valid CPFs and CNPJs come from
the check-digit rule.
"""

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import polars as pl
import pytest

from scripts import leakguard

GUARD = Path(leakguard.__file__)
ACCT = "1234" + "-5"
EMAIL = "someone" + "@" + "gmail.com"
OWNER = "Fulana Exemplo" + " da Silva"
NUMBER = "98765" + "-4"
NUMBER_BARE = NUMBER.replace("-", "")   # the real number written without its dash: no ACCT shape
TRANSFER = "901/0001/" + NUMBER
RECEIVER = "Beltrano" + " Recebedor"
SECRET = "s3cr3t" + "-value"


def _cpf(base9):
    digits = base9
    for size in (9, 10):
        total = sum(int(d) * w for d, w in zip(digits, range(size + 1, 1, -1), strict=True))
        digits += str((total * 10) % 11 % 10)
    return digits


def _cnpj(base12):
    digits = base12
    weights = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    for size in (12, 13):
        total = sum(int(d) * w for d, w in zip(digits, weights[13 - size:], strict=True))
        digits += str(0 if total % 11 < 2 else 11 - total % 11)
    return digits


CPF = _cpf("529982247")
CPF_PUNCT = f"{CPF[:3]}.{CPF[3:6]}.{CPF[6:9]}-{CPF[9:]}"
CNPJ = _cnpj("112223330001")
CNPJ_PUNCT = f"{CNPJ[:2]}.{CNPJ[2:5]}.{CNPJ[5:8]}/{CNPJ[8:12]}-{CNPJ[12:]}"


def _wrong(doc):
    """The same document with its last check digit off by one: a made-up value."""
    return doc[:-1] + str((int(doc[-1]) + 1) % 10)


def _clean_env(monkeypatch):
    """No GETFINANCE_DATA, name patterns or GIT_* (a git hook exports them) from the caller's shell."""
    for name in [n for n in os.environ if n.startswith("GIT_")]:
        monkeypatch.delenv(name)
    monkeypatch.delenv("GETFINANCE_DATA", raising=False)
    monkeypatch.delenv("LEAKGUARD_NAME_PATTERNS", raising=False)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """Run the guard from a scratch root, with one made-up owner name pattern and no real data."""
    _clean_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(leakguard, "NAME_PATTERNS", [re.compile(r"fulano\s+de\s+tal", re.IGNORECASE)])
    monkeypatch.setattr(leakguard, "MAIN_ROOT", tmp_path)   # no .leakguard-workspaces unless a test writes one
    monkeypatch.setattr(leakguard, "MAIN_ROOT_ERROR", None)

    def write(rel, text):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
        return rel

    return write


def run(capsys, *args):
    code = leakguard.main(list(args))
    out, err = capsys.readouterr()
    return code, out, [line.strip() for line in err.splitlines()[1:]]


# ---------------------------------------------------------------------------- private files


def test_clean_files_pass_and_are_counted(repo, capsys):
    files = [repo("a.py", "x = 2024\nport = 8080\n"), repo("b.md", "version 1.2.3\n")]
    code, out, failures = run(capsys, *files)
    assert (code, failures) == (0, [])
    assert out.strip() == ("leak guard: 2 file(s) clean (no real values: neither GETFINANCE_DATA nor "
                           ".leakguard-workspaces names any)")


def test_a_private_surface_is_blocked_without_reading_it(repo, capsys):
    """Blocked by its path alone: it need not exist, and its content adds no second failure."""
    rel = repo("workspaces/GetFinance-Dev/x.py", f"cpf = '{CPF}'\n")
    code, _, failures = run(capsys, rel, "docs/missing.md")
    assert code == 1
    assert failures == ["PATH-BLOCK: workspaces/GetFinance-Dev/x.py is under a private surface",
                        "PATH-BLOCK: docs/missing.md is under a private surface"]


@pytest.mark.parametrize("rel", ["workspaces/GetFinance/data/raw/x.csv", "workspaces/GetFinance/report/r.txt",
                                 "workspaces/W/docs/archive/old.md", "workspaces/W/docs/workbook.md",
                                 "workspaces/W/config/investments.yaml",
                                 "workspaces/W/config/merchant_normalization.local.yaml",
                                 "workspaces/W/.env", "workspaces/W/config/.env.local", ".env",
                                 "a.parquet", "tests/fixtures/e.db", "w.xlsx", "deep/dir/book.XLSM",
                                 "x.sqlite", "y.duckdb", "z.xls"])
def test_private_files_are_blocked_without_reading_them(repo, capsys, rel):
    code, _, failures = run(capsys, rel)
    assert code == 1
    assert failures == [f"PRIVATE: {rel} is a private file (data, report, .env, real config, a data "
                        "artifact or gitignored)"]


@pytest.mark.parametrize("rel", ["workspaces/GetFinance/.env.example", "workspaces/W/scripts/report/edicao.py",
                                 "workspaces/W/docs/data.md", "workspaces/W/config/investments.synthetic.yaml",
                                 "workspaces/W/tests/data_mutator.py", "workspaces/W/scripts/data/x.py",
                                 "data/x.csv", "report/quality.txt", "config/investments.yaml"])
def test_ordinary_files_are_read_not_blocked(repo, capsys, rel):
    """Workspace rules hold inside workspaces/<name>/ only; elsewhere the content is checked."""
    assert run(capsys, repo(rel, "x = 1\n"))[:1] == (0,)
    assert run(capsys, repo(rel, f"x = '{CPF}'\n"))[2] == [f"CPF: {rel}:1 -> '52…' (11 chars)"]


# ---------------------------------------------------------------------------- patterns


@pytest.mark.parametrize(("label", "value"), [("CPF", CPF), ("CPF", CPF_PUNCT), ("CNPJ", CNPJ),
                                              ("CNPJ", CNPJ_PUNCT), ("ACCT", ACCT), ("EMAIL", EMAIL)])
def test_personal_data_fails_with_its_label_and_line_without_being_echoed(repo, capsys, label, value):
    code, _, failures = run(capsys, repo("notes.txt", f"first\nbefore {value} after\n"))
    assert code == 1
    assert failures == [f"{label}: notes.txt:2 -> '{value[:2]}…' ({len(value)} chars)"]


@pytest.mark.parametrize("value", [_wrong(CPF), _wrong(CPF_PUNCT), _wrong(CNPJ), "000" + "00000000",
                                   "111" + "22233344", "12345678" + "000190", "98765432" + "000155",
                                   "000" + "00000000000"])
def test_a_document_with_invalid_check_digits_is_made_up_and_passes(repo, capsys, value):
    assert run(capsys, repo("gen.py", f"doc = '{value}'\n"))[2] == []


def test_a_cpf_with_its_first_check_digit_wrong_passes(repo, capsys):
    first_wrong = CPF[:9] + str((int(CPF[9]) + 1) % 10) + CPF[10]
    cnpj_first_wrong = CNPJ[:12] + str((int(CNPJ[12]) + 1) % 10) + CNPJ[13]
    assert run(capsys, repo("gen.py", f"a = '{first_wrong}'\nb = '{cnpj_first_wrong}'\n"))[2] == []


@pytest.mark.parametrize("text", [f"x = {CPF}9", f"x = 9{CPF}", f"x = 1.{CPF}", f"x = {CPF}.5",
                                  f"sha256:7f{CPF}ab", f"h = a{CNPJ}", f"x = {CNPJ}0"])
def test_digits_inside_a_longer_number_or_a_word_are_not_a_document(repo, capsys, text):
    assert run(capsys, repo("x.py", text + "\n"))[2] == []


@pytest.mark.parametrize("text", [f"CPF {CPF_PUNCT}.", f"CPF: {CPF[:3]} {CPF[3:6]} {CPF[6:9]} {CPF[9:]}",
                                  f"cnpj {CNPJ_PUNCT}.", f"({CPF})"])
def test_a_document_at_the_end_of_a_sentence_or_with_spaces_is_flagged(repo, capsys, text):
    failures = run(capsys, repo("x.md", text + "\n"))[2]
    assert len(failures) == 1


@pytest.mark.parametrize("text", ["SERIE 2026-4-10 10X", "0000-0", "99999-9", "a1234-5", "1234-56", "123-4"])
def test_a_date_part_a_filler_or_another_shape_is_not_an_account(repo, capsys, text):
    assert run(capsys, repo("x.py", f"s = '{text}'\n"))[2] == []


def test_an_account_at_the_end_of_a_date_like_run_is_still_flagged(repo, capsys):
    assert len(run(capsys, repo("x.py", f"s = '10-{ACCT}'\n"))[2]) == 1


@pytest.mark.parametrize(("user", "domain"), [("x.y", "uol.com.br"), ("a", "bol.com.br"), ("a", "proton.me"),
                                             ("z", "terra.com.br"), ("q", "googlemail.com"), ("w", "yahoo.com.br"),
                                             ("m", "icloud.com"), ("o", "OUTLOOK.COM"), ("h", "hotmail.com")])
def test_personal_e_mail_providers_are_flagged(repo, capsys, user, domain):
    assert len(run(capsys, repo("x.py", f"to = '{user}@{domain}'\n"))[2]) == 1


def test_an_example_e_mail_passes(repo, capsys):
    assert run(capsys, repo("x.py", "to = 'test@example.com'\nfrom = 'ci@github.com'\n"))[2] == []


def test_an_owner_name_is_blocked_except_where_attribution_is_intended(repo, capsys):
    text = "Copyright\nFulano de\n  Tal\n"
    code, _, failures = run(capsys, repo("src.py", text), repo("README.md", text), repo("LICENSE", text))
    assert code == 1
    assert failures == ["NAME: src.py:2 -> 'Fu…' (15 chars)"]


def test_attribution_files_are_still_checked_for_other_pii(repo, capsys):
    assert run(capsys, repo("README.md", f"mail me at {EMAIL}\n"))[2] == ["EMAIL: README.md:1 -> 'so…' (17 chars)"]


def test_every_hit_in_every_file_is_reported(repo, capsys):
    a = repo("a.txt", f"{ACCT} and {EMAIL}\n")
    b = repo("b.txt", f"\n{CPF}\n")
    assert run(capsys, a, b)[2] == ["ACCT: a.txt:1 -> '12…' (6 chars)", "EMAIL: a.txt:1 -> 'so…' (17 chars)",
                                    "CPF: b.txt:2 -> '52…' (11 chars)"]


def test_files_skipped_on_the_way_do_not_end_the_scan(repo, capsys, tmp_path):
    """A blocked, missing, binary or unreadable file is passed over; the files after it are still scanned."""
    unreadable = repo("locked.txt", "x\n")
    (tmp_path / unreadable).chmod(0)
    (tmp_path / "font.woff2").write_bytes(b"wOF2\x00" + CPF.encode())
    try:
        failures = run(capsys, "docs/a.md", "gone.py", "font.woff2", unreadable, repo("last.txt", f"{CPF}\n"))[2]
    finally:
        (tmp_path / unreadable).chmod(0o644)
    assert failures == ["PATH-BLOCK: docs/a.md is under a private surface", "CPF: last.txt:1 -> '52…' (11 chars)"]


def test_text_that_is_not_utf8_is_still_checked(repo, capsys, tmp_path):
    """cp1252 and UTF-16 (PowerShell `>`) files are text too."""
    (tmp_path / "latin.txt").write_bytes("Ação ".encode("cp1252") + CPF.encode() + b"\n")
    (tmp_path / "u16.txt").write_bytes(f"x\n{CPF}\n".encode("utf-16"))
    (tmp_path / "u16be.txt").write_bytes(b"\xfe\xff" + f"{CPF}\n".encode("utf-16-be"))
    assert run(capsys, "latin.txt", "u16.txt", "u16be.txt")[2] == [
        "CPF: latin.txt:1 -> '52…' (11 chars)", "CPF: u16.txt:2 -> '52…' (11 chars)",
        "CPF: u16be.txt:1 -> '52…' (11 chars)"]


def test_a_file_path_is_judged_in_posix_form(repo, capsys):
    rel = repo("workspaces/W/data/x.txt", "x\n")
    assert run(capsys, str(Path(rel)))[2][0].startswith("PRIVATE: workspaces/W/data/x.txt ")


# ---------------------------------------------------------------------------- owner name patterns


@pytest.fixture
def guard_home(tmp_path, monkeypatch):
    """Point the guard at a scratch repo root (its .leakguard-local lives there) with no env."""
    monkeypatch.setattr(leakguard, "MAIN_ROOT", tmp_path)
    monkeypatch.delenv("LEAKGUARD_NAME_PATTERNS", raising=False)
    return tmp_path


def names(patterns, text):
    return [m.group(0) for rx in patterns for m in rx.finditer(text)]


def test_no_env_and_no_local_file_means_no_name_patterns(guard_home):
    assert leakguard._load_owner_name_patterns() == []


def test_env_patterns_are_split_by_pathsep_and_blanks_dropped(guard_home, monkeypatch):
    monkeypatch.setenv("LEAKGUARD_NAME_PATTERNS", os.pathsep.join(["fulano", " ", "beltrano"]))
    found = names(leakguard._load_owner_name_patterns(), "FULANO e Beltrano")
    assert found == ["FULANO", "Beltrano"]


def test_local_file_adds_one_pattern_per_line_skipping_comments_and_blanks(guard_home, monkeypatch):
    monkeypatch.setenv("LEAKGUARD_NAME_PATTERNS", "fulano")
    (guard_home / ".leakguard-local").write_text("# owner names\n\n  ciclano  \n#beltrano\n", encoding="utf-8")
    found = names(leakguard._load_owner_name_patterns(), "Fulano, Ciclano, #beltrano")
    assert found == ["Fulano", "Ciclano"]


def test_leakguard_local_is_read_from_the_given_root(tmp_path, guard_home):
    other = tmp_path / "other"
    other.mkdir()
    (other / ".leakguard-local").write_text("ciclano\n", encoding="utf-8")
    (guard_home / ".leakguard-local").write_text("fulano\n", encoding="utf-8")
    assert names(leakguard._load_owner_name_patterns(other), "Fulano Ciclano") == ["Ciclano"]
    assert names(leakguard._load_owner_name_patterns(), "Fulano Ciclano") == ["Fulano"]


# ---------------------------------------------------------------------------- the main checkout


def _git(*args, cwd):
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "init.defaultBranch=main"]
    return subprocess.run([*git, *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def clean_git(tmp_path, monkeypatch):
    """D5: no inherited GIT_* (a git hook exports them) and no repo above tmp_path."""
    _clean_env(monkeypatch)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    return tmp_path


def _init(path):
    path.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", cwd=path)
    return path


def test_from_a_worktree_the_main_checkout_is_found(clean_git):
    """D1: commits are made from worktrees, where gitignored files (.leakguard-local,
    .leakguard-workspaces) do not exist."""
    main = _init(clean_git / "main")
    (main / "README.md").write_text("x\n", encoding="utf-8")
    _git("add", "-A", cwd=main)
    _git("commit", "-qm", "init", cwd=main)
    wt = clean_git / "wt"
    _git("worktree", "add", "-q", str(wt), cwd=main)
    assert leakguard._main_checkout(wt)[0].resolve() == main.resolve()
    assert leakguard._main_checkout(wt)[1] is None
    assert leakguard._main_checkout(main)[0].resolve() == main.resolve()


def test_a_worktree_whose_main_checkout_is_gone_is_an_error(clean_git):
    wt = clean_git / "wt"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: /nonexistent/main/.git/worktrees/wt\n", encoding="utf-8")
    root, error = leakguard._main_checkout(wt)
    assert root == wt
    assert error == ("this is a worktree and its main checkout could not be found, so "
                     ".leakguard-local and .leakguard-workspaces were not read")


def test_outside_a_git_repo_the_root_is_local_and_not_an_error(clean_git):
    assert leakguard._main_checkout(clean_git) == (clean_git, None)


def test_a_worktree_without_its_main_checkout_fails_the_check(repo, capsys, monkeypatch):
    """D2: without the main checkout the real data's workspaces are not known; that must not pass."""
    monkeypatch.setattr(leakguard, "MAIN_ROOT_ERROR", "this is a worktree and its main checkout could not be found")
    code, _, failures = run(capsys, repo("a.py", "x = 1\n"))
    assert code == 1
    assert failures == ["REAL-VALUES: this is a worktree and its main checkout could not be found"]


# ---------------------------------------------------------------------------- real values: reading them


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


@pytest.fixture
def workspace(tmp_path):
    return make_workspace(tmp_path / "ws")


def test_real_values_come_from_raw_edicao_and_env(workspace):
    values = leakguard.real_values(workspace)
    for expected, source in [(OWNER.lower(), "raw.accounts.owner"), (NUMBER, "raw.accounts.number"),
                             (TRANSFER, "raw.accounts.bankData"), (RECEIVER.lower(), "raw.transactions.paymentData"),
                             ("txn-uuid-0003-cccc", "raw.transactions.id"), ("7654321", "raw.transactions.paymentData"),
                             ("padaria do ze", "edicao.mapeamento"), (SECRET, ".env.CLIENT_SECRET"),
                             ("item-uuid-0009-zzzz", ".env.ITEM_IDS"), ("client-id-123", ".env.CLIENT_ID")]:
        assert values.get(expected) == source, expected


def test_amounts_descriptions_dates_short_words_and_temp_files_are_not_real_values(workspace):
    values = leakguard.real_values(workspace)
    for absent in ["123456.78", "padaria", "netflix", "x", "conta", "2026-11-20", "2026-11-12t09:00:00.000z",
                   "temporario nao lido jamais", "1.5", "true", "12"]:
        assert absent not in values, absent


def test_no_data_means_no_real_values(tmp_path):
    assert leakguard.real_values(tmp_path) == {}


def test_each_part_of_a_transfer_number_is_a_value_of_its_own(workspace):
    """The account segment may be written alone, without the bank and agency around it."""
    values = leakguard.real_values(workspace)
    assert values[TRANSFER] == "raw.accounts.bankData"
    assert NUMBER in values
    assert "0001" not in values   # a part too short to block


@pytest.mark.parametrize(("leaf", "worth"), [("abc12", False), ("abc123", True), ("netflixnetfl", True),
                                             ("netflixnetf", False), ("netflix brasil", True), ("000000", False),
                                             ("aaaaaa", False), ("111-111", False), ("12345", False),
                                             ("2026-11-20", False), ("2026-11-12 09:00", False), ("901/0001", True)])
def test_which_leaves_are_worth_blocking(leaf, worth):
    assert leakguard._worth_blocking(leaf) is worth


def test_env_values_of_any_key_with_export_quotes_and_comments(tmp_path):
    """Every value in .env is a credential to keep out, however the line is written."""
    (tmp_path / ".env").write_text("# comment\nexport CLIENT_SECRET=s3cr3t-export\n"
                                   "CLIENT_ID=client-abc-123   # dev app\nOTHER_TOKEN=\"tok-999-xyz\"\n"
                                   "EMPTY=\nNOEQUALS\n  \n", encoding="utf-8")
    assert leakguard.real_values(tmp_path) == {"s3cr3t-export": ".env.CLIENT_SECRET",
                                               "client-abc-123": ".env.CLIENT_ID", "tok-999-xyz": ".env.OTHER_TOKEN"}


@pytest.mark.parametrize(("raw", "expected"), [("abc   # dev app", "abc   "), ("abc#not-a-comment", "abc#not-a-comment"),
                                               ("#x", "#x"), ("a\t#b", "a\t"), ("a " + " " * 50_000 + "b",
                                                                                  "a " + " " * 50_000 + "b")])
def test_inline_comments_are_cut_only_after_whitespace(raw, expected):
    assert leakguard._strip_inline_comment(raw) == expected


@pytest.mark.parametrize(("text", "expected"), [('{"a": ["x", {"b": "y"}]}', ["x", "y"]), ("{'a': None, 'b': 'z'}", ["z"]),
                                                ("[1, 'w']", ["1", "w"]), ("{not json", ["{not json"]),
                                                ("  plain  ", ["plain"]), ("[" * 5000, ["[" * 5000])])
def test_nested_text_is_opened_into_its_string_leaves(text, expected):
    assert list(leakguard._string_leaves(text)) == expected


# ---------------------------------------------------------------------------- real values: matching them


def _hits(text, values):
    return leakguard.real_value_hits(text, values)


@pytest.mark.parametrize("written", [NUMBER_BARE, "98765 4", "98765.4", "98765/4", f"account {NUMBER}."])
def test_a_real_number_matches_with_any_or_no_separator(written):
    assert _hits(f"x = '{written}'", {NUMBER: "raw.accounts.number"}) == [(1, NUMBER, "raw.accounts.number")]


def test_a_real_number_inside_a_longer_number_does_not_match():
    values = {NUMBER: "raw.accounts.number"}
    assert _hits("x = 1987654", values) == []
    assert _hits("x = 9876543", values) == []


def test_a_real_name_matches_in_any_case_and_wrapped_across_lines():
    values = {OWNER.lower(): "raw.accounts.owner"}
    assert _hits(f"a\nWritten by {OWNER.upper()[:14]}\n   da Silva, 2026\n", values) == [
        (2, OWNER.lower(), "raw.accounts.owner")]


def test_every_occurrence_is_a_hit_in_line_order():
    values = {"zz-value-1": "a", NUMBER: "b"}
    assert _hits(f"{NUMBER}\nzz-value-1 and {NUMBER}\n", values) == [(1, NUMBER, "b"), (2, NUMBER, "b"),
                                                                     (2, "zz-value-1", "a")]


# ---------------------------------------------------------------------------- real values: where they come from


def test_getfinance_data_gives_the_real_values_of_its_workspace(repo, capsys, workspace, monkeypatch):
    monkeypatch.setenv("GETFINANCE_DATA", str(workspace / "data"))
    code, _, failures = run(capsys, repo("fixture.py", f"x = 1\nOWNER = '{OWNER}'\nS = '{SECRET}'\n"))
    assert code == 1
    assert failures == [f"REAL: fixture.py:2 -> 'fu…' ({len(OWNER)} chars, raw.accounts.owner from ws)",
                        f"REAL: fixture.py:3 -> 's3…' ({len(SECRET)} chars, .env.CLIENT_SECRET from ws)"]
    assert OWNER not in "".join(failures)


def test_a_clean_file_says_how_many_real_values_it_was_checked_against(repo, capsys, workspace, monkeypatch):
    monkeypatch.setenv("GETFINANCE_DATA", str(workspace / "data"))
    code, out, _ = run(capsys, repo("a.py", "x = 1\n"))
    assert code == 0
    assert out.strip() == f"leak guard: 1 file(s) clean ({len(leakguard.real_values(workspace))} real values from ws)"


@pytest.mark.parametrize("bad", ["missing/data", "ws", "noraw/data"])
def test_getfinance_data_that_is_not_a_data_directory_with_raw_fails_closed(repo, capsys, tmp_path, monkeypatch, bad):
    """A typo in the variable would check against no data and pass silently: it fails instead. It
    names the data/ directory itself, not the workspace above it."""
    make_workspace(tmp_path / "ws")
    (tmp_path / "noraw" / "data").mkdir(parents=True)
    monkeypatch.setenv("GETFINANCE_DATA", str(tmp_path / bad))
    code, _, failures = run(capsys, repo("a.py", "x = 1\n"))
    assert code == 1
    assert failures == [f"REAL-VALUES: GETFINANCE_DATA={tmp_path / bad} is not a workspace's data/ directory "
                        "with raw/ in it"]


def test_getfinance_data_that_yields_no_real_value_fails(repo, capsys, tmp_path, monkeypatch):
    empty = tmp_path / "empty" / "data"
    (empty / "raw").mkdir(parents=True)
    monkeypatch.setenv("GETFINANCE_DATA", str(empty))
    code, out, failures = run(capsys, repo("a.py", "x = 1\n"))
    assert (code, out) == (1, "")
    assert failures == [f"REAL-VALUES: GETFINANCE_DATA={empty} yields no real value (no data/raw, data/edicao.db "
                        "or .env there); nothing was checked against it"]


def test_an_empty_getfinance_data_is_unset(repo, capsys, monkeypatch):
    monkeypatch.setenv("GETFINANCE_DATA", "")
    assert run(capsys, repo("a.py", "x = 1\n"))[0] == 0


def test_a_listed_workspace_outside_the_checkout_is_checked(repo, capsys, tmp_path):
    ws = make_workspace(tmp_path.parent / f"{tmp_path.name}-outside" / "Real")
    repo(".leakguard-workspaces", f"# the real workspace stays on the Windows drive\n\n  {ws}  \n")
    code, _, failures = run(capsys, repo("a.py", f"x = '{NUMBER_BARE}'\n"))
    assert code == 1
    assert failures == [f"REAL: a.py:1 -> '98…' ({len(NUMBER)} chars, raw.accounts.number from Real)"]


def test_a_listed_entry_may_be_relative_to_the_root_or_start_with_a_tilde(repo, capsys, tmp_path, monkeypatch):
    home = tmp_path.parent / f"{tmp_path.name}-home"
    make_workspace(home / "Ws", owner="Ciclano Morador da Casa")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))   # what expanduser reads on Windows
    make_workspace(tmp_path / "rel")
    repo(".leakguard-workspaces", "rel\n~/Ws\n")
    code, _, failures = run(capsys, repo("a.py", f"x = '{OWNER}'\ny = 'Ciclano Morador da Casa'\n"))
    assert code == 1
    assert [f.rsplit(" from ", 1)[1] for f in failures] == ["rel)", "Ws)"]


def test_a_listed_directory_without_data_fails_closed(repo, capsys, tmp_path):
    repo(".leakguard-workspaces", f"{tmp_path / 'gone'}\n")
    code, _, failures = run(capsys, repo("a.py", "x = 1\n"))
    assert code == 1
    assert failures == [f"REAL-VALUES: {tmp_path / 'gone'} (listed in .leakguard-workspaces) yields no real value "
                        "(no data/raw, data/edicao.db or .env there); nothing was checked against it"]


def test_data_that_cannot_be_read_fails_closed_and_does_not_stop_the_others(repo, capsys, tmp_path, workspace):
    broken = tmp_path / "broken"
    (broken / "data" / "raw" / "accounts" / "ingestion_date=2026-11-01").mkdir(parents=True)
    (broken / "data" / "raw" / "accounts" / "ingestion_date=2026-11-01" / "data.parquet").write_bytes(b"not parquet")
    repo(".leakguard-workspaces", f"{broken}\n{workspace}\n")
    code, _, failures = run(capsys, repo("a.py", f"x = '{NUMBER_BARE}'\n"))
    assert code == 1
    assert failures[0].startswith(f"REAL-VALUES: {broken} (listed in .leakguard-workspaces): its data could not "
                                  "be read (")
    assert failures[1:] == [f"REAL: a.py:1 -> '98…' ({len(NUMBER)} chars, raw.accounts.number from ws)"]


def test_a_workspace_named_twice_is_checked_once(repo, capsys, workspace, monkeypatch):
    monkeypatch.setenv("GETFINANCE_DATA", str(workspace / "data"))
    repo(".leakguard-workspaces", f"{workspace}\n{workspace}/.\n")
    code, _, failures = run(capsys, repo("a.py", f"x = '{NUMBER_BARE}'\n"))
    assert code == 1
    assert len(failures) == 1


def test_the_owner_name_is_attribution_in_license_and_readme_but_nothing_else_is(repo, capsys, workspace, monkeypatch):
    monkeypatch.setenv("GETFINANCE_DATA", str(workspace / "data"))
    lic = repo("LICENSE", f"Copyright (c) 2026 {OWNER}\n")
    readme = repo("README.md", f"by {OWNER}, account {NUMBER[:5]}{NUMBER[6:]}\n")
    other = repo("notes.md", f"{OWNER}\n")
    nested = repo("workspaces/W/README.md", f"{OWNER}\n")
    code, _, failures = run(capsys, lic, readme, other, nested)
    assert code == 1
    assert [f.split(" -> ")[0] for f in failures] == ["REAL: README.md:1", "REAL: notes.md:1",
                                                      "REAL: workspaces/W/README.md:1"]
    assert "raw.accounts.number" in failures[0]


def test_the_owner_exemption_does_not_depend_on_which_source_came_first(repo, capsys, tmp_path):
    """The owner's name may also be a counterparty somewhere; it is still attribution in LICENSE."""
    first = make_workspace(tmp_path / "A", owner="Someone Else Entirely")
    (first / ".env").write_text(f"PAYEE={OWNER}\n", encoding="utf-8")
    make_workspace(tmp_path / "B")
    repo(".leakguard-workspaces", "A\nB\n")
    assert run(capsys, repo("LICENSE", f"(c) {OWNER}\n"))[:1] == (0,)
    assert len(run(capsys, repo("notes.md", f"(c) {OWNER}\n"))[2]) == 2


def test_a_value_of_exactly_the_minimum_length_is_kept(repo, capsys, tmp_path):
    ws = tmp_path / "W"
    ws.mkdir()
    (ws / ".env").write_text("A=ab" + "c123\nB=xy" + "z12\n", encoding="utf-8")
    repo(".leakguard-workspaces", "W\n")
    code, _, failures = run(capsys, repo("a.py", "x = 'abc123'\ny = 'xyz12'\n"))
    assert code == 1
    assert failures == ["REAL: a.py:1 -> 'ab…' (6 chars, .env.A from W)"]


# ---------------------------------------------------------------------------- command line


def _cli_repo(tmp_path, monkeypatch):
    """A scratch repo with its own copy of the guard: its main checkout is the scratch repo, never this one."""
    _clean_env(monkeypatch)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    repo = _init(tmp_path / "repo")
    (repo / "scripts").mkdir()
    shutil.copy(GUARD, repo / "scripts" / "leakguard.py")
    return repo


def _cli(cwd, guard, *args):
    return subprocess.run([sys.executable, str(guard), *args], cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", check=False)


def test_all_checks_tracked_files_with_any_name_and_fails_on_a_real_value(tmp_path, monkeypatch, workspace):
    """Without -z, git quotes a non-ASCII path and the file would be skipped."""
    repo = _cli_repo(tmp_path, monkeypatch)
    (repo / "relatório.md").write_text(f"cpf {CPF}\n", encoding="utf-8")
    (repo / "fixture.py").write_text(f"OWNER = '{OWNER}'\n", encoding="utf-8")
    (repo / "untracked.py").write_text(f"x = '{CPF}'\n", encoding="utf-8")
    _git("add", "relatório.md", "fixture.py", "scripts", cwd=repo)
    (repo / ".leakguard-workspaces").write_text(f"{workspace}\n", encoding="utf-8")
    result = _cli(repo, repo / "scripts" / "leakguard.py", "--all")
    assert result.returncode == 1
    assert "relatório.md:1 -> '52…'" in result.stderr
    assert "REAL: fixture.py:1 -> 'fu…'" in result.stderr
    assert "untracked.py" not in result.stderr
    assert OWNER not in result.stderr + result.stdout


def test_a_clean_repo_passes_with_all(tmp_path, monkeypatch):
    repo = _cli_repo(tmp_path, monkeypatch)
    (repo / "code.py").write_text("x = 1\n", encoding="utf-8")
    _git("add", "-A", cwd=repo)
    result = _cli(repo, repo / "scripts" / "leakguard.py", "--all")
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("leak guard: 2 file(s) clean (no real values")


def test_a_force_added_ignored_file_is_private(tmp_path, monkeypatch):
    """Whatever .gitignore excludes is private, even when force-added."""
    repo = _cli_repo(tmp_path, monkeypatch)
    (repo / ".gitignore").write_text("secret-notes/\n", encoding="utf-8")
    (repo / "secret-notes").mkdir()
    (repo / "secret-notes" / "a.md").write_text("ok\n", encoding="utf-8")
    _git("add", "-A", cwd=repo)
    _git("add", "-f", "secret-notes/a.md", cwd=repo)
    result = _cli(repo, repo / "scripts" / "leakguard.py", "--all")
    assert result.returncode == 1
    assert "PRIVATE: secret-notes/a.md is a private file" in result.stderr
    staged = _cli(repo, repo / "scripts" / "leakguard.py", "secret-notes/a.md", ".gitignore")
    assert staged.stderr.splitlines()[1:] == ["  PRIVATE: secret-notes/a.md is a private file (data, report, .env, "
                                              "real config, a data artifact or gitignored)"]


def test_history_finds_values_messages_and_private_paths_that_later_commits_removed(tmp_path, monkeypatch, workspace):
    repo = _cli_repo(tmp_path, monkeypatch)
    (repo / ".leakguard-workspaces").write_text(f"{workspace}\n", encoding="utf-8")
    (repo / ".gitignore").write_text(".leakguard-workspaces\n", encoding="utf-8")
    (repo / "fixture.py").write_text(f"OWNER = '{OWNER}'\n", encoding="utf-8")
    _git("add", "-A", cwd=repo)
    _git("commit", "-qm", f"pay {RECEIVER} back", cwd=repo)
    (repo / "fixture.py").write_text("OWNER = 'fake'\n", encoding="utf-8")
    (repo / "dump.parquet").write_bytes(b"PAR1\x00")
    _git("add", "-A", cwd=repo)
    _git("commit", "-qm", "scrub", cwd=repo)
    _git("rm", "-q", "dump.parquet", cwd=repo)
    _git("commit", "-qm", "remove", cwd=repo)
    tree = _cli(repo, repo / "scripts" / "leakguard.py", "--all")
    assert tree.returncode == 0, tree.stderr
    history = _cli(repo, repo / "scripts" / "leakguard.py", "--history")
    assert history.returncode == 1
    assert "raw.accounts.owner from ws" in history.stderr
    assert "raw.transactions.paymentData from ws" in history.stderr
    assert "history: dump.parquet: private file was committed" in history.stderr


def test_a_clean_history_passes(tmp_path, monkeypatch):
    repo = _cli_repo(tmp_path, monkeypatch)
    _git("add", "-A", cwd=repo)
    _git("commit", "-qm", "init", cwd=repo)
    result = _cli(repo, repo / "scripts" / "leakguard.py", "--history")
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("leak guard: history clean (no real values")


def test_a_commit_message_file_is_checked_like_any_file(tmp_path, monkeypatch, workspace):
    repo = _cli_repo(tmp_path, monkeypatch)
    (repo / ".leakguard-workspaces").write_text(f"{workspace}\n", encoding="utf-8")
    msg = repo / ".git" / "COMMIT_EDITMSG"
    msg.write_text(f"Pay {RECEIVER} back\n", encoding="utf-8")
    result = _cli(repo, repo / "scripts" / "leakguard.py", str(msg))
    assert result.returncode == 1
    assert "raw.transactions.paymentData from ws" in result.stderr


def test_from_a_worktree_the_listed_workspaces_of_the_main_checkout_are_read(tmp_path, monkeypatch, workspace):
    repo = _cli_repo(tmp_path, monkeypatch)
    (repo / ".gitignore").write_text(".leakguard-workspaces\n", encoding="utf-8")
    (repo / "fixture.py").write_text(f"OWNER = '{OWNER}'\n", encoding="utf-8")
    _git("add", "-A", cwd=repo)
    _git("commit", "-qm", "add", cwd=repo)
    (repo / ".leakguard-workspaces").write_text(f"{workspace}\n", encoding="utf-8")
    wt = tmp_path / "wt"
    _git("worktree", "add", "-q", str(wt), cwd=repo)
    result = _cli(wt, wt / "scripts" / "leakguard.py", "fixture.py")
    assert result.returncode == 1
    assert "REAL: fixture.py:1 -> 'fu…'" in result.stderr


# ---------------------------------------------------------------------------- the same, in process
# The command-line rules above, on the module itself, so coverage and the mutation run see them.


def test_in_process_no_file_is_clean(repo, capsys):
    assert run(capsys) == (0, "leak guard: 0 file(s) clean (no real values: neither GETFINANCE_DATA nor "
                              ".leakguard-workspaces names any)\n", [])


def test_in_process_all_and_history(repo, capsys, tmp_path, monkeypatch, workspace):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    _git("init", "-q", cwd=tmp_path)
    repo(".gitignore", "ws/\n.leakguard-workspaces\n")
    repo("fixture.py", f"OWNER = '{OWNER}'\n")
    repo("ok.py", "x = 1\n")
    _git("add", "-A", cwd=tmp_path)
    _git("commit", "-qm", f"pay {RECEIVER}", cwd=tmp_path)
    repo("fixture.py", "OWNER = 'fake'\n")
    _git("commit", "-qam", "scrub", cwd=tmp_path)
    assert run(capsys, "--all")[:2] == (0, "leak guard: 3 file(s) clean (no real values: neither GETFINANCE_DATA "
                                           "nor .leakguard-workspaces names any)\n")
    assert run(capsys, "--history")[:2] == (0, "leak guard: history clean (no real values: neither "
                                               "GETFINANCE_DATA nor .leakguard-workspaces names any)\n")
    repo(".leakguard-workspaces", f"{workspace}\n")
    code, _, failures = run(capsys, "--history")
    assert code == 1
    # The owner is in the commit that added it and in the one that removed it; the receiver in a message.
    assert sorted(f.rsplit(", ", 1)[1] for f in failures) == ["raw.accounts.owner from ws)"] * 2 + [
        "raw.transactions.paymentData from ws)"]
    repo("x.db", "")
    _git("add", "x.db", cwd=tmp_path)
    _git("commit", "-qm", "db", cwd=tmp_path)
    _git("rm", "-q", "x.db", cwd=tmp_path)
    _git("commit", "-qm", "no db", cwd=tmp_path)
    assert run(capsys, "--history")[2][-1] == "history: x.db: private file was committed"
