"""Tests for scripts/leakguard.py, the one guard against private files, personal data and real values.

Every PII-shaped or "real" value is made up and assembled at run time, so this file never holds
one and the guard passes over it like any other committed file. Valid CPFs and CNPJs come from
the check-digit rule.
"""

import itertools
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
from hypothesis import HealthCheck, given, settings, strategies as st

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


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """Run the guard from a scratch git repository (with no repository above it), with one made-up
    owner name pattern, no real data and the run without it waived (LEAKGUARD_NO_REAL_DATA=1)."""
    _clean_env(monkeypatch)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    _git("init", "-q", cwd=tmp_path)
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
    assert out.strip() == f"leak guard: 2 file(s) clean ({NO_DATA})"


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
                                 "x.sqlite", "y.duckdb", "z.xls",
                                 # m4: the case of a private path does not matter (Windows, the prod disk)
                                 "workspaces/W/Data/raw/x.csv", "workspaces/W/REPORT/r.txt",
                                 "workspaces/W/config/Investments.yaml", "workspaces/W/Docs/Workbook.md",
                                 "workspaces/W/.ENV", "A.PARQUET",
                                 # m4: an archive inside a workspace (backup_dados.py writes zips of data/)
                                 "workspaces/W/backup.zip", "workspaces/W/bk/dados.7z", "workspaces/W/x.tar.gz",
                                 "workspaces/W/x.TGZ", "workspaces/W/x.rar", "workspaces/W/x.tar"])
def test_private_files_are_blocked_without_reading_them(repo, capsys, rel):
    code, _, failures = run(capsys, rel)
    assert code == 1
    assert failures == [f"PRIVATE: {rel} is a private file (data, report, .env, real config, a data "
                        "artifact or gitignored)"]


@pytest.mark.parametrize("rel", ["workspaces/GetFinance/.env.example", "workspaces/W/scripts/report/edicao.py",
                                 "workspaces/W/docs/data.md", "workspaces/W/config/investments.synthetic.yaml",
                                 "workspaces/W/tests/data_mutator.py", "workspaces/W/scripts/data/x.py",
                                 "data/x.csv", "report/quality.txt", "config/investments.yaml",
                                 "tools/backup.zip.md", "release.zip.txt"])
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
                                  f"x = {CNPJ}0", f"x = 7,{CNPJ}"])
def test_digits_inside_a_longer_number_are_not_a_document(repo, capsys, text):
    assert run(capsys, repo("x.py", text + "\n"))[2] == []


@pytest.mark.parametrize("text", [f"sha256:7f{CPF}ab", f"h = a{CNPJ}", f"cpf_{CPF}", f"{CPF}_old", f"id{CPF}",
                                  f"CPF\t{CPF[:3]}\t{CPF[3:6]}\t{CPF[6:9]}\t{CPF[9:]}",
                                  f"{CNPJ[:2]}\t{CNPJ[2:5]}\t{CNPJ[5:8]}\t{CNPJ[8:12]}\t{CNPJ[12:]}"])
def test_a_document_next_to_a_letter_or_underscore_or_split_by_tabs_is_flagged(repo, capsys, text):
    """m1: only a digit next to it makes it part of something longer; a letter or '_' does not."""
    assert len(run(capsys, repo("x.py", text + "\n"))[2]) == 1


def test_a_document_split_across_lines_is_not_one(repo, capsys):
    assert run(capsys, repo("x.txt", f"{CPF[:3]}\n{CPF[3:6]}\n{CPF[6:9]}\n{CPF[9:]}\n"))[2] == []


def test_uv_lock_hashes_are_not_documents_but_its_real_values_are_checked(repo, capsys, workspace, monkeypatch):
    """A hex digest may hold eleven digits that pass the check-digit rule: in uv.lock, which holds
    hashes and package names only, the document patterns do not apply; every other check does."""
    monkeypatch.setenv("GETFINANCE_DATA", str(workspace / "data"))
    code, _, failures = run(capsys, repo("uv.lock", f"hash = 'sha256:7f{CPF}ab'\nx = '{SECRET}'\n{EMAIL}\n"))
    assert code == 1
    assert [f.split(" -> ")[0] for f in failures] == ["EMAIL: uv.lock:3", "REAL: uv.lock:2"]
    assert len(run(capsys, repo("workspaces/W/uv.lock", f"h = 7f{CPF}ab\n"))[2]) == 1


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


def test_a_crlf_file_reports_the_same_finding_as_an_lf_one(repo, capsys, tmp_path):
    """A Windows checkout writes CRLF: a name split across lines measures the same, on the same line."""
    (tmp_path / "src.py").write_bytes(b"Copyright\r\nFulano de\r\n  Tal\r\n")
    code, _, failures = run(capsys, "src.py")
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
    """A blocked, missing or unreadable file is passed over; the files after it are still scanned."""
    unreadable = repo("locked.txt", "x\n")
    (tmp_path / unreadable).chmod(0)
    try:
        failures = run(capsys, "docs/a.md", "gone.py", unreadable, repo("last.txt", f"{CPF}\n"))[2]
    finally:
        (tmp_path / unreadable).chmod(0o644)
    assert failures == ["PATH-BLOCK: docs/a.md is under a private surface", "CPF: last.txt:1 -> '52…' (11 chars)"]


