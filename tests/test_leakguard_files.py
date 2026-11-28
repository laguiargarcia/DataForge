"""Tests for scripts/leakguard_files.py: private paths, how files and paths are read and checked, and
what git says (ignored files, tracked files, the whole history).

Every PII-shaped or "real" value is made up and assembled at run time (tests/leakguard_fixtures.py).
"""

import re
import subprocess
from pathlib import Path

import pytest

from scripts import leakguard_files, leakguard_patterns, leakguard_values
from tests import leakguard_fixtures
from tests.leakguard_fixtures import AMOUNT, CNPJ, CPF, EMAIL, NUMBER_BARE, OWNER, RECEIVER, _git, run

repo = pytest.fixture(leakguard_fixtures.repo)
workspace = pytest.fixture(leakguard_fixtures.workspace)


# ---------------------------------------------------------------------------- private files


def test_a_private_surface_is_blocked_without_reading_it(repo, capsys):
    """Blocked by its path alone: it need not exist, and its content adds no second failure."""
    rel = repo("workspaces/GetFinance-Dev/x.py", f"cpf = '{CPF}'\n")
    code, _, failures = run(capsys, rel, "docs/superpowers/missing.md")
    assert code == 1
    assert failures == ["PATH-BLOCK: workspaces/GetFinance-Dev/x.py is under a private surface",
                        "PATH-BLOCK: docs/superpowers/missing.md is under a private surface"]


@pytest.mark.parametrize("rel", ["docs/superpowers/specs/s.md", "docs/phases/1.2-x.md", "docs/gsd-workflow.md",
                                 "docs/workbook-handoff.md", "Docs/Superpowers/plans/p.md", "DOCS/PHASES/x.md"])
def test_the_history_docs_stay_blocked(repo, capsys, rel):
    """df-azm6: docs/ is versioned, but its history (superpowers/, phases/, gsd-workflow.md) and the
    owner-only workbook note hold real values and stay private, whatever the case of the path."""
    assert run(capsys, rel)[2] == [f"PATH-BLOCK: {rel} is under a private surface"]


@pytest.mark.parametrize("rel", ["docs/commands.md", "docs/subsystems/api.md", "docs/adr/0005-x.md",
                                 "docs/superpowers.md", "docs/phases-of-the-moon.md"])
def test_the_reference_docs_are_read_not_blocked(repo, capsys, rel):
    """df-azm6: a reference doc is not refused by its path; its content is checked like any file's."""
    assert run(capsys, repo(rel, "x = 1\n"))[:1] == (0,)
    assert run(capsys, repo(rel, f"x = '{CPF}'\n"))[2] == [f"CPF: {rel}:1 -> '52…' (11 chars)"]


ROOT = Path(__file__).resolve().parent.parent


def _ignored_by_this_repo(rel):
    return subprocess.run(["git", "check-ignore", "-q", "--no-index", rel], cwd=ROOT, check=False).returncode == 0


def test_this_repos_gitignore_keeps_out_what_the_guard_blocks():
    """df-azm6: the docs/ lines of the repo's own .gitignore and the docs/ entries of BLOCK_PREFIXES
    are the same paths, so a history path added to one side only fails here; and git does ignore
    each of them, while it lets the reference docs in."""
    ignored = {line.strip().lstrip("/") for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
               if line.strip().startswith("/docs/")}
    blocked = {p for p in leakguard_files.BLOCK_PREFIXES if p.startswith("docs/")}
    assert ignored == blocked
    for prefix in blocked:
        assert _ignored_by_this_repo(prefix + "x.md" if prefix.endswith("/") else prefix), prefix
    for rel in ("docs/commands.md", "docs/subsystems/api.md"):
        assert not _ignored_by_this_repo(rel), rel
        assert not rel.startswith(leakguard_files.BLOCK_PREFIXES), rel


