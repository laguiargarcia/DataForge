"""Tests for scripts/leakguard.py, the pre-commit and CI guard against PII and private paths.

Every PII-shaped value is assembled at run time, so this file never holds one and the guard
passes over it like any other committed file.
"""

import os
import re
import subprocess

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
    monkeypatch.setattr(leakguard, "WORKSPACES", tmp_path / "workspaces")   # no private guard unless a test adds one
    monkeypatch.setattr(leakguard, "MAIN_ROOT_ERROR", None)

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
    (guard_home / ".leakguard-local").write_text("# owner names\n\n  ciclano  \n#beltrano\n",
                                                 encoding="utf-8")
    found = names(leakguard._load_owner_name_patterns(), "Fulano, Ciclano, #beltrano")
    assert found == ["Fulano", "Ciclano"]


# ---------------------------------------------------------------- real values from a private workspace (df-k37o)

REAL_ACCOUNT = "98765" + "-4321"
REAL_OWNER = "fulana" + " verdadeira"
PUBLIC_BANK = "banco" + " publico s.a."

PRIVATE_GUARD = f"""
from pathlib import Path
def real_values(workspace: Path):
    assert (workspace / "tools" / "leakguard.py").is_file()
    return {{"{REAL_ACCOUNT}": "raw.accounts.number", "{REAL_OWNER}": "raw.accounts.owner",
            "{PUBLIC_BANK}": "raw.transactions.paymentData", "": "raw.x", "ab": "raw.y", 12345678: "raw.z"}}
def allowed():
    return {{"{PUBLIC_BANK}"}}
def real_value_hits(text, values):
    return sorted((n, v, s) for n, line in enumerate(text.lower().splitlines(), start=1)
                  for v, s in values.items() if v in line)
"""


def _guard(values: dict, allow=()) -> str:
    """A private guard with its own values and allow-list."""
    return f"""
def real_values(workspace):
    return {values!r}
def allowed():
    return {set(allow)!r}
def real_value_hits(text, values):
    return sorted((n, v, s) for n, line in enumerate(text.lower().splitlines(), start=1)
                  for v, s in values.items() if v in line)
"""


@pytest.fixture
def private_guard(repo):
    """A private workspace that ships tools/leakguard.py with made-up 'real' values."""
    repo("workspaces/Priv/tools/leakguard.py", PRIVATE_GUARD)
    return repo


def test_a_real_value_from_a_private_workspace_fails_without_being_echoed(private_guard, capsys):
    rel = private_guard("workspaces/Show/scripts/x.py", f"# e.g. '{REAL_ACCOUNT.upper()}' -> '4321'\n")
    code, _, failures = run(capsys, rel)
    assert code == 1
    assert failures == [f"  REAL: {rel}:1 -> '98…' ({len(REAL_ACCOUNT)} chars, raw.accounts.number from Priv)"]
    assert REAL_ACCOUNT not in "".join(failures)


def test_the_owner_name_is_attribution_in_license_and_readme_but_nothing_else_is(private_guard, capsys):
    lic = private_guard("LICENSE", f"Copyright (c) 2026 {REAL_OWNER}\n")
    readme = private_guard("README.md", f"by {REAL_OWNER}, account {REAL_ACCOUNT}\n")
    other = private_guard("notes.md", f"{REAL_OWNER}\n")
    code, _, failures = run(capsys, lic, readme, other)
    assert code == 1
    assert [f.split(" -> ")[0] for f in failures] == ["  REAL: README.md:1", "  REAL: notes.md:1"]
    assert "raw.accounts.number" in failures[0]


def test_the_owner_exemption_does_not_depend_on_which_source_came_first(repo, capsys):
    """D3: the owner's name may also be a counterparty somewhere; it is still attribution in LICENSE."""
    repo("workspaces/A/tools/leakguard.py", _guard({REAL_OWNER: "raw.transactions.paymentData"}))
    repo("workspaces/B/tools/leakguard.py", _guard({REAL_OWNER: "raw.accounts.owner"}))
    code, _, failures = run(capsys, repo("LICENSE", f"(c) {REAL_OWNER}\n"))
    assert (code, failures) == (0, [])


def test_one_workspace_allowing_a_value_does_not_unblock_it_in_another(repo, capsys):
    """D4: an allow-list filters only its own workspace's values."""
    repo("workspaces/A/tools/leakguard.py", _guard({REAL_ACCOUNT: "raw.x"}, allow=[REAL_ACCOUNT]))
    repo("workspaces/B/tools/leakguard.py", _guard({REAL_ACCOUNT: "raw.accounts.number"}))
    code, _, failures = run(capsys, repo("a.py", f"x = '{REAL_ACCOUNT}'\n"))
    assert code == 1
    assert [f.endswith("from B)") for f in failures] == [True]


def test_empty_short_and_non_string_values_from_a_private_guard_are_dropped(private_guard, capsys):
    """D9: '' would match every line, a 2-character value would be shown whole by the mask."""
    code, _, failures = run(capsys, private_guard("a.py", "ab cd 12345678\n"))
    assert (code, failures) == (0, [])