def test_a_file_with_a_nul_byte_is_still_read(repo, capsys, tmp_path):
    """m5: one NUL does not make a text file skippable, and a binary outside a workspace is read too."""
    (tmp_path / "notes.txt").write_bytes(b"header\x00\n" + EMAIL.encode() + b"\n")
    (tmp_path / "font.woff2").write_bytes(b"wOF2\x00" + CPF.encode())
    (tmp_path / "latin.bin").write_bytes(b"\x00A\xe7\xe3o " + CPF.encode())
    assert run(capsys, "notes.txt", "font.woff2", "latin.bin")[2] == [
        "EMAIL: notes.txt:2 -> 'so…' (17 chars)", "CPF: font.woff2:1 -> '52…' (11 chars)",
        "CPF: latin.bin:1 -> '52…' (11 chars)"]


def test_text_that_is_not_utf8_is_still_checked(repo, capsys, tmp_path):
    """cp1252 and UTF-16 (PowerShell `>`) files are text too."""
    (tmp_path / "latin.txt").write_bytes("Ação ".encode("cp1252") + CPF.encode() + b"\n")
    (tmp_path / "u16.txt").write_bytes(f"x\n{CPF}\n".encode("utf-16"))
    (tmp_path / "u16be.txt").write_bytes(b"\xfe\xff" + f"{CPF}\n".encode("utf-16-be"))
    assert run(capsys, "latin.txt", "u16.txt", "u16be.txt")[2] == [
        "CPF: latin.txt:1 -> '52…' (11 chars)", "CPF: u16.txt:2 -> '52…' (11 chars)",
        "CPF: u16be.txt:1 -> '52…' (11 chars)"]


def test_an_archive_outside_a_workspace_is_read_not_blocked(repo, capsys, tmp_path):
    """The archive rule is a workspace's (its data/ is what gets zipped); elsewhere the content is checked."""
    (tmp_path / "fixture.zip").write_bytes(b"PK\x03\x04 " + CPF.encode())
    assert run(capsys, "fixture.zip")[2] == ["CPF: fixture.zip:1 -> '52…' (11 chars)"]


def test_a_binary_file_inside_a_workspace_is_private(repo, capsys, tmp_path):
    """m4: a dump or backup under any name cannot be read for real values, so it is refused; a web
    font or an image (a built app's assets) is public and passes."""
    (tmp_path / "workspaces/W/apps/dist").mkdir(parents=True)
    (tmp_path / "workspaces/W/dados.bak").write_bytes(b"SQLite format 3\x00rows")
    (tmp_path / "workspaces/W/apps/dist/font.woff2").write_bytes(b"wOF2\x00glyphs")
    (tmp_path / "workspaces/W/apps/dist/logo.PNG").write_bytes(b"\x89PNG\x00pixels")
    (tmp_path / "workspaces/W/notes.txt").write_text("plain\n", encoding="utf-8")
    code, _, failures = run(capsys, "workspaces/W/dados.bak", "workspaces/W/apps/dist/font.woff2",
                            "workspaces/W/apps/dist/logo.PNG", "workspaces/W/notes.txt")
    assert code == 1
    assert failures == ["PRIVATE: workspaces/W/dados.bak is a binary file inside a workspace (a dump or a "
                        "backup cannot be read for real values)"]


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
    monkeypatch.delenv("LEAKGUARD_NO_REAL_DATA")
    assert run(capsys, repo("a.py", "x = 1\n"))[0] == 1


NO_SOURCE = ("REAL-VALUES: no real values to check against: neither GETFINANCE_DATA, .leakguard-workspaces "
             "nor a workspaces/GetFinance/data/ of the main checkout names any. Outside CI the guard needs the "
             "real data: set GETFINANCE_DATA=<workspace>/data on the command (GETFINANCE_DATA=<workspace>/data "
             "git commit ...); with no real data at all, declare it with LEAKGUARD_NO_REAL_DATA=1")


@pytest.mark.parametrize("waiver", [None, "", "0", "yes", "true"])
def test_outside_ci_a_run_with_no_source_of_real_values_fails_and_says_what_to_set(repo, capsys, monkeypatch,
                                                                                    waiver):
    """B2: no real data must be declared, never assumed from an absent variable or listing."""
    monkeypatch.delenv("LEAKGUARD_NO_REAL_DATA")
    if waiver is not None:
        monkeypatch.setenv("LEAKGUARD_NO_REAL_DATA", waiver)
    code, out, failures = run(capsys, repo("a.py", "x = 1\n"))
    assert (code, out, failures) == (1, "", [NO_SOURCE])


def test_in_ci_a_run_with_no_source_of_real_values_passes_and_says_why(repo, capsys, monkeypatch):
    monkeypatch.delenv("LEAKGUARD_NO_REAL_DATA")
    monkeypatch.setenv("CI", "true")
    assert run(capsys, repo("a.py", "x = 1\n")) == (0, "leak guard: 1 file(s) clean (no real values: CI)\n", [])


def test_the_waiver_does_not_skip_a_named_source(repo, capsys, workspace, monkeypatch):
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("GETFINANCE_DATA", str(workspace / "data"))
    assert run(capsys, repo("a.py", f"x = '{NUMBER_BARE}'\n"))[0] == 1