@pytest.mark.parametrize("rel", ["workspaces/GetFinance/data/raw/x.csv", "workspaces/GetFinance/report/r.txt",
                                 "workspaces/W/docs/archive/old.md", "workspaces/W/docs/workbook.md",
                                 "workspaces/W/config/investments.yaml",
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


def test_a_crlf_file_reports_the_same_finding_as_an_lf_one(repo, capsys, tmp_path):
    """A Windows checkout writes CRLF: a name split across lines measures the same, on the same line."""
    (tmp_path / "src.py").write_bytes(b"Copyright\r\nFulano de\r\n  Tal\r\n")
    code, _, failures = run(capsys, "src.py")
    assert code == 1
    assert failures == ["NAME: src.py:2 -> 'Fu…' (15 chars)"]


def test_files_skipped_on_the_way_do_not_end_the_scan(repo, capsys, tmp_path):
    """A blocked, missing or unreadable file is passed over; the files after it are still scanned."""
    unreadable = repo("locked.txt", "x\n")
    (tmp_path / unreadable).chmod(0)
    try:
        failures = run(capsys, "docs/phases/a.md", "gone.py", unreadable, repo("last.txt", f"{CPF}\n"))[2]
    finally:
        (tmp_path / unreadable).chmod(0o644)
    assert failures == ["PATH-BLOCK: docs/phases/a.md is under a private surface", "CPF: last.txt:1 -> '52…' (11 chars)"]


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


def test_inside_a_workspace_only_a_path_under_workspaces_has_an_inside():
    assert leakguard_files._inside_workspace("src/a/b.txt") is None
    assert leakguard_files._inside_workspace("workspaces/W") is None
    assert leakguard_files._inside_workspace("Workspaces/W/Data/x") == "data/x"


def test_an_archive_in_a_nested_folder_outside_a_workspace_is_read(repo, capsys, tmp_path):
    (tmp_path / "tests/fixtures").mkdir(parents=True)
    (tmp_path / "tests/fixtures/data.zip").write_bytes(b"PK\x03\x04 " + CPF.encode())
    assert run(capsys, "tests/fixtures/data.zip")[2] == ["CPF: tests/fixtures/data.zip:1 -> '52…' (11 chars)"]


def test_the_name_of_a_path_is_its_last_part_only():
    assert leakguard_files._private_reason("a/b/c/x.db", set()) is not None
    assert leakguard_files._private_reason("a/b/c/.env.example", set()) is None
    assert leakguard_files._private_reason("a/b.env/c.txt", set()) is None


# ---- _decode


def test_decode_reads_utf8_cp1252_and_utf16_and_never_raises():
    assert leakguard_files._decode("ação\r\nx".encode()) == "ação\nx"
    assert leakguard_files._decode(b"a\xe7\xe3o") == "a\xe7\xe3o"   # cp1252
    assert leakguard_files._decode(b"\x81") == "�"   # a byte cp1252 does not define
    assert leakguard_files._decode(b"\xff\xfe" + "a\r\n".encode("utf-16-le")) == "a\n"
    assert leakguard_files._decode(b"\xfe\xff" + "b".encode("utf-16-be")) == "b"
    assert leakguard_files._decode(b"\xff\xfe" + b"a\x00" + b"\x00\xd8") == "a�"   # a lone surrogate
    assert leakguard_files._decode(b"\xff\xfe" + b"a\x00" + b"b") == "a�"   # an odd byte count


# ---- the files .gitignore excludes


def test_gitignored_names_the_excluded_files_even_when_tracked_and_whatever_their_name(repo, tmp_path):
    repo(".gitignore", "secret-notes/\n*.tmp\n")
    repo("secret-notes/a.md", "ok\n")
    repo("relatório.tmp", "ok\n")
    repo("ok.py", "x = 1\n")
    _git("add", "-f", "secret-notes/a.md", "relatório.tmp", "ok.py", ".gitignore", cwd=tmp_path)
    assert leakguard_files._gitignored(["secret-notes/a.md", "ok.py", "relatório.tmp", "new\nline.tmp"]) == {
        "secret-notes/a.md", "relatório.tmp", "new\nline.tmp"}
    assert leakguard_files._gitignored([]) == set()


def test_a_force_added_ignored_file_is_private_in_process(repo, capsys, tmp_path):
    repo(".gitignore", "secret-notes/\n")
    repo("secret-notes/a.md", "ok\n")
    _git("add", "-f", "secret-notes/a.md", cwd=tmp_path)
    assert run(capsys, "secret-notes/a.md", ".gitignore")[2] == [
        "PRIVATE: secret-notes/a.md is a private file (data, report, .env, real config, a data artifact or "
        "gitignored)"]
    assert run(capsys, "--all")[2] == run(capsys, "secret-notes/a.md")[2]


def test_git_feeds_its_input_and_decodes_whatever_it_prints(repo, tmp_path):
    assert leakguard_files._git("hash-object", "--stdin", stdin="hello").strip() == "b6fc4c620b67d95f953a5c1c1230aaab5db5a1b0"
    blob = tmp_path / "latin.txt"
    blob.write_bytes(b"a\xe7b\n")
    sha = leakguard_files._git("hash-object", "-w", "latin.txt").strip()
    assert leakguard_files._git("cat-file", "blob", sha) == "a�b\n"


def test_git_reads_utf8_and_a_failing_command_is_an_error_that_says_why(monkeypatch):
    calls = []
    original = subprocess.run
    monkeypatch.setattr(leakguard_files.subprocess, "run", lambda *a, **k: calls.append(k) or original(*a, **k))
    with pytest.raises(leakguard_files.GitError, match=r"^git no-such-subcommand failed with exit code 1: git: "):
        leakguard_files._git("no-such-subcommand")
    assert calls[0]["encoding"] == "utf-8"
    assert leakguard_files._git("no-such-subcommand", ok=(1,)) == ""
    monkeypatch.setattr(leakguard_files.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a[0], 3, stdout="", stderr=""))
    with pytest.raises(leakguard_files.GitError, match=r"^git status failed with exit code 3$"):
        leakguard_files._git("status")


def test_a_git_error_says_only_the_first_line_git_wrote(repo, monkeypatch):
    monkeypatch.setenv("LC_ALL", "C")   # git's messages in English, whatever the machine's language
    monkeypatch.setenv("LANGUAGE", "C")
    with pytest.raises(leakguard_files.GitError) as raised:
        leakguard_files._git("log", "nosuchref")
    assert str(raised.value) == ("git log failed with exit code 128: fatal: ambiguous argument 'nosuchref': unknown "
                                 "revision or path not in the working tree.")


def test_git_that_cannot_run_is_an_error(monkeypatch):
    monkeypatch.setenv("PATH", "")
    with pytest.raises(leakguard_files.GitError, match=r"^git status could not run \(FileNotFoundError: "):
        leakguard_files._git("status")


def test_a_file_outside_the_repository_is_checked_and_never_ignored(repo, capsys, tmp_path, workspace,
                                                                    monkeypatch):
    """pre-commit's commit-msg hook passes a worktree's message file, which lives in the main checkout's .git/."""
    monkeypatch.setenv("GETFINANCE_DATA", str(workspace / "data"))
    outside = tmp_path.parent / f"{tmp_path.name}-msg" / "COMMIT_EDITMSG"
    outside.parent.mkdir()
    outside.write_text(f"Pay {RECEIVER} back\n", encoding="utf-8")
    repo(".gitignore", "*.tmp\n")
    repo("a.tmp", "ok\n")
    assert leakguard_files._gitignored([str(outside), "a.tmp"]) == {"a.tmp"}
    assert leakguard_files._gitignored([str(outside)]) == set()
    code, _, failures = run(capsys, str(outside))
    assert code == 1
    assert [f.split(" -> ")[1].rsplit(", ", 1)[1] for f in failures] == ["raw.transactions.paymentData from ws)"]


@pytest.mark.parametrize(("path", "outside"), [
    ("a.py", False), ("sub/../a.py", False), ("../a.py", True), ("{top}/a.py", False), ("{top}-other/a.py", True),
    ("{top}/../a.py", True)])
def test_outside_the_repository_is_judged_by_the_spelling_of_the_path(tmp_path, monkeypatch, path, outside):
    monkeypatch.chdir(tmp_path)   # a relative path is relative to the cwd, here the repository's top
    assert leakguard_files._outside(path.format(top=tmp_path), tmp_path) is outside


def test_two_spellings_of_the_same_directory_are_the_same_repository(tmp_path):
    """git prints the long, resolved name; a caller may spell it another way (a link, a Windows short name)."""
    link = tmp_path.parent / f"{tmp_path.name}-link"
    try:
        link.symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip("this machine cannot create a symlink")
    assert leakguard_files._outside(str(link / "a.py"), tmp_path) is False
    assert leakguard_files._outside(str(tmp_path / "a.py"), link) is False


def test_a_force_added_ignored_symlink_that_points_outside_is_still_private(repo, capsys, tmp_path):
    """A data/ linked to another disk: following the link would put it outside and skip check-ignore."""
    elsewhere = tmp_path.parent / f"{tmp_path.name}-elsewhere"
    elsewhere.mkdir()
    repo(".gitignore", "links/\n")
    (tmp_path / "links").mkdir()
    try:
        (tmp_path / "links" / "data").symlink_to(elsewhere, target_is_directory=True)
    except OSError:
        pytest.skip("this machine cannot create a symlink")
    _git("add", "-f", "links/data", cwd=tmp_path)
    assert run(capsys, "links/data")[2] == [f"PRIVATE: links/data is a {leakguard_files.PRIVATE_FILE}"]


def test_from_a_subdirectory_an_absolute_path_in_the_repository_is_still_asked(repo, capsys, tmp_path, monkeypatch):
    repo(".gitignore", "*.tmp\n")
    repo("sub/keep.md", "ok\n")
    repo("a.tmp", "ok\n")
    _git("add", "-f", "a.tmp", cwd=tmp_path)
    monkeypatch.chdir(tmp_path / "sub")
    assert run(capsys, str(tmp_path / "a.tmp"))[2] == [
        f"PRIVATE: {(tmp_path / 'a.tmp').as_posix()} is a {leakguard_files.PRIVATE_FILE}"]


def test_history_finds_a_real_value_in_a_non_ascii_file_name(repo, capsys, tmp_path, monkeypatch):
    """Quoted, git would write the name as octal escapes and the value in it would not be seen."""
    value = "joão" + "zinho inventado"
    monkeypatch.setattr(leakguard_values, "load_real_values", lambda: ([("ws", {value: "raw.accounts.owner"})], []))
    repo(f"notes/{value}.md", "ok\n")
    _git("add", "-A", cwd=tmp_path)
    _git("commit", "-qm", "add", cwd=tmp_path)
    _git("rm", "-q", f"notes/{value}.md", cwd=tmp_path)
    _git("commit", "-qm", "remove", cwd=tmp_path)
    failures = run(capsys, "--history")[2]
    assert failures
    assert {f.split(":", 1)[0] for f in failures} == {"REAL"}


def test_a_private_surface_named_after_a_document_is_masked(repo, capsys):
    rel = f"docs/superpowers/{CPF}.md"
    assert run(capsys, rel)[2][1] == f"PATH-BLOCK: 'do…' ({len(rel)} chars) is under a private surface"


def test_gitignored_with_git_failing_is_an_error_not_an_empty_set(repo, monkeypatch):
    monkeypatch.setenv("PATH", "")
    with pytest.raises(leakguard_files.GitError):
        leakguard_files._gitignored(["a.py"])


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
                        f"PRIVATE: 'wo…' ({len(rel)} chars) is a {leakguard_files.PRIVATE_FILE}"]


