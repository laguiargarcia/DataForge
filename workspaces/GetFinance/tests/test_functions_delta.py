import polars as pl
from pathlib import Path

from tests import _delta_util

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load_functions(tmp_path):
    ns = {"__file__": str(SCRIPTS / "functions" / "functions.py")}
    _delta_util.exec_functions(ns)
    ns["_connection_root"] = lambda layer: tmp_path / layer  # seam: point layers at tmp
    return ns


def test_overwrite_then_read_roundtrip(tmp_path):
    ns = _load_functions(tmp_path)
    df = pl.DataFrame({"id": ["a", "b"], "v": [1, 2]})
    ns["write_delta_overwrite"]("cleansed", "accounts", df)
    got = ns["read_delta_table"]("cleansed", "accounts").sort("id")
    assert got.to_dicts() == [{"id": "a", "v": 1}, {"id": "b", "v": 2}]


def test_upsert_creates_then_merges(tmp_path):
    ns = _load_functions(tmp_path)
    ns["upsert_delta_table"]("curated", "fact", pl.DataFrame({"k": [1], "d": ["x"], "v": [10]}), ["k", "d"])
    ns["upsert_delta_table"]("curated", "fact", pl.DataFrame({"k": [1, 1], "d": ["y", "x"], "v": [99, 11]}), ["k", "d"])
    got = ns["read_delta_table"]("curated", "fact").sort(["k", "d"])
    assert got.to_dicts() == [
        {"k": 1, "d": "x", "v": 11},   # updated
        {"k": 1, "d": "y", "v": 99},   # inserted
    ]