def test_the_getfinance_data_directory_of_the_main_checkout_is_found_without_being_named(repo, capsys, tmp_path,
                                                                                        monkeypatch):
    """B2: the real data where the pipeline writes it (workspaces/GetFinance/data/) is read with no
    variable and no listing, as both old guards did."""
    monkeypatch.delenv("LEAKGUARD_NO_REAL_DATA")
    make_workspace(tmp_path / "workspaces" / "GetFinance")
    code, _, failures = run(capsys, repo("a.py", f"x = '{NUMBER_BARE}'\n"))
    assert code == 1
    assert failures == [f"REAL: a.py:1 -> '98…' ({len(NUMBER)} chars, raw.accounts.number from GetFinance)"]


def test_a_getfinance_data_directory_with_only_edicao_is_found(repo, capsys, tmp_path, monkeypatch):
    monkeypatch.delenv("LEAKGUARD_NO_REAL_DATA")
    data = tmp_path / "workspaces" / "GetFinance" / "data"
    data.mkdir(parents=True)
    conn = sqlite3.connect(data / "edicao.db")
    conn.execute("CREATE TABLE mapeamento (padrao TEXT)")
    conn.execute("INSERT INTO mapeamento VALUES ('MERCADO DO BAIRRO')")
    conn.commit()
    conn.close()
    assert run(capsys, repo("a.py", "y = 'mercado do bairro'\n"))[2] == [
        "REAL: a.py:1 -> 'me…' (17 chars, edicao.mapeamento from GetFinance)"]


@pytest.mark.parametrize("layout", ["synthetic", "other workspace", "no raw or edicao"])
def test_synthetic_data_or_another_workspace_is_not_a_source(repo, capsys, tmp_path, monkeypatch, layout):
    """The demo's data (raw/SYNTHETIC, written by its generator) is made up, and another workspace in
    the checkout is not the one whose data is real: reading either as real would flag values that
    are not. Without another source, the run still needs the waiver."""
    monkeypatch.delenv("LEAKGUARD_NO_REAL_DATA")
    if layout == "synthetic":
        ws = make_workspace(tmp_path / "workspaces" / "GetFinance")
        (ws / "data" / "raw" / "SYNTHETIC").write_text("written by the generator\n", encoding="utf-8")
    elif layout == "other workspace":
        make_workspace(tmp_path / "workspaces" / "MyBank")
    else:
        (tmp_path / "workspaces" / "GetFinance" / "data" / "curated").mkdir(parents=True)
    assert run(capsys, repo("a.py", f"x = '{NUMBER_BARE}'\n"))[2] == [NO_SOURCE]


def test_a_found_data_directory_that_yields_nothing_fails_closed(repo, capsys, tmp_path):
    (tmp_path / "workspaces" / "GetFinance" / "data" / "raw").mkdir(parents=True)
    code, _, failures = run(capsys, repo("a.py", "x = 1\n"))
    assert code == 1
    assert failures == [f"REAL-VALUES: {tmp_path / 'workspaces' / 'GetFinance'} (found in the main checkout) "
                        "yields no real value (no data/raw, data/edicao.db or .env there); nothing was checked "
                        "against it"]


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
    if "from mutmut." in GUARD.read_text(encoding="utf-8"):
        # The mutation run instruments the guard, and the copy imports mutmut, which cannot start
        # outside the mutation's own folder. The in-process tests below hold the same rules.
        pytest.skip("guard instrumented by the mutation run: its copy cannot run as a command")
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
    assert result.stdout == f"leak guard: 2 file(s) clean ({NO_DATA})\n"


def test_the_command_without_real_data_or_its_waiver_fails_outside_ci(tmp_path, monkeypatch):
    """B2, as pre-commit and a person run it: no GETFINANCE_DATA, no listing, no data/ in the checkout."""
    repo = _cli_repo(tmp_path, monkeypatch)
    monkeypatch.delenv("LEAKGUARD_NO_REAL_DATA")
    (repo / "code.py").write_text("x = 1\n", encoding="utf-8")
    _git("add", "-A", cwd=repo)
    result = _cli(repo, repo / "scripts" / "leakguard.py", "--all")
    assert (result.returncode, result.stdout) == (1, "")
    assert result.stderr.splitlines()[1:] == [f"  {NO_SOURCE}"]


def test_the_command_finds_the_data_directory_of_a_workspace_in_the_checkout(tmp_path, monkeypatch):
    repo = _cli_repo(tmp_path, monkeypatch)
    monkeypatch.delenv("LEAKGUARD_NO_REAL_DATA")
    make_workspace(repo / "workspaces" / "GetFinance")
    (repo / ".gitignore").write_text("workspaces/*/data/\nworkspaces/*/.env\n", encoding="utf-8")
    (repo / "fixture.py").write_text(f"OWNER = '{OWNER}'\n", encoding="utf-8")
    _git("add", "-A", cwd=repo)
    result = _cli(repo, repo / "scripts" / "leakguard.py", "--all")
    assert result.returncode == 1
    assert "REAL: fixture.py:1 -> 'fu…'" in result.stderr
    assert "raw.accounts.owner from GetFinance" in result.stderr


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
    assert run(capsys) == (0, f"leak guard: 0 file(s) clean ({NO_DATA})\n", [])