def test_a_binary_file_named_after_a_document_is_reported_with_its_path_masked(repo, capsys, tmp_path):
    rel = f"workspaces/W/scripts/{CPF}.bin"
    (tmp_path / rel).parent.mkdir(parents=True)
    (tmp_path / rel).write_bytes(b"\0\1")
    failures = run(capsys, rel)[2]
    assert failures[1] == f"PRIVATE: 'wo…' ({len(rel)} chars) is a {leakguard_files.BINARY_IN_WORKSPACE}"


def test_a_path_with_no_finding_is_shown_as_it_is():
    assert leakguard_files._path_failures("workspaces/W/data/x.csv", []) == ("workspaces/W/data/x.csv", [])


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


def test_a_missing_or_unreadable_file_is_skipped_and_a_binary_one_in_a_workspace_does_not_stop_the_scan(
        repo, capsys, tmp_path):
    (tmp_path / "workspaces/W").mkdir(parents=True)
    (tmp_path / "workspaces/W/dump.bak").write_bytes(b"x\x00y")
    repo("last.txt", f"cpf {CPF}\n")
    assert run(capsys, "workspaces/W/dump.bak", "gone.txt", "last.txt")[2] == [
        "PRIVATE: workspaces/W/dump.bak is a binary file inside a workspace (a dump or a backup cannot be read "
        "for real values)", "CPF: last.txt:1 -> '52…' (11 chars)"]


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
    assert all(f.startswith("REAL: history ") for f in failures if f.startswith("REAL"))
    assert sorted(f for f in failures if f.startswith("history:")) == [
        "history: branch.db: private file was committed", "history: merged.db: private file was committed"]


