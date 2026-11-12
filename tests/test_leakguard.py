"""Tests for scripts/leakguard.py, the pre-commit and CI guard against PII and private paths.

Every PII-shaped value is assembled at run time, so this file never holds one and the guard
passes over it like any other committed file.
"""

import os
import re

import pytest

from scripts import leakguard

CPF_PLAIN = "529" + "98224725"
CPF_PUNCT = "529.982" + ".247-25"
SYNTHETIC_CPF_PUNCT = "111.222" + ".333-44"   # the allowlisted fake, punctuated
ACCT = "1234" + "-5"
EMAIL = "someone" + "@" + "gmail.com"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """Run the guard from a scratch repo root, with one made-up owner name pattern."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(leakguard, "NAME_PATTERNS", [re.compile(r"fulano\s+de\s+tal", re.IGNORECASE)])

    def write(rel, text):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
        return rel

    return write


def run(capsys, *files):
    code = leakguard.main(["leakguard.py", *files])
    out, err = capsys.readouterr()
    return code, out, err.splitlines()[1:]


def test_clean_files_pass_and_are_counted(repo, capsys):
    files = [repo("a.py", "x = 2024\nport = 8080\n"), repo("b.md", "version 1.2.3\n")]
    code, out, failures = run(capsys, *files)
    assert (code, failures) == (0, [])
    assert out.strip() == "leak guard: 2 file(s) clean"


def test_a_private_path_is_blocked_without_reading_it(repo, capsys):
    """Blocked by its path alone: it need not exist, and its content adds no second failure."""
    rel = repo("workspaces/GetFinance-Dev/x.py", f"cpf = '{CPF_PLAIN}'\n")
    code, _, failures = run(capsys, rel, "docs/missing.md")
    assert code == 1
    assert failures == ["  PATH-BLOCK: workspaces/GetFinance-Dev/x.py is under a private surface",
                        "  PATH-BLOCK: docs/missing.md is under a private surface"]


@pytest.mark.parametrize(("label", "value"), [("CPF", CPF_PLAIN), ("CPF", CPF_PUNCT),
                                              ("ACCT", ACCT), ("EMAIL", EMAIL)])
def test_pii_in_content_fails_with_its_label(repo, capsys, label, value):
    code, _, failures = run(capsys, repo("notes.txt", f"before {value} after\n"))
    assert code == 1
    assert failures == [f"  {label}: notes.txt -> {value!r}"]


@pytest.mark.parametrize("value", sorted(leakguard.ALLOWLIST) + [SYNTHETIC_CPF_PUNCT])
def test_synthetic_literals_are_exempt_bare_or_punctuated(repo, capsys, value):
    assert run(capsys, repo("gen.py", f"doc = '{value}'\n"))[0] == 0


def test_an_owner_name_is_blocked_except_where_attribution_is_intended(repo, capsys):
    text = "Copyright Fulano de Tal\n"
    code, _, failures = run(capsys, repo("src.py", text), repo("README.md", text), repo("LICENSE", text))
    assert code == 1
    assert failures == ["  NAME: src.py -> 'Fulano de Tal'"]


def test_attribution_files_are_still_checked_for_other_pii(repo, capsys):
    assert run(capsys, repo("README.md", f"mail me at {EMAIL}\n"))[2] == [f"  EMAIL: README.md -> {EMAIL!r}"]


def test_binaries_and_missing_files_are_skipped(repo, capsys):
    code, out, _ = run(capsys, repo("data/report.xlsx", f"{CPF_PLAIN}\n"), "gone.py")
    assert code == 0
    assert out.strip() == "leak guard: 2 file(s) clean"


def test_every_hit_in_every_file_is_reported(repo, capsys):
    a = repo("a.txt", f"{ACCT} and {EMAIL}\n")
    b = repo("b.txt", f"{CPF_PLAIN}\n")
    assert run(capsys, a, b)[2] == [f"  ACCT: a.txt -> {ACCT!r}", f"  EMAIL: a.txt -> {EMAIL!r}",
                                    f"  CPF: b.txt -> {CPF_PLAIN!r}"]


def test_files_skipped_on_the_way_do_not_end_the_scan(repo, capsys, tmp_path):
    """A blocked, missing, binary or unreadable file is passed over; the files after it are
    still scanned."""
    unreadable = repo("locked.txt", "x\n")
    (tmp_path / unreadable).chmod(0)
    try:
        failures = run(capsys, "docs/a.md", "gone.py", repo("r.xlsx", "x"), unreadable,
                       repo("last.txt", f"{CPF_PLAIN}\n"))[2]
    finally:
        (tmp_path / unreadable).chmod(0o644)
    assert failures[-1] == f"  CPF: last.txt -> {CPF_PLAIN!r}"


def test_an_exempt_literal_does_not_hide_a_real_hit_after_it(repo, capsys):
    synthetic = sorted(leakguard.ALLOWLIST)[0]
    assert run(capsys, repo("mix.txt", f"{synthetic} {CPF_PLAIN}\n"))[2] == [f"  CPF: mix.txt -> {CPF_PLAIN!r}"]


def test_bytes_that_are_not_utf8_do_not_hide_the_rest_of_the_file(repo, capsys, tmp_path):
    (tmp_path / "latin.txt").write_bytes(b"caf\xe9 " + CPF_PLAIN.encode() + b"\n")
    assert run(capsys, "latin.txt")[2] == [f"  CPF: latin.txt -> {CPF_PLAIN!r}"]


# ---------------------------------------------------------------------------- owner name patterns


@pytest.fixture
def guard_home(tmp_path, monkeypatch):
    """Point the guard at a scratch repo root (its .leakguard-local lives there) with no env."""
    monkeypatch.setattr(leakguard, "__file__", str(tmp_path / "scripts" / "leakguard.py"))
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
    (guard_home / ".leakguard-local").write_text("# owner names\n\n  ciclano  \n#beltrano\n",
                                                 encoding="utf-8")
    found = names(leakguard._load_owner_name_patterns(), "Fulano, Ciclano, #beltrano")
    assert found == ["Fulano", "Ciclano"]