def test_a_value_the_private_workspace_allows_passes(private_guard, capsys):
    code, _, _ = run(capsys, private_guard("a.py", f"issuer = '{PUBLIC_BANK.upper()}'\n"))
    assert code == 0


def test_a_private_guard_that_cannot_run_fails_the_check(repo, capsys):
    repo("workspaces/Priv/tools/leakguard.py", "import a_module_that_is_not_installed\n")
    code, _, failures = run(capsys, repo("a.py", "x = 1\n"))
    assert code == 1
    assert len(failures) == 1
    assert failures[0].startswith("  REAL-VALUES: workspaces/Priv/tools/leakguard.py could not run")


def test_a_private_guard_without_the_public_api_fails_the_check(repo, capsys):
    repo("workspaces/Priv/tools/leakguard.py", "def real_values(ws):\n    return {}\n")
    code, _, failures = run(capsys, repo("a.py", "x = 1\n"))
    assert code == 1
    assert "AttributeError" in failures[0]


def test_a_worktree_without_its_main_checkout_fails_the_check(repo, capsys, monkeypatch):
    """D2: without the main checkout the private workspaces are not there; that must not pass."""
    monkeypatch.setattr(leakguard, "MAIN_ROOT_ERROR", "this is a worktree and its main checkout could not be found")
    code, _, failures = run(capsys, repo("a.py", "x = 1\n"))
    assert code == 1
    assert failures == ["  REAL-VALUES: this is a worktree and its main checkout could not be found"]


def test_without_a_private_guard_there_is_no_real_value_check(repo, capsys):
    code, out, _ = run(capsys, repo("a.py", f"x = '{REAL_ACCOUNT}'\n"))
    assert code == 0
    assert out.strip() == "leak guard: 1 file(s) clean"


@pytest.fixture
def clean_git(tmp_path, monkeypatch):
    """D5: no inherited GIT_* (a git hook exports them) and no repo above tmp_path."""
    for name in [n for n in os.environ if n.startswith("GIT_")]:
        monkeypatch.delenv(name)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
    return lambda *args, cwd: subprocess.run([*git, *args], cwd=cwd, check=True, capture_output=True)


def test_from_a_worktree_the_main_checkout_is_found(tmp_path, clean_git):
    """D1: commits are made from worktrees, where gitignored files (private workspaces,
    .leakguard-local) do not exist."""
    main = tmp_path / "main"
    main.mkdir()
    (main / "README.md").write_text("x\n", encoding="utf-8")
    clean_git("init", "-q", cwd=main)
    clean_git("add", "-A", cwd=main)
    clean_git("commit", "-qm", "init", cwd=main)
    wt = tmp_path / "wt"
    clean_git("worktree", "add", "-q", str(wt), cwd=main)
    assert leakguard._main_checkout(wt)[0].resolve() == main.resolve()
    assert leakguard._main_checkout(wt)[1] is None
    assert leakguard._main_checkout(main)[0].resolve() == main.resolve()


def test_a_worktree_whose_main_checkout_is_gone_is_an_error(tmp_path, clean_git):
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: /nonexistent/main/.git/worktrees/wt\n", encoding="utf-8")
    root, error = leakguard._main_checkout(wt)
    assert root == wt
    assert error == ("this is a worktree and its main checkout could not be found, so the private "
                     "workspaces and .leakguard-local were not checked")


def test_outside_a_git_repo_the_root_is_local_and_not_an_error(tmp_path, clean_git):
    assert leakguard._main_checkout(tmp_path) == (tmp_path, None)


def test_leakguard_local_is_read_from_the_main_checkout(guard_home):
    (guard_home / ".leakguard-local").write_text("fulano\n", encoding="utf-8")
    assert names(leakguard._load_owner_name_patterns(), "Fulano") == ["Fulano"]


def test_a_value_of_exactly_the_minimum_length_is_kept(repo, capsys):
    six = "ab" + "c123"
    repo("workspaces/A/tools/leakguard.py", _guard({six: "raw.x", six[:5]: "raw.y"}))
    code, _, failures = run(capsys, repo("a.py", f"x = '{six}'\n"))
    assert code == 1
    assert [f.split(" -> ")[1][:4] for f in failures] == ["'ab…"]


def test_a_failing_private_guard_does_not_stop_the_others(repo, capsys):
    repo("workspaces/A/tools/leakguard.py", "import a_module_that_is_not_installed\n")
    repo("workspaces/B/tools/leakguard.py", _guard({REAL_ACCOUNT: "raw.accounts.number"}))
    code, _, failures = run(capsys, repo("a.py", f"x = '{REAL_ACCOUNT}'\n"))
    assert code == 1
    assert failures[0].startswith("  REAL-VALUES: workspaces/A/tools/leakguard.py could not run")
    assert failures[1].endswith("raw.accounts.number from B)")