def test_in_process_all_and_history(repo, capsys, tmp_path, workspace):
    repo(".gitignore", "ws/\n.leakguard-workspaces\n")
    repo("fixture.py", f"OWNER = '{OWNER}'\n")
    repo("ok.py", "x = 1\n")
    _git("add", "-A", cwd=tmp_path)
    _git("commit", "-qm", f"pay {RECEIVER}", cwd=tmp_path)
    repo("fixture.py", "OWNER = 'fake'\n")
    _git("commit", "-qam", "scrub", cwd=tmp_path)
    assert run(capsys, "--all")[:2] == (0, f"leak guard: 3 file(s) clean ({NO_DATA})\n")
    assert run(capsys, "--history")[:2] == (0, f"leak guard: history clean ({NO_DATA})\n")
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


# ---------------------------------------------------------------------------- the rules, one function at a time
# Each test below pins one function on its own, with the guard run in process (the mutation run skips
# the command-line tests above), so a change to any detail of the function is noticed.


def _total(digits, weights):
    return sum(int(d) * w for d, w in zip(digits, weights, strict=True))


def _documents_by_remainder(size, base_of):
    """{(which check digit, remainder of its sum mod 11): a valid made-up document}: the rule maps a
    remainder of 0 or 1 to digit 0 and the rest to 11 - remainder, and each case is its own branch."""
    found = {}
    for n in range(1, 4000):
        base = base_of(n)
        doc = _cpf(base) if size == CPF_SIZE else _cnpj(base)
        weights = list(range(11, 1, -1)) if size == CPF_SIZE else [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
        first = _total(doc[:size - 2], weights[len(weights) - size + 2:]) % 11
        second = _total(doc[:size - 1], weights[len(weights) - size + 1:]) % 11
        if len(set(doc)) > 1:
            found.setdefault((1, first), doc)
            found.setdefault((2, second), doc)
    return {key: doc for key, doc in found.items() if key[1] <= 2}


CPF_SIZE = 11
CPF_BY_REMAINDER = _documents_by_remainder(CPF_SIZE, lambda n: f"{n * 7919:09d}")
CNPJ_BY_REMAINDER = _documents_by_remainder(14, lambda n: f"{n * 7919 % 100_000_000:08d}0001")


def test_every_remainder_branch_of_the_check_digit_rule_is_exercised_by_the_search():
    assert sorted(CPF_BY_REMAINDER) == sorted(CNPJ_BY_REMAINDER) == [(w, r) for w in (1, 2) for r in (0, 1, 2)]


@pytest.mark.parametrize("key", sorted(CPF_BY_REMAINDER))
def test_a_cpf_is_valid_whatever_the_remainder_of_its_check_sums(key):
    assert leakguard._cpf_valid(CPF_BY_REMAINDER[key]) is True
    assert leakguard._cpf_valid(_wrong(CPF_BY_REMAINDER[key])) is False


@pytest.mark.parametrize("key", sorted(CNPJ_BY_REMAINDER))
def test_a_cnpj_is_valid_whatever_the_remainder_of_its_check_sums(key):
    assert leakguard._cnpj_valid(CNPJ_BY_REMAINDER[key]) is True
    assert leakguard._cnpj_valid(_wrong(CNPJ_BY_REMAINDER[key])) is False


def _two_digit_document(make, size):
    """A valid made-up document written with exactly two distinct digits."""
    for pair in itertools.combinations("0123456789", 2):
        for mask in range(1, 2 ** size):
            doc = make("".join(pair[mask >> i & 1] for i in range(size)))
            if len(set(doc)) == 2:
                return doc
    raise AssertionError("no such document")


def test_documents_made_of_two_distinct_digits_are_still_documents():
    """Only a run of one digit is a filler."""
    assert leakguard._cpf_valid(_two_digit_document(_cpf, 9)) is True
    assert leakguard._cnpj_valid(_two_digit_document(_cnpj, 12)) is True
    assert leakguard._cpf_valid("1" * 11) is False
    assert leakguard._cnpj_valid("1" * 14) is False


def test_a_two_digit_account_is_still_an_account(repo, capsys):
    assert run(capsys, repo("a.txt", "n = 1111" + "-2\n"))[2] == ["ACCT: a.txt:1 -> '11…' (6 chars)"]


@pytest.mark.parametrize(("leaf", "worth"), [("121212", True), ("1212.12", True), ("aaaaaa", False),
                                             ("111111", False)])
def test_two_distinct_characters_are_enough_to_be_a_value(leaf, worth):
    assert leakguard._worth_blocking(leaf) is worth


def test_a_value_that_only_has_digits_in_it_is_not_number_like():
    assert leakguard._is_number_like("abc1234567") is False
    assert leakguard._is_number_like("1234567") is True


def test_inside_a_workspace_only_a_path_under_workspaces_has_an_inside():
    assert leakguard._inside_workspace("src/a/b.txt") is None
    assert leakguard._inside_workspace("workspaces/W") is None
    assert leakguard._inside_workspace("Workspaces/W/Data/x") == "data/x"


def test_an_archive_in_a_nested_folder_outside_a_workspace_is_read(repo, capsys, tmp_path):
    (tmp_path / "tests/fixtures").mkdir(parents=True)
    (tmp_path / "tests/fixtures/data.zip").write_bytes(b"PK\x03\x04 " + CPF.encode())
    assert run(capsys, "tests/fixtures/data.zip")[2] == ["CPF: tests/fixtures/data.zip:1 -> '52…' (11 chars)"]


def test_the_name_of_a_path_is_its_last_part_only():
    assert leakguard._private_reason("a/b/c/x.db", set()) is not None
    assert leakguard._private_reason("a/b/c/.env.example", set()) is None
    assert leakguard._private_reason("a/b.env/c.txt", set()) is None


# ---- _decode


def test_decode_reads_utf8_cp1252_and_utf16_and_never_raises():
    assert leakguard._decode("ação\r\nx".encode()) == "ação\nx"
    assert leakguard._decode(b"a\xe7\xe3o") == "a\xe7\xe3o"   # cp1252
    assert leakguard._decode(b"\x81") == "�"   # a byte cp1252 does not define
    assert leakguard._decode(b"\xff\xfe" + "a\r\n".encode("utf-16-le")) == "a\n"
    assert leakguard._decode(b"\xfe\xff" + "b".encode("utf-16-be")) == "b"
    assert leakguard._decode(b"\xff\xfe" + b"a\x00" + b"\x00\xd8") == "a�"   # a lone surrogate
    assert leakguard._decode(b"\xff\xfe" + b"a\x00" + b"b") == "a�"   # an odd byte count


# ---- the files .gitignore excludes


def test_gitignored_names_the_excluded_files_even_when_tracked_and_whatever_their_name(repo, tmp_path):
    repo(".gitignore", "secret-notes/\n*.tmp\n")
    repo("secret-notes/a.md", "ok\n")
    repo("relatório.tmp", "ok\n")
    repo("ok.py", "x = 1\n")
    _git("add", "-f", "secret-notes/a.md", "relatório.tmp", "ok.py", ".gitignore", cwd=tmp_path)
    assert leakguard._gitignored(["secret-notes/a.md", "ok.py", "relatório.tmp", "new\nline.tmp"]) == {
        "secret-notes/a.md", "relatório.tmp", "new\nline.tmp"}
    assert leakguard._gitignored([]) == set()


def test_a_force_added_ignored_file_is_private_in_process(repo, capsys, tmp_path):
    repo(".gitignore", "secret-notes/\n")
    repo("secret-notes/a.md", "ok\n")
    _git("add", "-f", "secret-notes/a.md", cwd=tmp_path)
    assert run(capsys, "secret-notes/a.md", ".gitignore")[2] == [
        "PRIVATE: secret-notes/a.md is a private file (data, report, .env, real config, a data artifact or "
        "gitignored)"]
    assert run(capsys, "--all")[2] == run(capsys, "secret-notes/a.md")[2]


def test_git_feeds_its_input_and_decodes_whatever_it_prints(repo, tmp_path):
    assert leakguard._git("hash-object", "--stdin", stdin="hello").strip() == "b6fc4c620b67d95f953a5c1c1230aaab5db5a1b0"
    blob = tmp_path / "latin.txt"
    blob.write_bytes(b"a\xe7b\n")
    sha = leakguard._git("hash-object", "-w", "latin.txt").strip()
    assert leakguard._git("cat-file", "blob", sha) == "a�b\n"


def test_git_reads_utf8_and_a_failing_command_is_an_error_that_says_why(monkeypatch):
    calls = []
    original = subprocess.run
    monkeypatch.setattr(leakguard.subprocess, "run", lambda *a, **k: calls.append(k) or original(*a, **k))
    with pytest.raises(leakguard.GitError, match=r"^git no-such-subcommand failed with exit code 1: git: "):
        leakguard._git("no-such-subcommand")
    assert calls[0]["encoding"] == "utf-8"
    assert leakguard._git("no-such-subcommand", ok=(1,)) == ""
    monkeypatch.setattr(leakguard.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a[0], 3, stdout="", stderr=""))
    with pytest.raises(leakguard.GitError, match=r"^git status failed with exit code 3$"):
        leakguard._git("status")