def test_history_counts_lines_from_its_own_format_and_never_colours_it(monkeypatch):
    calls = []
    monkeypatch.setattr(leakguard_files, "_git", lambda *args, **kw: calls.append(args) or  ("" if kw.get("text", True) else b""))
    leakguard_files.check_history([])
    assert "--no-color" in calls[0]


def test_a_commit_message_is_the_first_line_after_its_hash(repo, capsys, tmp_path, workspace):
    (tmp_path / ".leakguard-workspaces").write_text(f"{workspace}\n", encoding="utf-8")
    repo("ok.py", "x = 1\n")
    _git("add", "ok.py", cwd=tmp_path)
    _git("commit", "-qm", f"pay {RECEIVER}", cwd=tmp_path)
    code, _, failures = run(capsys, "--history")
    assert code == 1
    assert [f.split(" ->")[0] for f in failures] == ["REAL: history " + _head(tmp_path) + " msg:1"]


def test_history_reads_what_git_takes_for_binary(repo, capsys, tmp_path):
    """df-wkay: a text with a NUL and a UTF-16 one (PowerShell `>`) are 'Binary files differ' in a plain
    log; the audit reads them as the staged check does, after a later commit removed them."""
    repo("ok.py", "x = 1\n")
    (tmp_path / "stray.txt").write_bytes(b"header\x00\n" + EMAIL.encode() + b"\n")
    (tmp_path / "u16.txt").write_bytes(f"x\nFulano de Tal\n{CPF}\n".encode("utf-16"))
    (tmp_path / "u16be.txt").write_bytes(b"\xfe\xff" + f"{CPF}\n".encode("utf-16-be"))
    (tmp_path / "copy.txt").write_bytes(b"\xfe\xff" + f"{CPF}\n".encode("utf-16-be"))   # the same version twice
    _git("add", "-A", cwd=tmp_path)
    _git("commit", "-qm", "add", cwd=tmp_path)
    _git("rm", "-q", "stray.txt", "u16.txt", "u16be.txt", "copy.txt", cwd=tmp_path)
    _git("commit", "-qm", "remove", cwd=tmp_path)
    assert run(capsys, "--all")[2] == []
    failures = run(capsys, "--history")[2]
    emails = [f for f in failures if f.startswith("EMAIL: history ")]   # in the log: the commit that adds, the one that removes
    assert [f.partition(" -> ")[2] for f in emails] == ["'so…' (17 chars)"] * 2
    assert [f for f in failures if f.startswith(("CPF: history: ", "NAME: history: "))] == [
        "CPF: history: copy.txt:1 -> '52…' (11 chars)", "CPF: history: u16.txt:3 -> '52…' (11 chars)",
        "NAME: history: u16.txt:2 -> 'Fu…' (13 chars)"]


