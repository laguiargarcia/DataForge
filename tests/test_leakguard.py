"""Tests for scripts/leakguard.py, the command of the one guard against private files, personal data and
real values: its output, its exit code and how it runs as pre-commit and CI run it. The rules of
each part are tested beside it, in tests/test_leakguard_<part>.py (df-n8ac.36).

Every PII-shaped or "real" value is made up and assembled at run time (tests/leakguard_fixtures.py).
"""

import itertools
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from scripts import leakguard, leakguard_files, leakguard_patterns, leakguard_values
from tests import leakguard_fixtures
from tests.leakguard_fixtures import (
    CPF, NO_DATA, NO_SOURCE, NUMBER_BARE, OWNER, RECEIVER, _clean_env, _git, _init, make_workspace, run,
)

repo = pytest.fixture(leakguard_fixtures.repo)
workspace = pytest.fixture(leakguard_fixtures.workspace)


GUARD = Path(leakguard.__file__)
# The guard's parts beside the command (df-n8ac.36): a copy of the command needs them beside it.
GUARD_PARTS = sorted(GUARD.parent.glob("leakguard_*.py"))


# ---------------------------------------------------------------------------- a clean run


def test_clean_files_pass_and_are_counted(repo, capsys):
    files = [repo("a.py", "x = 2024\nport = 8080\n"), repo("b.md", "version 1.2.3\n")]
    code, out, failures = run(capsys, *files)
    assert (code, failures) == (0, [])
    assert out.strip() == f"leak guard: 2 file(s) clean ({NO_DATA})"


# ---------------------------------------------------------------------------- command line


def _cli_repo(tmp_path, monkeypatch):
    """A scratch repo with its own copy of the guard: its main checkout is the scratch repo, never this one."""
    if any("from mutmut." in module.read_text(encoding="utf-8") for module in [GUARD, *GUARD_PARTS]):
        # The mutation run instruments a part of the guard, and the copy imports mutmut, which cannot
        # start outside the mutation's own folder. The in-process tests hold the same rules.
        pytest.skip("guard instrumented by the mutation run: its copy cannot run as a command")
    _clean_env(monkeypatch)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    repo = _init(tmp_path / "repo")
    (repo / "scripts").mkdir()
    shutil.copy(GUARD, repo / "scripts" / "leakguard.py")
    # The parts are excluded like a gitignored file, so the scratch repo tracks the command alone, as
    # before the guard was split; _cli puts them beside the copy it runs (a worktree checks out none).
    with (repo / ".git" / "info" / "exclude").open("a", encoding="utf-8") as exclude:
        exclude.write("scripts/leakguard_*.py\n")
    return repo


def _cli(cwd, guard, *args):
    for part in GUARD_PARTS:
        shutil.copy(part, guard.parent / part.name)
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


def test_a_git_error_that_names_a_value_is_masked(repo, capsys, monkeypatch):
    said = f"fatal: pathspec 'x_{CPF}' is beyond a symbolic link"
    monkeypatch.setattr(leakguard_files.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a[0], 128, stdout="", stderr=said))
    failures = run(capsys, "--all")[2]
    assert failures == [f"GIT: {leakguard_patterns._mask(f'git ls-files failed with exit code 128: {said}')}; nothing was checked"]


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
    monkeypatch.setattr(leakguard_values, "load_real_values", lambda: ([("ws", {value: "raw.accounts.number"})], []))
    name = f"x{before}{value}{after}x".replace("\n", "_")
    rel = {"text": "notes/a.txt", "name": f"notes/{name}.txt", "private name": f"workspaces/W/data/{name}.csv"}[place]
    repo(rel, f"{before}{value}{after}\n")
    code = leakguard.main([rel])
    err = capsys.readouterr().err.lower()
    assert code == 1
    assert not [value[i:i + 3] for i in range(len(value) - 2) if value[i:i + 3] in err]


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
    assert (code, failures) == (1, [leakguard_values.NO_SOURCE])
