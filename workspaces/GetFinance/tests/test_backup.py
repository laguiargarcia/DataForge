# tests/test_backup.py — scripts/backup/backup_dados.py (df-sgyf): zips what cannot be
# rebuilt (raw + edicao.db) into a dated file outside the repo and keeps the newest N.
# Runs the real script in a throwaway workspace under tmp_path; never touches real data.
import shutil
import sqlite3
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from tests._delta_util import SCRIPTS

_SCRIPT = SCRIPTS / "backup" / "backup_dados.py"
pytestmark = pytest.mark.skipif(not _SCRIPT.exists(), reason="backup_dados.py not in this workspace")


def _workspace(tmp_path: Path, with_edicao: bool = True) -> Path:
    ws = tmp_path / "ws"
    (ws / "scripts" / "backup").mkdir(parents=True)
    (ws / "scripts" / "functions").mkdir(parents=True)
    shutil.copy(_SCRIPT, ws / "scripts" / "backup" / "backup_dados.py")
    shutil.copy(SCRIPTS / "functions" / "functions.py", ws / "scripts" / "functions" / "functions.py")
    (ws / "connections").mkdir()
    (ws / "connections" / "connections.yaml").write_text(
        "connections:\n"
        "  raw: {type: parquet_dir, path: data/raw/}\n"
        "  edicao: {type: sqlite_file, path: data/edicao.db}\n",
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


def _run(ws: Path, destino: str, manter: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ws / "scripts" / "backup" / "backup_dados.py"),
         "--destino", destino, "--manter", str(manter)],
        capture_output=True, text=True, cwd=ws.parent, check=False,
    )


def test_zip_holds_raw_and_a_readable_copy_of_edicao(tmp_path):
    ws = _workspace(tmp_path)
    result = _run(ws, "../backups", 3)
    assert result.returncode == 0, result.stderr
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
    assert _run(ws, "../backups", 3).returncode == 0
    (zipped,) = sorted((tmp_path / "backups").glob("getfinance-*.zip"))
    with zipfile.ZipFile(zipped) as z:
        assert sorted(z.namelist()) == ["edicao.db", "raw/transactions/ingestion_date=2026-11-01/data.parquet"]


def test_destination_is_relative_to_the_workspace_not_the_cwd(tmp_path):
    ws = _workspace(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    result = subprocess.run(
        [sys.executable, str(ws / "scripts" / "backup" / "backup_dados.py"),
         "--destino", "../backups", "--manter", "3"],
        capture_output=True, text=True, cwd=elsewhere, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert len(list((tmp_path / "backups").glob("getfinance-*.zip"))) == 1


def test_keeps_only_the_newest_n(tmp_path):
    ws = _workspace(tmp_path)
    for _ in range(4):
        assert _run(ws, "../backups", 2).returncode == 0
    zips = sorted((tmp_path / "backups").glob("getfinance-*.zip"))
    assert len(zips) == 2
    assert not list((tmp_path / "backups").glob("*.parcial"))


def test_missing_edicao_fails_and_creates_nothing(tmp_path):
    ws = _workspace(tmp_path, with_edicao=False)
    result = _run(ws, "../backups", 3)
    assert result.returncode != 0
    assert not (ws / "data" / "edicao.db").exists()   # sqlite must not create an empty one
    assert not list((tmp_path / "backups").glob("getfinance-*"))


def test_keep_must_be_at_least_one(tmp_path):
    ws = _workspace(tmp_path)
    result = _run(ws, "../backups", 0)
    assert result.returncode != 0
    assert not list((tmp_path / "backups").glob("getfinance-*"))