def _head(tmp_path):
    return _git("rev-parse", "--short=7", "HEAD", cwd=tmp_path).stdout.decode().strip()


NONCE = "0123456789abcdef"


def _log(*commits):
    """The log as check_history reads it: (hash, message, diff) of each commit, framed as git writes it."""
    return "".join(f"{NONCE}C {sha}\n{NONCE}M\n{message}\n\n{NONCE}E\n{diff}" for sha, message, diff in commits)


def _found(log, sources):
    return leakguard_files._history_failures(log, sources, NONCE)


SHA = "a" * 40
FAKE_NAME = "Fulano" + "Zeta"


def _diff(path, *lines, hunk="@@ -1 +1 @@"):
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n{hunk}\n" + "".join(f"{x}\n" for x in lines)


@pytest.fixture
def named(monkeypatch):
    monkeypatch.setattr(leakguard_patterns, "NAME_PATTERNS", [re.compile(FAKE_NAME, re.IGNORECASE)])


def test_a_hunk_header_range_is_not_read_as_an_amount_but_its_context_text_is():
    sources = [("ws", {AMOUNT: "raw.transactions.amount", RECEIVER.lower(): "raw.transactions.paymentData"})]
    header = f"@@ -1,2 +{AMOUNT.replace('.', ',')} @@"
    assert _found(_log((SHA, "m", _diff("f.py", "+x", hunk=header))), sources) == []
    in_context = _found(
        _log((SHA, "m", _diff("f.py", "+x", hunk=f"{header} pay {RECEIVER} now"))), sources)
    assert len(in_context) == 1
    assert in_context[0].startswith(f"REAL: history {SHA[:7]} f.py:4 -> ")


def test_the_login_of_a_merge_message_is_let_through_but_not_the_rest_of_its_line(named):
    merge = f"Merge pull request #12 from {FAKE_NAME}/fix/x"
    assert _found(_log((SHA, merge, "")), []) == []
    other = f"Merge pull request #12 from other/{FAKE_NAME}-branch"
    assert [f.split(" ->")[0] for f in _found(_log((SHA, other, "")), [])] == [
        f"NAME: history {SHA[:7]} msg:1"]
    assert _found(_log((SHA, f"thanks {FAKE_NAME}", "")), [])


def test_the_attribution_files_and_the_lock_are_exempt_as_in_check_files(named):
    diff = _diff("LICENSE", f"+(c) {FAKE_NAME}") + _diff("uv.lock", f'+hash = "{CPF}"')
    assert _found(_log((SHA, "m", diff)), []) == []
    elsewhere = _diff("docs/LICENSE", f"+(c) {FAKE_NAME}") + _diff("lock.txt", f"+{CPF}")
    assert [f.split(" ->")[0] for f in _found(_log((SHA, "m", elsewhere)), [])] == [
        f"NAME: history {SHA[:7]} docs/LICENSE:5", f"CPF: history {SHA[:7]} lock.txt:5"]


