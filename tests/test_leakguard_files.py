"""Tests for scripts/leakguard_files.py: private paths, how files and paths are read and checked, and
what git says (ignored files, tracked files, the whole history).

Every PII-shaped or "real" value is made up and assembled at run time (tests/leakguard_fixtures.py).
"""

import subprocess
from pathlib import Path

import pytest

from scripts import leakguard_files, leakguard_values
from tests import leakguard_fixtures
from tests.leakguard_fixtures import CNPJ, CPF, EMAIL, NUMBER_BARE, OWNER, RECEIVER, _git, run

repo = pytest.fixture(leakguard_fixtures.repo)
workspace = pytest.fixture(leakguard_fixtures.workspace)


# ---------------------------------------------------------------------------- private files


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
    rel = f"docs/{CPF}.md"
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
    assert all(f.startswith("REAL: history:") for f in failures if f.startswith("REAL"))
    assert sorted(f for f in failures if f.startswith("history:")) == [
        "history: branch.db: private file was committed", "history: merged.db: private file was committed"]


def test_history_counts_lines_from_its_own_format_and_never_colours_it(monkeypatch):
    calls = []
    monkeypatch.setattr(leakguard_files, "_git", lambda *args, **kw: calls.append(args) or "")
    leakguard_files.check_history([])
    assert "--no-color" in calls[0]


def test_a_commit_message_is_the_first_line_after_its_hash(repo, capsys, tmp_path, workspace):
    (tmp_path / ".leakguard-workspaces").write_text(f"{workspace}\n", encoding="utf-8")
    repo("ok.py", "x = 1\n")
    _git("add", "ok.py", cwd=tmp_path)
    _git("commit", "-qm", f"pay {RECEIVER}", cwd=tmp_path)
    code, _, failures = run(capsys, "--history")
    assert code == 1
    assert [f.split(" ->")[0] for f in failures] == ["REAL: history:2"]


def test_history_reads_what_git_takes_for_binary(repo, capsys, tmp_path):
    """df-wkay: a text with a NUL and a UTF-16 one (PowerShell `>`) are 'Binary files differ' in a plain
    log; the audit reads them as the staged check does, after a later commit removed them."""
    repo("ok.py", "x = 1\n")
    (tmp_path / "nul.txt").write_bytes(b"header\x00\n" + EMAIL.encode() + b"\n")
    (tmp_path / "u16.txt").write_bytes(f"x\nFulano de Tal\n{CPF}\n".encode("utf-16"))
    (tmp_path / "u16be.txt").write_bytes(b"\xfe\xff" + f"{CPF}\n".encode("utf-16-be"))
    (tmp_path / "copy.txt").write_bytes(b"\xfe\xff" + f"{CPF}\n".encode("utf-16-be"))   # the same version twice
    _git("add", "-A", cwd=tmp_path)
    _git("commit", "-qm", "add", cwd=tmp_path)
    _git("rm", "-q", "nul.txt", "u16.txt", "u16be.txt", "copy.txt", cwd=tmp_path)
    _git("commit", "-qm", "remove", cwd=tmp_path)
    assert run(capsys, "--all")[2] == []
    failures = run(capsys, "--history")[2]
    emails = [f for f in failures if f.startswith("EMAIL: history:")]   # in the log: the commit that adds, the one that removes
    assert [f.partition(" -> ")[2] for f in emails] == ["'so…' (17 chars)"] * 2
    assert [f for f in failures if f.startswith(("CPF: history: ", "NAME: history: "))] == [
        "CPF: history: copy.txt:1 -> '52…' (11 chars)", "CPF: history: u16.txt:3 -> '52…' (11 chars)",
        "NAME: history: u16.txt:2 -> 'Fu…' (13 chars)"]


def test_history_with_no_file_asks_git_for_no_content(monkeypatch):
    asked = []
    monkeypatch.setattr(leakguard_files, "_git", lambda *args, **kw: "")
    monkeypatch.setattr(leakguard_files, "_git_bytes", lambda *args, **kw: asked.append(args) or b"")
    assert leakguard_files.check_history([]) == []
    assert asked == []