def test_a_git_error_says_only_the_first_line_git_wrote(repo, monkeypatch):
    monkeypatch.setenv("LC_ALL", "C")   # git's messages in English, whatever the machine's language
    monkeypatch.setenv("LANGUAGE", "C")
    with pytest.raises(leakguard.GitError) as raised:
        leakguard._git("log", "nosuchref")
    assert str(raised.value) == ("git log failed with exit code 128: fatal: ambiguous argument 'nosuchref': unknown "
                                 "revision or path not in the working tree.")


def test_git_that_cannot_run_is_an_error(monkeypatch):
    monkeypatch.setenv("PATH", "")
    with pytest.raises(leakguard.GitError, match=r"^git status could not run \(FileNotFoundError: "):
        leakguard._git("status")


def test_a_file_outside_the_repository_is_checked_and_never_ignored(repo, capsys, tmp_path, workspace,
                                                                    monkeypatch):
    """pre-commit's commit-msg hook passes a worktree's message file, which lives in the main checkout's .git/."""
    monkeypatch.setenv("GETFINANCE_DATA", str(workspace / "data"))
    outside = tmp_path.parent / f"{tmp_path.name}-msg" / "COMMIT_EDITMSG"
    outside.parent.mkdir()
    outside.write_text(f"Pay {RECEIVER} back\n", encoding="utf-8")
    repo(".gitignore", "*.tmp\n")
    repo("a.tmp", "ok\n")
    assert leakguard._gitignored([str(outside), "a.tmp"]) == {"a.tmp"}
    assert leakguard._gitignored([str(outside)]) == set()
    code, _, failures = run(capsys, str(outside))
    assert code == 1
    assert [f.split(" -> ")[1].rsplit(", ", 1)[1] for f in failures] == ["raw.transactions.paymentData from ws)"]