def test_a_message_accepted_by_decision_is_let_through_but_its_diff_is_read(named, monkeypatch):
    monkeypatch.setitem(leakguard_files.ACCEPTED_MESSAGES, SHA, "df-test")
    failures = _found(_log((SHA, f"by {FAKE_NAME}", _diff("f.py", f"+{FAKE_NAME}"))), [])
    assert [f.split(" ->")[0] for f in failures] == [f"NAME: history {SHA[:7]} f.py:5"]
    assert _found(_log(("b" * 40, f"by {FAKE_NAME}", "")), [])


def test_a_real_value_in_a_normal_diff_line_and_in_another_commits_message_is_found(named):
    failures = _found(
        _log((SHA, "ok", _diff("f.py", f"+owner = '{FAKE_NAME}'")), ("b" * 40, f"pay {FAKE_NAME}", "")), [])
    assert [f.split(" ->")[0] for f in failures] == [f"NAME: history {SHA[:7]} f.py:5", f"NAME: history {'b' * 7} msg:1"]


def test_the_same_finding_of_a_merge_shown_once_per_parent_is_reported_once(named):
    entry = (SHA, "merge", _diff("f.py", f"+{FAKE_NAME}"))
    assert len(_found(_log(entry, entry), [])) == 1


def test_a_path_with_a_finding_is_masked_in_the_history_location(named):
    failures = _found(_log((SHA, "m", _diff(f"{FAKE_NAME}.txt", "+x"))), [])
    assert failures
    assert not [f for f in failures if FAKE_NAME in f]


def test_a_merge_login_is_let_through_only_in_the_subject(named):
    body = f"subject\n\nMerge pull request #3 from {FAKE_NAME}/x"
    assert [f.split(" ->")[0] for f in _found(_log((SHA, body, "")), [])] == [f"NAME: history {SHA[:7]} msg:3"]


def test_a_path_is_the_one_of_the_plus_lines_not_what_follows_a_b_in_a_name(named):
    odd = f"diff --git a/notes b/LICENSE b/notes b/LICENSE\n--- a/notes b/LICENSE\n+++ b/notes b/LICENSE\n@@ -1 +1 @@\n+(c) {FAKE_NAME}\n"
    assert [f.split(" ->")[0] for f in _found(_log((SHA, "m", odd)), [])] == [f"NAME: history {SHA[:7]} notes b/LICENSE:5"]
    gone = f"diff --git a/LICENSE b/LICENSE\n--- a/LICENSE\n+++ /dev/null\n@@ -1 +0,0 @@\n-(c) {FAKE_NAME}\n"
    assert _found(_log((SHA, "m", gone)), []) == []


def test_a_section_with_no_path_line_is_read_with_no_exemption(named):
    rename = f"diff --git a/old b/LICENSE\nsimilarity index 100%\nrename from {FAKE_NAME}\nrename to LICENSE\n"
    assert [f.split(" ->")[0] for f in _found(_log((SHA, "m", rename)), [])] == [f"NAME: history {SHA[:7]} LICENSE:3"]


def test_the_header_of_an_exempt_file_is_read_but_its_hunks_are_not(named):
    header = f"diff --git a/old b/LICENSE\nrename from {FAKE_NAME}\nrename to LICENSE\n--- a/old\n+++ b/LICENSE\n"
    failures = _found(_log((SHA, "m", header + f"@@ -1 +1 @@\n+(c) {FAKE_NAME}\n")), [])
    assert [f.split(" ->")[0] for f in failures] == [f"NAME: history {SHA[:7]} LICENSE:2"]


def test_text_before_the_first_file_is_read_and_findings_come_with_a_masked_path_once(named):
    failures = _found(_log((SHA, "m", f"stray {FAKE_NAME}\n" + _diff("f.py", "+x"))), [])
    assert [f.split(" ->")[0] for f in failures] == [f"NAME: history {SHA[:7]} diff:1"]


def test_history_keeps_the_frame_bytes_of_the_old_format_inside_content(repo, capsys, tmp_path, named):
    """A binary with the control bytes git printed as frame, and a message with one, hide nothing."""
    (tmp_path / "b.bin").write_bytes(b"\x01\x02\x03\n" + EMAIL.encode() + b"\n")
    repo("ok.py", "x = 1\n")
    _git("add", "-A", cwd=tmp_path)
    _git("commit", "-qm", f"msg \x03 {FAKE_NAME}", cwd=tmp_path)
    failures = run(capsys, "--history")[2]
    assert [f.partition(" -> ")[0].rpartition(":")[0] for f in failures if f.startswith("EMAIL")] == [
        f"EMAIL: history {_head(tmp_path)} b.bin"]
    assert [f for f in failures if f.startswith("NAME: history ") and "msg:" in f]


