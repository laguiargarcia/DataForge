# tests/test_backup.py — scripts/backup/backup_dados.py (df-sgyf): zips what cannot be
# rebuilt (raw + edicao.db + the bank statement PDFs, df-z4fh.5) into a dated file outside the repo
# and keeps the newest N.
# Runs the real script in this process (so coverage counts it, df-qwta) against a throwaway workspace
# under tmp_path: _workspace_root is that workspace, so connections.yaml and --destino resolve there
# (a __file__ pointing there would do the same, but coverage then drops the script). Never touches
# real data.
import shutil
import sqlite3
import sys
import zipfile
from pathlib import Path

from tests._delta_util import SCRIPTS, compile_script, exec_functions

_SCRIPT = SCRIPTS / "backup" / "backup_dados.py"
_CODE = compile_script(_SCRIPT)


def _workspace(tmp_path: Path, with_edicao: bool = True) -> Path:
    ws = tmp_path / "ws"
    (ws / "connections").mkdir(parents=True)
    (ws / "connections" / "connections.yaml").write_text(
        "connections:\n"
        "  raw: {type: parquet_dir, path: data/raw/}\n"
        "  edicao: {type: sqlite_file, path: data/edicao.db}\n"
        "  extratos: {type: pdf_dir, path: data/extratos/}\n",
        encoding="utf-8",
    )
    part = ws / "data" / "raw" / "transactions" / "ingestion_date=2026-11-01"
    part.mkdir(parents=True)
    (part / "data.parquet").write_bytes(b"raw-bytes")
    if with_edicao:
        conn = sqlite3.connect(ws / "data" / "edicao.db")
        conn.execute("CREATE TABLE categorias (subcategoria TEXT)")
        conn.execute("INSERT INTO categorias VALUES ('mercado')")
        conn.commit()
        conn.close()
    return ws


def _run(ws: Path, destino: str, manter: int) -> int:
    """The script's exit code: 0 when it ran to the end, 1 when it stopped with a SystemExit message."""
    ns = exec_functions({"__file__": str(_SCRIPT)})
    ns["_workspace_root"] = lambda: ws
    old_argv, sys.argv = sys.argv, ["backup_dados.py", "--destino", destino, "--manter", str(manter)]
    try:
        exec(_CODE, ns)
    except SystemExit as fim:
        return fim.code if isinstance(fim.code, int) else 1
    finally:
        sys.argv = old_argv
    return 0


def test_zip_holds_raw_and_a_readable_copy_of_edicao(tmp_path):
    ws = _workspace(tmp_path)
    assert _run(ws, "../backups", 3) == 0
    zips = sorted((tmp_path / "backups").glob("getfinance-*.zip"))
    assert len(zips) == 1
    with zipfile.ZipFile(zips[0]) as z:
        assert z.read("raw/transactions/ingestion_date=2026-11-01/data.parquet") == b"raw-bytes"
        z.extract("edicao.db", tmp_path / "restored")
    conn = sqlite3.connect(tmp_path / "restored" / "edicao.db")
    assert conn.execute("SELECT subcategoria FROM categorias").fetchall() == [("mercado",)]
    conn.close()


def test_a_leftover_partition_temp_file_is_not_backed_up(tmp_path):
    """df-ahua.10.5: the temp file a partition write that died half way leaves is not data: skipped."""
    ws = _workspace(tmp_path)
    part = ws / "data" / "raw" / "transactions" / "ingestion_date=2026-11-01"
    (part / ".data.parquet.tmp").write_bytes(b"torn")
    assert _run(ws, "../backups", 3) == 0
    (zipped,) = sorted((tmp_path / "backups").glob("getfinance-*.zip"))
    with zipfile.ZipFile(zipped) as z:
        assert sorted(z.namelist()) == ["edicao.db", "raw/transactions/ingestion_date=2026-11-01/data.parquet"]


def test_the_statement_pdfs_go_into_the_zip(tmp_path, capsys):
    """df-z4fh.5: a statement PDF has no rebuild either, so data/extratos/ is zipped under extratos/."""
    ws = _workspace(tmp_path)
    extratos = ws / "data" / "extratos"
    (extratos / "2025").mkdir(parents=True)
    (extratos / "extrato-2026.pdf").write_bytes(b"%PDF-fake-2026")
    (extratos / "2025" / "extrato-2025.pdf").write_bytes(b"%PDF-fake-2025")
    assert _run(ws, "../backups", 3) == 0
    assert "2 extrato(s)" in capsys.readouterr().out
    (zipped,) = sorted((tmp_path / "backups").glob("getfinance-*.zip"))
    with zipfile.ZipFile(zipped) as z:
        assert z.read("extratos/extrato-2026.pdf") == b"%PDF-fake-2026"
        assert z.read("extratos/2025/extrato-2025.pdf") == b"%PDF-fake-2025"


def test_a_missing_statement_folder_is_no_error(tmp_path, capsys):
    ws = _workspace(tmp_path)
    assert _run(ws, "../backups", 3) == 0
    assert "0 extrato(s)" in capsys.readouterr().out
    assert not (ws / "data" / "extratos").exists()   # the backup only reads it


def test_destination_is_relative_to_the_workspace_not_the_cwd(tmp_path, monkeypatch):
    ws = _workspace(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert _run(ws, "../backups", 3) == 0
    assert len(list((tmp_path / "backups").glob("getfinance-*.zip"))) == 1


def test_keeps_only_the_newest_n(tmp_path):
    ws = _workspace(tmp_path)
    for _ in range(4):
        assert _run(ws, "../backups", 2) == 0
    zips = sorted((tmp_path / "backups").glob("getfinance-*.zip"))
    assert len(zips) == 2
    assert not list((tmp_path / "backups").glob("*.parcial"))


def test_missing_edicao_fails_and_creates_nothing(tmp_path):
    ws = _workspace(tmp_path, with_edicao=False)
    assert _run(ws, "../backups", 3) != 0
    assert not (ws / "data" / "edicao.db").exists()   # sqlite must not create an empty one
    assert not list((tmp_path / "backups").glob("getfinance-*"))


def test_keep_must_be_at_least_one(tmp_path):
    ws = _workspace(tmp_path)
    assert _run(ws, "../backups", 0) != 0
    assert not list((tmp_path / "backups").glob("getfinance-*"))


def test_missing_raw_fails_and_creates_nothing(tmp_path):
    ws = _workspace(tmp_path)
    shutil.rmtree(ws / "data" / "raw")
    assert _run(ws, "../backups", 3) != 0
    assert not (tmp_path / "backups").exists()


def test_a_zip_that_fails_its_check_is_removed_and_nothing_is_kept(tmp_path, monkeypatch):
    ws = _workspace(tmp_path)
    monkeypatch.setattr(zipfile.ZipFile, "testzip", lambda self: "edicao.db")
    assert _run(ws, "../backups", 3) != 0
    assert list((tmp_path / "backups").iterdir()) == []   # neither the final zip nor the .parcial