def test_gitignored_with_git_failing_is_an_error_not_an_empty_set(repo, monkeypatch):
    monkeypatch.setenv("PATH", "")
    with pytest.raises(leakguard.GitError):
        leakguard._gitignored(["a.py"])


@pytest.mark.parametrize(("args", "broken"), list(itertools.product([["--all"], ["--history"], ["a.py"]],
                                                                   ["not a repository", "no git"])))
def test_the_guard_fails_closed_when_git_fails(repo, capsys, monkeypatch, args, broken):
    """Outside a repository (or with no git) an empty answer from git would read as "nothing to check"."""
    repo("a.py", "x = 1\n")
    if broken == "no git":
        monkeypatch.setenv("PATH", "")
    else:
        shutil.rmtree(".git")   # the cwd, the scratch repository
    code, out, failures = run(capsys, *args)
    assert (code, out) == (1, "")
    assert len(failures) == 1
    assert failures[0].startswith("GIT: git ")
    assert failures[0].endswith("; nothing was checked")



# No line of the guard's output holds a value, nor more than its first 2 characters (df-c0vn): CI logs
# are public. The values are digits (an account, a document) or words of letters that no fixed text of
# the guard uses, so any 3 characters of one found in the output came from the value.
VALUES = st.one_of(
    st.text("0123456789", min_size=6, max_size=14).filter(lambda v: len(set(v)) > 1),
    st.lists(st.text("jqxz", min_size=3, max_size=6), min_size=2, max_size=3).map(" ".join))
FILLER = st.text("ab -=_\n", max_size=8)


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(case=st.tuples(VALUES, FILLER, FILLER, st.sampled_from(["text", "name", "private name"])))
def test_no_output_line_holds_more_than_two_characters_of_a_value(repo, capsys, monkeypatch, case):
    value, before, after, place = case
    monkeypatch.setattr(leakguard, "load_real_values", lambda: ([("ws", {value: "raw.accounts.number"})], []))
    name = f"x{before}{value}{after}x".replace("\n", "_")
    rel = {"text": "notes/a.txt", "name": f"notes/{name}.txt", "private name": f"workspaces/W/data/{name}.csv"}[place]
    repo(rel, f"{before}{value}{after}\n")
    code = leakguard.main([rel])
    err = capsys.readouterr().err.lower()
    assert code == 1
    assert not [value[i:i + 3] for i in range(len(value) - 2) if value[i:i + 3] in err]


# The name of a file is a text like any other (df-0dk7): a document, a real value or the owner's name in
# it is a finding, and the path is shown masked in every message about that file.
@pytest.mark.parametrize("case", [
    (f"comprovante_{CPF}.txt", "CPF"), (f"nota {CNPJ}.md", "CNPJ"),
    ("conta-" + NUMBER_BARE + ".txt", "REAL"), ("Fulano de Tal.md", "NAME")])
def test_a_finding_in_a_file_name_fails_with_the_path_masked(repo, capsys, workspace, monkeypatch, case):
    """Whether the file is there or not (a deleted file is staged by its name alone)."""
    name, label = case
    monkeypatch.setenv("GETFINANCE_DATA", str(workspace / "data"))
    rel = f"notes/{name}"
    for _ in ("missing", "written"):
        code, _, failures = run(capsys, rel)
        assert code == 1
        assert [f.split(":", 1)[0] for f in failures] == [label]
        assert failures[0].startswith(f"{label}: the path 'no…' ({len(rel)} chars) -> ")
        assert name not in failures[0]
        repo(rel, "x = 1\n")


def test_a_private_file_named_after_a_document_is_reported_with_its_path_masked(repo, capsys):
    rel = f"workspaces/W/data/{CPF}.csv"
    code, _, failures = run(capsys, rel)
    assert code == 1
    assert failures == [f"CPF: the path 'wo…' ({len(rel)} chars) -> '52…' (11 chars)",
                        f"PRIVATE: 'wo…' ({len(rel)} chars) is a {leakguard.PRIVATE_FILE}"]


def test_a_binary_file_named_after_a_document_is_reported_with_its_path_masked(repo, capsys, tmp_path):
    rel = f"workspaces/W/scripts/{CPF}.bin"
    (tmp_path / rel).parent.mkdir(parents=True)
    (tmp_path / rel).write_bytes(b"\0\1")
    failures = run(capsys, rel)[2]
    assert failures[1] == f"PRIVATE: 'wo…' ({len(rel)} chars) is a {leakguard.BINARY_IN_WORKSPACE}"