def test_a_blanked_text_keeps_its_length_and_its_lines():
    assert leakguard_files._blank(re.match(r"[^\n]+\n?[^\n]*", "ab c\nde")) == "    \n  "


def test_a_path_is_taken_whole_from_its_plus_or_minus_line(named):
    """The tab git adds after a path with a space is cut; a trailing space or letter belongs to the name."""
    def section(path):
        return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n+(c) {FAKE_NAME}\n"
    exempt = _found(_log((SHA, "m", f"diff --git a/LICENSE b/LICENSE\n--- a/LICENSE\t\n+++ b/LICENSE\t\n@@ -1 +1 @@\n+(c) {FAKE_NAME}\n")), [])
    assert exempt == []
    gone = _found(_log((SHA, "m", f"diff --git a/LICENSE b/LICENSE\n--- a/LICENSE\t\n+++ /dev/null\n@@ -1 +0,0 @@\n-(c) {FAKE_NAME}\n")), [])
    assert gone == []
    for odd in ("LICENSE ", "LICENSEX"):
        failures = _found(_log((SHA, "m", section(odd))), [])
        assert [f.split(" ->")[0] for f in failures] == [f"NAME: history {SHA[:7]} {odd}:5"]
        removed = f"diff --git a/{odd} b/{odd}\n--- a/{odd}\n+++ /dev/null\n@@ -1 +0,0 @@\n-(c) {FAKE_NAME}\n"
        assert [f.split(" ->")[0] for f in _found(_log((SHA, "m", removed)), [])] == [f"NAME: history {SHA[:7]} {odd}:5"]


def test_text_before_the_first_file_is_read_from_its_first_character(named):
    failures = _found(_log((SHA, "m", f"{FAKE_NAME}\n" + _diff("f.py", "+x"))), [])
    assert [f.split(" ->")[0] for f in failures] == [f"NAME: history {SHA[:7]} diff:1"]


def test_the_default_location_of_a_finding_is_its_path_and_line():
    assert leakguard_files.check_text("a.txt", f"x\ncpf {CPF}\n", []) == ["CPF: a.txt:2 -> '52…' (11 chars)"]


def test_a_message_and_a_header_are_read_as_the_text_called_history(named, monkeypatch):
    """Whatever a file's name exempts (a digest file, an attribution file), 'history' does not earn."""
    monkeypatch.setattr(leakguard_patterns, "DIGEST_FILES", {leakguard_files.HISTORY})
    monkeypatch.setattr(leakguard_patterns, "NAME_EXEMPT_PATHS", {leakguard_files.HISTORY})
    header = f"diff --git a/f b/f\nrename from {FAKE_NAME}\nrename to g\n"
    assert _found(_log((SHA, f"cpf {CPF} {FAKE_NAME}", header)), []) == []


def test_the_log_is_framed_by_a_new_hex_nonce_each_run(monkeypatch):
    formats = []
    monkeypatch.setattr(leakguard_files, "_git", lambda *args, **kw: formats.append(args[-1]) or  ("" if kw.get("text", True) else b""))
    leakguard_files.check_history([])
    leakguard_files.check_history([])
    nonces = [re.fullmatch(r"--format=([0-9a-f]{32})C %H%n\1M%n%B%n\1E", f) for f in formats if "C %H" in f]
    assert len(nonces) == 2
    assert nonces[0].group(1) != nonces[1].group(1)


def test_history_with_no_file_asks_git_for_no_content(monkeypatch):
    asked = []
    monkeypatch.setattr(leakguard_files, "_git", lambda *args, **kw: asked.append(args[0]) or  ("" if kw.get("text", True) else b""))
    assert leakguard_files.check_history([]) == []
    assert asked == ["log", "rev-list", "log"]


def test_the_utf16_versions_are_cut_out_of_what_git_wrote_byte_for_byte(monkeypatch):
    """Each object comes as '<id> <type> <size>', a line feed, its bytes and one more line feed. Only a
    file version with a byte order mark is kept, under the first path it had, whatever comes before it."""
    le, be = b"\xff\xfe" + "a\n".encode("utf-16-le"), b"\xfe\xff" + "b".encode("utf-16-be")
    objects = [("1" * 40, "blob", le), ("2" * 40, "tree", b"\xff\xfe tree bytes\n"), ("3" * 40, "blob", b"plain\n"),
               ("4" * 40, "blob", be)]
    listing = f"{'0' * 40}\n{'1' * 40} first.txt\n{'1' * 40} again.txt\n{'2' * 40} dir\n{'3' * 40} plain.txt\n{'4' * 40} dir/b e.txt\n"
    asked = {}

    def git(*args, stdin=None, text=True):
        if args[0] == "rev-list":
            return listing
        asked.update(args=args, stdin=stdin, text=text)
        return b"".join(f"{sha} {kind} {len(data)}\n".encode() + data + b"\n" for sha, kind, data in objects)

    monkeypatch.setattr(leakguard_files, "_git", git)
    assert leakguard_files._utf16_blobs() == [("first.txt", le), ("dir/b e.txt", be)]
    assert asked == {"args": ("cat-file", "--batch"), "text": False,
                     "stdin": "".join(f"{n * 40}\n" for n in "1234").encode()}


