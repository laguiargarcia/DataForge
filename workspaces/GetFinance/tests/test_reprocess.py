import datetime
import subprocess

import polars as pl
import pytest

from tests import _delta_util

pytestmark = pytest.mark.skipif(
    not (_delta_util.SCRIPTS / "reprocess.py").exists(),
    reason="reprocess.py not in this checkout",
)

# Raw partition column shapes (all-VARCHAR, mirroring write_parquet_partition output).
TXN_COLS = ["id", "date", "amount", "category", "categoryId", "description",
            "descriptionRaw", "merchant", "accountId", "accountName", "ingested_at"]
ACC_COLS = ["id", "type", "name", "ingested_at"]

# Full cleansed.transactions schema (raw2cleansed output) — for seeding a pre-existing table.
CLEANSED_SCHEMA = {
    "id": pl.Utf8, "date": pl.Date, "amount": pl.Float64, "category": pl.Utf8,
    "categoryId": pl.Utf8, "description": pl.Utf8, "descriptionRaw": pl.Utf8,
    "merchant": pl.Utf8, "account": pl.Utf8, "accountId": pl.Utf8,
    "account_type": pl.Utf8, "ingested_at": pl.Utf8,
}

# Connection registry the reprocess script consults. Injected so tests never depend on a
# workspace connections.yaml: raw is the immutable parquet system-of-record, cleansed is Delta.
_FAKE_CONNS = {
    "raw": {"type": "parquet_dir", "path": "data/raw/"},
    "cleansed": {"type": "delta_dir", "path": "data/cleansed/"},
    "curated": {"type": "delta_dir", "path": "data/curated/"},
}


def _write_partition(raw_root, entity, records, cols, ingestion_date):
    part_dir = raw_root / entity / f"ingestion_date={ingestion_date}"
    part_dir.mkdir(parents=True, exist_ok=True)
    out = part_dir / "data.parquet"
    data = {c: [(str(r.get(c)) if r.get(c) is not None else None) for r in records] for c in cols}
    pl.DataFrame(data, schema={c: pl.Utf8 for c in cols}).write_parquet(str(out))


def _seed_txn_partition(raw_root, transactions, accounts, ingestion_date):
    txn_records = [
        {"id": t["id"], "date": t.get("date", "2026-01-01"), "amount": str(t.get("amount", "0")),
         "category": t.get("category", ""), "categoryId": t.get("categoryId", ""),
         "description": t.get("description", ""), "descriptionRaw": t.get("descriptionRaw", ""),
         "merchant": t.get("merchant"), "accountId": t.get("accountId", "acc-1"),
         "accountName": t.get("accountName", "Checking"),
         "ingested_at": t.get("ingested_at", f"{ingestion_date}T00:00:00Z")}
        for t in transactions
    ]
    _write_partition(raw_root, "transactions", txn_records, TXN_COLS, ingestion_date)
    acc_records = [{"id": a["id"], "type": a["type"], "name": a.get("name", ""),
                    "ingested_at": a.get("ingested_at", f"{ingestion_date}T00:00:00Z")} for a in accounts]
    _write_partition(raw_root, "accounts", acc_records, ACC_COLS, ingestion_date)


def _seed_cleansed_row(tmp_path, txn_id):
    df = pl.DataFrame(
        {"id": [txn_id], "date": [datetime.date(2020, 1, 1)], "amount": [1.0],
         "category": ["x"], "categoryId": ["c"], "description": ["d"], "descriptionRaw": ["dr"],
         "merchant": ["m"], "account": ["Checking"], "accountId": ["acc-1"],
         "account_type": ["BANK"], "ingested_at": ["2020-01-01T00:00:00Z"]},
        schema=CLEANSED_SCHEMA,
    )
    _delta_util.seed(tmp_path, "cleansed", "transactions", df)


def _make_inproc_runner(tmp_path, recorder=None):
    """Replace the subprocess transform-runner with an in-process exec against tmp_path.

    Swaps only the process boundary (a real subprocess can't see the harness's tmp
    _connection_root override) — the REAL transform script still runs end to end.
    """
    def _run(transform_path, source, target):
        from pathlib import Path
        p = Path(transform_path)
        if recorder is not None:
            recorder.append(p.stem)
        _delta_util.run_script(p.parent.name, p.stem,
                               ["--source", source, "--target", target], tmp_path)
    return _run


def _spy_runner(recorder):
    def _run(transform_path, source, target):
        from pathlib import Path
        recorder.append(Path(transform_path).stem)
    return _run