def test_a_path_with_no_finding_is_shown_as_it_is():
    assert leakguard._path_failures("workspaces/W/data/x.csv", []) == ("workspaces/W/data/x.csv", [])


def test_history_reads_a_non_ascii_path_and_masks_a_path_with_a_finding(repo, capsys, tmp_path):
    """Without -z git quotes "relatório.db" and its .db suffix would not match (df-1ryd)."""
    repo("relatório.db", "")
    repo(f"x_{CPF}.db", "")
    _git("add", "-A", cwd=tmp_path)
    _git("commit", "-qm", "dbs", cwd=tmp_path)
    _git("rm", "-q", "relatório.db", f"x_{CPF}.db", cwd=tmp_path)
    _git("commit", "-qm", "no dbs", cwd=tmp_path)
    failures = run(capsys, "--history")[2]
    assert "history: relatório.db: private file was committed" in failures
    assert f"history: 'x_…' ({len(CPF) + 5} chars): private file was committed" in failures
    assert not [f for f in failures if CPF in f]


@pytest.mark.parametrize(("function", "setup"), [
    ("_listed_workspaces", ".leakguard-workspaces"), ("_load_owner_name_patterns", ".leakguard-local")])
def test_text_files_of_the_guard_are_read_as_utf8_and_a_stray_byte_does_not_stop_it(function, setup, repo,
                                                                                     tmp_path, monkeypatch):
    seen = []
    original = Path.read_text

    def spy(self, *args, **kwargs):
        seen.append((self.name, kwargs.get("encoding"), kwargs.get("errors")))
        return original(self, *args, **kwargs)

    (tmp_path / setup).write_bytes(b"# caf\xe9\nfulano\n")
    monkeypatch.setattr(Path, "read_text", spy)
    result = getattr(leakguard, function)()
    assert seen == [(setup, "utf-8", "ignore")]
    assert len(result) == 1


def test_an_env_file_is_read_as_utf8(tmp_path, monkeypatch):
    seen = []
    original = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: seen.append(k.get("encoding")) or original(self, *a, **k))
    (tmp_path / ".env").write_text("A=abcdef\n", encoding="utf-8")
    assert dict(leakguard._env_values(tmp_path / ".env")) == {"abcdef": ".env.A"}
    assert seen == ["utf-8"]


def test_a_missing_or_unreadable_file_is_skipped_and_a_binary_one_in_a_workspace_does_not_stop_the_scan(
        repo, capsys, tmp_path):
    (tmp_path / "workspaces/W").mkdir(parents=True)
    (tmp_path / "workspaces/W/dump.bak").write_bytes(b"x\x00y")
    repo("last.txt", f"cpf {CPF}\n")
    assert run(capsys, "workspaces/W/dump.bak", "gone.txt", "last.txt")[2] == [
        "PRIVATE: workspaces/W/dump.bak is a binary file inside a workspace (a dump or a backup cannot be read "
        "for real values)", "CPF: last.txt:1 -> '52…' (11 chars)"]


# ---- .env and nested values


def test_env_values_keep_an_equals_sign_inside_the_value_and_only_trim_quotes(tmp_path):
    (tmp_path / ".env").write_text("# OLD_KEY=oldvalue123\nTOKEN=ab12cd34==\nEDGE=\"Xedge-valueX\"\n#   =\n",
                                   encoding="utf-8")
    assert dict(leakguard._env_values(tmp_path / ".env")) == {"ab12cd34==": ".env.TOKEN",
                                                              "Xedge-valueX": ".env.EDGE"}


@pytest.mark.parametrize(("raw", "expected"), [("#a ", "#a "), (" #b", " "), ("x#y #z", "x#y ")])
def test_an_inline_comment_may_start_right_after_the_first_character(raw, expected):
    assert leakguard._strip_inline_comment(raw) == expected


def test_a_transfer_number_is_split_on_its_slashes_only_when_it_is_a_number():
    assert list(leakguard._expand(TRANSFER)) == [TRANSFER, "901", "0001", NUMBER]
    assert list(leakguard._expand("901 / 0001 / 98765")) == ["901 / 0001 / 98765", "901", "0001", "98765"]
    assert list(leakguard._expand("abc/def-ghi")) == ["abc/def-ghi"]
    assert list(leakguard._expand("98765432")) == ["98765432"]


def test_a_real_name_with_a_doubled_space_still_matches_the_text_written_with_one():
    values = {"beltrano  recebedor": "src"}
    assert leakguard.real_value_hits("x Beltrano Recebedor", values) == [(1, "beltrano  recebedor", "src")]


# ---- the whole run


def test_the_header_of_a_failed_run_says_what_it_was_checked_against(repo, capsys, workspace, monkeypatch):
    monkeypatch.setenv("GETFINANCE_DATA", str(workspace / "data"))
    assert leakguard.main([repo("a.py", f"x = '{NUMBER_BARE}'\n")]) == 1
    header = capsys.readouterr().err.splitlines()[0]
    assert re.fullmatch(r"LEAK GUARD FAILED \(\d+ real values from ws\):", header)