def test_git_in_bytes_gives_the_bytes_and_says_why_it_failed_whatever_git_wrote(repo, tmp_path, monkeypatch):
    (tmp_path / "u16.txt").write_bytes(b"\xff\xfea\x00\r\x00\n\x00")
    sha = leakguard_files._git("hash-object", "-w", "u16.txt").strip()
    assert leakguard_files._git("cat-file", "blob", sha, text=False) == b"\xff\xfea\x00\r\x00\n\x00"
    assert leakguard_files._git("cat-file", "--batch", stdin=f"{sha}\n".encode(), text=False).startswith(sha.encode() + b" blob 8\n")
    monkeypatch.setattr(leakguard_files.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a[0], 128, stdout=b"", stderr=b" fatal: \xff bad\nmore\n"))
    with pytest.raises(leakguard_files.GitError) as raised:
        leakguard_files._git("cat-file", "--batch", text=False)
    assert str(raised.value) == "git cat-file failed with exit code 128: fatal: \ufffd bad"


def test_a_section_with_no_path_line_is_located_by_the_file_it_is_about(named):
    """df-cy4s: a rename names its new path, a mode change the path both sides of 'diff --git' share;
    only text before the first file, or a header naming two different paths, stays 'diff'."""
    mode = f"diff --git a/run.sh b/run.sh\nold mode 100644\nnew mode 100755\nindex {FAKE_NAME}\n"
    two = f"diff --git a/x b/y\nindex {FAKE_NAME}\n"
    short = "diff --git a/\nindex " + FAKE_NAME + "\n"
    assert [f.split(" ->")[0] for f in _found(_log((SHA, "m", mode + two + short)), [])] == [
        f"NAME: history {SHA[:7]} run.sh:4", f"NAME: history {SHA[:7]} diff:2"]


def test_two_values_masked_alike_on_one_line_are_two_findings_and_a_merge_repeat_is_one(monkeypatch):
    """df-1q7f: findings print masked (two characters and the length), so two different values can read
    the same; the count of one text is kept, the repetition of a merge's diff per parent (-m) is not."""
    twins = f"{FAKE_NAME} and {FAKE_NAME[:2]}{'x' * (len(FAKE_NAME) - 2)}"
    pattern = re.compile(re.escape(FAKE_NAME) + "|" + re.escape(twins.split(" and ")[1]))
    monkeypatch.setattr(leakguard_patterns, "NAME_PATTERNS", [pattern])
    entry = (SHA, "m", _diff("f.py", f"+{twins}"))
    found = _found(_log(entry, entry), [])
    assert len(found) == 2
    assert len(set(found)) == 1


def test_a_lone_carriage_return_cannot_start_a_forged_section(repo, capsys, tmp_path, named):
    """df-ovkt: read as text, a lone CR in content would end a line, and what follows could pose as a
    'diff --git' section of LICENSE whose hunks are exempt from the name check."""
    forged = f"\0x\rdiff --git a/LICENSE b/LICENSE\r+++ b/LICENSE\r@@ -1 +1 @@\r+by {FAKE_NAME}\n"
    (tmp_path / "f.bin").write_bytes(forged.encode())
    (tmp_path / "crlf.txt").write_bytes(f"a\r\nby {FAKE_NAME}\r\n".encode())
    _git("add", "-A", cwd=tmp_path)
    _git("commit", "-qm", "add", cwd=tmp_path)
    failures = [f.partition(" -> ")[0] for f in run(capsys, "--history")[2] if f.startswith("NAME")]
    assert failures == [f"NAME: history {_head(tmp_path)} crlf.txt:8", f"NAME: history {_head(tmp_path)} f.bin:7"]


def test_the_log_text_reads_crlf_as_one_line_and_a_lone_cr_as_a_space():
    assert leakguard_files._log_text("a\r\nb\rc\n".encode()) == "a\nb c\n"
    assert leakguard_files._log_text(b"\xff") == "\ufffd"