def _reprocess(tmp_path, argv, runner):
    return _delta_util.run_script(
        ".", "reprocess", argv, tmp_path,
        overrides={"_connection_config": lambda n: _FAKE_CONNS[n],
                   "run_transform_subprocess": runner},
    )


def _ids(tmp_path):
    return {r["id"] for r in _delta_util.read(tmp_path, "cleansed", "transactions").to_dicts()}


def test_reprocess_rebuilds_cleansed_identical_to_fresh_run(tmp_path):
    txns = [{"id": "A", "amount": "10.0"}, {"id": "B", "amount": "20.0"}]
    accs = [{"id": "acc-1", "type": "BANK"}]
    _seed_txn_partition(tmp_path / "raw", txns, accs, "2026-01-01")

    _reprocess(tmp_path, ["--source", "raw", "--target", "cleansed", "--entity", "transactions"],
               _make_inproc_runner(tmp_path))
    via_reprocess = {r["id"]: r["amount"]
                     for r in _delta_util.read(tmp_path, "cleansed", "transactions").to_dicts()}

    fresh = tmp_path / "fresh"
    _seed_txn_partition(fresh / "raw", txns, accs, "2026-01-01")
    _delta_util.run_script("raw2cleansed", "raw2cleansed",
                           ["--source", "raw", "--target", "cleansed"], fresh)
    via_direct = {r["id"]: r["amount"]
                  for r in _delta_util.read(fresh, "cleansed", "transactions").to_dicts()}

    assert via_reprocess == via_direct == {"A": 10.0, "B": 20.0}


def test_reprocess_preserves_aged_out_id(tmp_path):
    # OLD lives only in an old raw partition; the latest fetch (newer partition) has only NEW.
    # raw is the durable system of record, so reprocess must re-derive OLD into cleansed.
    _seed_txn_partition(tmp_path / "raw", [{"id": "OLD", "amount": "5.0"}],
                        [{"id": "acc-1", "type": "BANK"}], "2026-01-01")
    _seed_txn_partition(tmp_path / "raw", [{"id": "NEW", "amount": "9.0"}],
                        [{"id": "acc-1", "type": "BANK"}], "2026-01-02")

    runner = _make_inproc_runner(tmp_path)
    _reprocess(tmp_path, ["--source", "raw", "--target", "cleansed", "--entity", "transactions"], runner)
    assert _ids(tmp_path) == {"OLD", "NEW"}

    # Running again (now the target exists → drop path) is idempotent and still lossless.
    _reprocess(tmp_path, ["--source", "raw", "--target", "cleansed", "--entity", "transactions"], runner)
    assert _ids(tmp_path) == {"OLD", "NEW"}


def test_reprocess_drops_stale_rows_absent_from_raw(tmp_path):
    # A normal accumulate run MERGEs and would KEEP a row no longer in raw. reprocess drops the
    # table first, so a stale cleansed row absent from raw must NOT survive.
    _seed_cleansed_row(tmp_path, "STALE")
    _seed_txn_partition(tmp_path / "raw", [{"id": "A", "amount": "10.0"}],
                        [{"id": "acc-1", "type": "BANK"}], "2026-01-01")

    _reprocess(tmp_path, ["--source", "raw", "--target", "cleansed", "--entity", "transactions"],
               _make_inproc_runner(tmp_path))
    assert _ids(tmp_path) == {"A"}


def test_reprocess_rebuilds_when_target_absent(tmp_path):
    _seed_txn_partition(tmp_path / "raw", [{"id": "A", "amount": "10.0"}],
                        [{"id": "acc-1", "type": "BANK"}], "2026-01-01")
    _reprocess(tmp_path, ["--source", "raw", "--target", "cleansed", "--entity", "transactions"],
               _make_inproc_runner(tmp_path))
    assert _ids(tmp_path) == {"A"}


def test_reprocess_refuses_parquet_dir_target(tmp_path):
    calls = []
    _reprocess(tmp_path, ["--source", "cleansed", "--target", "raw", "--entity", "transactions"],
               _spy_runner(calls))
    assert calls == [], "must refuse to rebuild the parquet_dir system of record"


def test_reprocess_unknown_entity_refused(tmp_path):
    calls = []
    _reprocess(tmp_path, ["--source", "raw", "--target", "cleansed", "--entity", "bogus"],
               _spy_runner(calls))
    assert calls == []