def test_the_scope_names_every_workspace_and_says_why_there_is_none(monkeypatch):
    _clean_env(monkeypatch, waive=False)
    assert leakguard._scope([]) == "no real values: none found"
    assert leakguard._scope([("a", {"x": 1}), ("b", {"y": 1, "z": 1})]) == "3 real values from a, b"


def test_the_help_describes_the_command_and_each_option(capsys):
    with pytest.raises(SystemExit):
        leakguard.main(["--help"])
    text = " ".join(capsys.readouterr().out.split())
    for expected in (leakguard.__doc__.splitlines()[0], "files to check (pre-commit passes the staged ones)",
                     "check every tracked file", "check every commit, message and path in git history"):
        assert expected in text
    assert "XX" not in text


def test_a_history_run_keeps_the_failures_of_loading_the_real_values(repo, capsys, tmp_path, monkeypatch):
    repo("ok.py", "x = 1\n")
    _git("add", "-A", cwd=tmp_path)
    _git("commit", "-qm", "init", cwd=tmp_path)
    _clean_env(monkeypatch, waive=False)
    code, _, failures = run(capsys, "--history")
    assert (code, failures) == (1, [leakguard.NO_SOURCE])


def test_a_listing_with_a_duplicate_and_one_without_values_does_not_stop_at_them(repo, capsys, tmp_path, workspace):
    other = make_workspace(tmp_path / "other", owner="Ciclano Morador da Casa")
    empty = tmp_path / "empty"
    empty.mkdir()
    repo(".leakguard-workspaces", f"{workspace}\n{workspace}\n{empty}\n{other}\n")
    sources, failures = leakguard.load_real_values()
    assert [name for name, _ in sources] == ["ws", "other"]
    assert len(failures) == 1
    assert failures[0].startswith(f"REAL-VALUES: {empty} (listed in .leakguard-workspaces) yields no real value")


def test_data_that_cannot_be_read_names_the_kind_of_error(repo, tmp_path):
    broken = tmp_path / "broken"
    (broken / "data" / "raw" / "accounts" / "ingestion_date=2026-11-01").mkdir(parents=True)
    (broken / "data" / "raw" / "accounts" / "ingestion_date=2026-11-01" / "data.parquet").write_bytes(b"not parquet")
    repo(".leakguard-workspaces", f"{broken}\n")
    _, failures = leakguard.load_real_values()
    assert re.search(r"\(\w+(Error|Exception): ", failures[0])
    assert "(NoneType" not in failures[0]


# ---- git history


@pytest.fixture
def history_repo(repo, tmp_path):
    """A repository whose history holds, on a branch that was never merged and in a merge that
    resolved a conflict by writing the value, what only an audit of all of it can see."""
    def git(*args):
        return _git(*args, cwd=tmp_path)

    repo("f.txt", "base\n")
    git("add", "-A")
    git("commit", "-qm", "base")
    git("branch", "-M", "main")
    git("checkout", "-qb", "side")
    repo("f.txt", "side\n")
    git("commit", "-qam", "side change")
    git("checkout", "-q", "main")
    repo("f.txt", "main\n")
    git("commit", "-qam", "main change")
    subprocess.run(["git", "merge", "--no-commit", "side"], cwd=tmp_path, capture_output=True, check=False)   # conflicts
    repo("f.txt", f"resolved by {RECEIVER}\n")
    repo("merged.db", "")
    git("add", "-A")
    git("commit", "-qm", "merge side")
    git("checkout", "-qb", "never-merged", "HEAD~1")
    repo("g.txt", f"owner {OWNER}\n")
    repo("branch.db", "")
    git("add", "-A")
    git("commit", "-qm", "see dump.db")
    git("checkout", "-q", "main")
    return tmp_path


def test_history_reads_every_branch_merge_resolutions_and_the_paths_of_both(history_repo, capsys, workspace):
    (history_repo / ".leakguard-workspaces").write_text(f"{workspace}\n", encoding="utf-8")
    code, _, failures = run(capsys, "--history")
    assert code == 1
    assert sorted({f.rsplit(", ", 1)[1] for f in failures if f.startswith("REAL")}) == [
        "raw.accounts.owner from ws)", "raw.transactions.paymentData from ws)"]
    assert all(f.startswith("REAL: history:") for f in failures if f.startswith("REAL"))
    assert sorted(f for f in failures if f.startswith("history:")) == [
        "history: branch.db: private file was committed", "history: merged.db: private file was committed"]


def test_history_counts_lines_from_its_own_format_and_never_colours_it(monkeypatch):
    calls = []
    monkeypatch.setattr(leakguard, "_git", lambda *args, **kw: calls.append(args) or "")
    leakguard.check_history([])
    assert "--no-color" in calls[0]


def test_a_commit_message_is_the_first_line_after_its_hash(repo, capsys, tmp_path, workspace):
    (tmp_path / ".leakguard-workspaces").write_text(f"{workspace}\n", encoding="utf-8")
    repo("ok.py", "x = 1\n")
    _git("add", "ok.py", cwd=tmp_path)
    _git("commit", "-qm", f"pay {RECEIVER}", cwd=tmp_path)
    code, _, failures = run(capsys, "--history")
    assert code == 1
    assert [f.split(" ->")[0] for f in failures] == ["REAL: history:2"]
