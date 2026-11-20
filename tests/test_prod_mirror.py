"""scripts/prod/mirror_workspace.py (df-bsb2.2.6.2): a release's workspace over prod's, prod's own files kept."""
import shutil
import tempfile
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from scripts.prod import mirror_workspace as mw

LOCALS = {
    ".env": "PLUGGY_CLIENT_ID=fake\n",
    "dataforge.db": "db",
    "data/raw/accounts/part.parquet": "raw",
    "data/edicao.db": "edicao",
    "report/out.txt": "r",
    "triggers/daily.yaml": "name: daily\n",
    "deploy/.ledger/1.json": "{}",
    "config/investments.yaml": "real holdings\n",
    "config/merchant_normalization.local.yaml": "rules: []\n",
    ".venv/pyvenv.cfg": "home = x\n",
    "scripts/__pycache__/functions.cpython-314.pyc": "pyc",
    "apps/financas/node_modules/x/index.js": "x",
}


def _tree(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(text.encode())   # bytes: Windows text mode would write \r\n


def _snapshot(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.fixture
def ws(tmp_path):
    src, dst, backup = tmp_path / "release" / "GetFinance", tmp_path / "prod" / "GetFinance", tmp_path / "bk"
    _tree(src, {"workspace.yaml": "name: GetFinance\n", "scripts/a.py": "a = 1\n", "jobs/j/job.yaml": "j\n"})
    _tree(dst, LOCALS)
    return src, dst, backup


def test_release_files_reach_prod_and_the_locals_stay_byte_for_byte(ws, capsys):
    src, dst, backup = ws
    assert mw.mirror(src, dst, backup) == 0
    assert "3 written, 0 deleted, 0 unchanged" in capsys.readouterr().out
    after = _snapshot(dst)
    for rel in ("workspace.yaml", "scripts/a.py", "jobs/j/job.yaml"):
        assert after[rel] == (src / rel).read_bytes()
    for rel, text in LOCALS.items():
        assert after[rel] == text.encode()
    assert (dst / mw.MANIFEST).read_text(encoding="utf-8").splitlines() == sorted(
        ["jobs/j/job.yaml", "scripts/a.py", "workspace.yaml"])


def test_a_file_the_release_dropped_is_deleted_and_its_empty_folder_too(ws, capsys):
    src, dst, backup = ws
    _tree(src, {"scripts/old/gone.py": "x\n"})
    mw.mirror(src, dst, backup)
    shutil.rmtree(src / "scripts" / "old")
    capsys.readouterr()
    assert mw.mirror(src, dst, tmp := backup.parent / "bk2") == 0
    assert "0 written, 1 deleted, 3 unchanged" in capsys.readouterr().out
    assert not (dst / "scripts" / "old").exists()
    assert (tmp / "scripts" / "old" / "gone.py").read_text(encoding="utf-8") == "x\n"   # kept for undo
    assert "scripts/old/gone.py" not in (dst / mw.MANIFEST).read_text(encoding="utf-8")


def test_without_a_manifest_nothing_is_deleted(ws):
    src, dst, backup = ws
    _tree(dst, {"scripts/made_in_prod.py": "keep\n"})
    mw.mirror(src, dst, backup)
    assert (dst / "scripts" / "made_in_prod.py").read_text(encoding="utf-8") == "keep\n"


def test_a_manifest_never_deletes_a_local_path(ws, capsys):
    # A hand-seeded manifest (git ls-files of the old checkout, df-bsb2.2.6.3) lists triggers/,
    # and files someone already removed by hand.
    src, dst, backup = ws
    (dst / mw.MANIFEST).write_text("triggers/daily.yaml\n.env\nstale.txt\nalready_gone.txt\n", encoding="utf-8")
    _tree(dst, {"stale.txt": "old\n"})
    assert mw.mirror(src, dst, backup) == 0
    assert "1 deleted" in capsys.readouterr().out
    assert (dst / "triggers" / "daily.yaml").is_file()
    assert (dst / ".env").is_file()
    assert not (dst / "stale.txt").exists()


def test_a_local_path_carried_by_the_release_is_ignored(ws, capsys):
    src, dst, backup = ws
    _tree(src, {".env": "from the release\n", "data/raw/x": "synthetic\n", "scripts/__pycache__/a.pyc": "c"})
    assert mw.mirror(src, dst, backup) == 0
    assert (dst / ".env").read_text(encoding="utf-8") == LOCALS[".env"]
    assert not (dst / "data" / "raw" / "x").exists()
    assert (dst / "scripts" / "__pycache__" / "functions.cpython-314.pyc").is_file()
    assert not (dst / "scripts" / "__pycache__" / "a.pyc").exists()
    warned = capsys.readouterr().err.splitlines()
    assert sorted(warned) == sorted(f"mirror: {rel} is prod's own, the release's copy is ignored"
                                    for rel in (".env", "data/raw/x"))   # a cache is skipped quietly


def test_a_git_checkout_in_prod_is_not_mirrored(ws, capsys):
    src, dst, backup = ws
    (dst / ".git").mkdir()
    before = _snapshot(dst)
    assert mw.mirror(src, dst, backup) == 0
    assert _snapshot(dst) == before
    assert "still a git checkout" in capsys.readouterr().out


def test_a_release_without_the_workspace_changes_nothing(ws, tmp_path, capsys):
    _, dst, backup = ws
    before = _snapshot(dst)
    assert mw.mirror(tmp_path / "nope", dst, backup) == 0
    assert _snapshot(dst) == before
    assert "the release has no nope workspace" in capsys.readouterr().out


def test_a_prod_workspace_that_does_not_exist_yet_is_created(ws, tmp_path):
    src, _, backup = ws
    dst = tmp_path / "new" / "home" / "GetFinance"
    assert mw.mirror(src, dst, backup) == 0
    assert (dst / "scripts" / "a.py").read_text(encoding="utf-8") == "a = 1\n"


def test_a_failure_after_the_deletes_brings_back_the_deleted_folders(ws, monkeypatch):
    src, dst, backup = ws
    _tree(dst, {mw.MANIFEST: "scripts/old/deep/gone.py\n", "scripts/old/deep/gone.py": "x\n"})
    before = _snapshot(dst)
    real_copy = shutil.copy2

    def copy2(source, target, *a, **k):
        if Path(source).name == mw.MANIFEST:   # saving the manifest: the last step, after the deletes
            raise OSError("disk full")
        return real_copy(source, target, *a, **k)

    monkeypatch.setattr(mw.shutil, "copy2", copy2)
    assert mw.mirror(src, dst, backup) == 1
    assert _snapshot(dst) == before


def test_a_failure_midway_puts_prod_back_as_it_was(ws, monkeypatch, capsys):
    src, dst, backup = ws
    _tree(dst, {"scripts/a.py": "a = 0\n", mw.MANIFEST: "scripts/a.py\nstale.txt\n", "stale.txt": "old\n"})
    _tree(src, {"z/last.py": "z\n"})
    before = _snapshot(dst)
    real_copy = shutil.copy2

    def copy2(source, target, *a, **k):
        if Path(source).name == "last.py":
            raise OSError("disk full")
        return real_copy(source, target, *a, **k)

    monkeypatch.setattr(mw.shutil, "copy2", copy2)
    assert mw.mirror(src, dst, backup) == 1
    assert _snapshot(dst) == before
    assert "putting" in capsys.readouterr().err


def test_the_command_line_takes_release_prod_and_backup(ws):
    src, dst, backup = ws
    assert mw.main([str(src), str(dst), str(backup)]) == 0
    assert (dst / "scripts" / "a.py").is_file()
    assert not backup.exists()   # nothing in prod changed before the first write: no backup needed
    (src / "scripts" / "a.py").write_text("a = 2\n", encoding="utf-8")
    assert mw.main([str(src), str(dst), str(backup)]) == 0
    assert (backup / "scripts" / "a.py").read_text(encoding="utf-8") == "a = 1\n"


def test_bad_arguments_print_the_usage(capsys):
    assert mw.main(["only-one"]) == 2
    assert "mirror_workspace.py" in capsys.readouterr().err


# Two levels, file names never folder names: any two paths can live in one tree.
_PATHS = st.builds("{}/{}".format, st.sampled_from(["scripts", "data", "triggers", "config", "apps/__pycache__"]),
                   st.sampled_from(["a.py", "b.py", "investments.yaml", "c.txt"]))


@settings(max_examples=80, deadline=None)
@given(old=st.sets(_PATHS, max_size=6), new=st.sets(_PATHS, max_size=6), local=st.sets(_PATHS, max_size=6))
def test_property_after_a_mirror_prod_is_the_release_plus_its_own_files(old, new, local):
    """Release files land, files only the last manifest named go, prod's own files never change."""
    # A folder per example: tmp_path would be one folder shared by every example Hypothesis runs.
    with tempfile.TemporaryDirectory() as tmp:
        _check_one_mirror(Path(tmp), old, new, local)


def _check_one_mirror(root: Path, old: set[str], new: set[str], local: set[str]) -> None:
    src, dst = root / "src", root / "dst"
    src.mkdir()
    dst.mkdir()
    _tree(src, {p: f"new {p}\n" for p in new})
    prod_own = {p: f"prod {p}\n" for p in local | old}
    _tree(dst, prod_own)
    (dst / mw.MANIFEST).write_text("".join(f"{p}\n" for p in old), encoding="utf-8")
    assert mw.mirror(src, dst, root / "bk") == 0
    after = _snapshot(dst)
    for p in new - {p for p in new if mw.is_local(p)}:
        assert after[p] == f"new {p}\n".encode()
    for p, text in prod_own.items():
        if mw.is_local(p):
            assert after[p] == text.encode()
        elif p in old and p not in new:
            assert p not in after
        elif p not in new:
            assert after[p] == text.encode()