def test_reprocess_all_routes_every_entity(tmp_path):
    calls = []
    _reprocess(tmp_path, ["--source", "raw", "--target", "cleansed", "--entity", "all"],
               _spy_runner(calls))
    assert calls == ["raw2cleansed", "raw2cleansed_accounts",
                     "raw2cleansed_investments", "raw2cleansed_bills"]


def test_connection_resolution_works_for_depth1_script(tmp_path):
    # reprocess.py lives directly under scripts/ (depth 1). functions.py historically assumed
    # scripts/<layer>/x.py (depth 2) for connections — a depth-1 script must still resolve its
    # workspace's connections.yaml, or a real (non-harness) prod run crashes.
    ws = tmp_path / "GetFinance"
    (ws / "connections").mkdir(parents=True)
    (ws / "connections" / "connections.yaml").write_text(
        "connections:\n  raw:\n    type: parquet_dir\n    path: data/raw/\n")
    (ws / "scripts").mkdir()
    fake_script = ws / "scripts" / "reprocess.py"
    fake_script.write_text("# placeholder")

    ns = {"__file__": str(fake_script)}
    _delta_util.exec_functions(ns)
    assert ns["_connection_config"]("raw")["type"] == "parquet_dir"
    assert ns["_connection_root"]("raw") == ws / "data" / "raw"


_CONNECTIONS_YAML = "connections:\n  raw:\n    type: parquet_dir\n    path: data/raw/\n"


def _functions_at(script):
    # functions.py loaded as if exec'd by `script` (its __file__ is what the path lookup walks from).
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("# placeholder")
    return _delta_util.exec_functions({"__file__": str(script)})


def _connections_at(root, text=_CONNECTIONS_YAML):
    (root / "connections").mkdir(parents=True, exist_ok=True)
    (root / "connections" / "connections.yaml").write_text(text)


def test_a_depth2_script_takes_its_workspace_before_a_nearer_connections_yaml(tmp_path):
    # The fast path: scripts/<layer>/x.py sits two dirs under the workspace root, and that root is
    # taken even when a nearer ancestor (here the layer dir) also holds a connections.yaml.
    ws = tmp_path / "GetFinance"
    _connections_at(ws)
    _connections_at(ws / "scripts" / "report", "connections:\n  raw:\n    type: other\n    path: x/\n")
    ns = _functions_at(ws / "scripts" / "report" / "build.py")
    assert ns["_workspace_root"]() == ws
    assert ns["_connection_config"]("raw") == {"type": "parquet_dir", "path": "data/raw/"}


def test_without_any_connections_yaml_the_root_is_two_dirs_up_and_reading_it_fails(tmp_path):
    ws = tmp_path / "GetFinance"
    ns = _functions_at(ws / "scripts" / "report" / "build.py")
    assert ns["_workspace_root"]() == ws
    with pytest.raises(FileNotFoundError, match="connections.yaml"):
        ns["_connection_config"]("raw")


def test_an_unknown_connection_name_raises(tmp_path):
    ws = tmp_path / "GetFinance"
    _connections_at(ws)
    ns = _functions_at(ws / "scripts" / "report" / "build.py")
    with pytest.raises(KeyError, match="nowhere"):
        ns["_connection_config"]("nowhere")


def test_run_transform_subprocess_passes_source_and_target(tmp_path):
    out = tmp_path / "argv.txt"
    transform = tmp_path / "transform.py"
    transform.write_text(f"import sys\nopen({str(out)!r}, 'w').write(' '.join(sys.argv[1:]))\n")
    ns = _delta_util.exec_functions({"__file__": str(_delta_util.FUNCTIONS)})
    ns["run_transform_subprocess"](transform, "raw", "cleansed")
    assert out.read_text() == "--source raw --target cleansed"


def test_run_transform_subprocess_raises_when_the_transform_fails(tmp_path):
    transform = tmp_path / "transform.py"
    transform.write_text("raise SystemExit(3)\n")
    ns = _delta_util.exec_functions({"__file__": str(_delta_util.FUNCTIONS)})
    with pytest.raises(subprocess.CalledProcessError):
        ns["run_transform_subprocess"](transform, "raw", "cleansed")


def test_reprocess_dry_run_does_not_drop_or_run(tmp_path):
    _seed_cleansed_row(tmp_path, "KEEP")
    calls = []
    _reprocess(tmp_path, ["--source", "raw", "--target", "cleansed", "--entity", "transactions",
                          "--dry-run"], _spy_runner(calls))
    assert calls == [], "dry-run must not execute the transform"
    assert _ids(tmp_path) == {"KEEP"}, "dry-run must not drop the existing table"
