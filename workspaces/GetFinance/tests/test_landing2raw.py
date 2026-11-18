import sys
import polars as pl
import pytest
from pathlib import Path
from unittest.mock import patch

from tests import _delta_util

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def run_script(name: str, argv: list[str], overrides: dict | None = None) -> dict:
    script_path = SCRIPTS / "landing" / f"{name}.py"
    source = "\n".join(
        line for line in script_path.read_text().splitlines()
        if not line.strip().startswith("exec(open(")
    )
    ns: dict = {"__file__": str(script_path)}
    _delta_util.exec_functions(ns)
    if overrides:
        ns.update(overrides)
    old_argv, sys.argv = sys.argv, ["script"] + argv
    try:
        exec(source, ns)
    except SystemExit:
        pass
    finally:
        sys.argv = old_argv
    return ns


@pytest.fixture(autouse=True)
def _pluggy_env(monkeypatch):
    monkeypatch.setenv("CLIENT_ID", "test-client")
    monkeypatch.setenv("CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("ITEM_IDS", "item-111")


def _raw_overrides(raw_root: Path) -> dict:
    """Redirect the 'raw' parquet_dir connection at a tmp directory."""
    return {
        "_connection_config": lambda name: {"type": "parquet_dir", "path": "raw/"},
        "_connection_root": lambda name: raw_root,
    }


def _partition_files(raw_root: Path, entity: str) -> list[Path]:
    return list((raw_root / entity).glob("ingestion_date=*/data.parquet"))


def _count(parquet_path: Path) -> int:
    return pl.read_parquet(str(parquet_path)).height


def test_landing2raw_writes_accounts_transactions_investments(tmp_path):
    raw_root = tmp_path / "raw"

    mock_accounts = {"results": [{"id": "acc-1", "name": "Checking", "type": "BANK"}]}
    mock_transactions = {
        "results": [{"id": "txn-1", "amount": -50.0, "date": "2026-01-15", "description": "Coffee", "category": "Food"}],
        "total": 1,
    }
    mock_investments = {
        "results": [{"id": "inv-1", "type": "FIXED_INCOME", "name": "CDB", "amount": 1000.0}],
        "total": 1,
    }

    with patch("requests.post") as mock_post, patch("requests.get") as mock_get:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"apiKey": "test-token"}
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.side_effect = [mock_accounts, mock_transactions, mock_investments]

        run_script("landing2raw", ["--target", "raw"], overrides={
            "ITEMS_ID": ["item-111"],
            **_raw_overrides(raw_root),
        })

    for entity in ("accounts", "transactions", "investments"):
        parts = _partition_files(raw_root, entity)
        assert len(parts) == 1, f"expected one {entity} partition, got {parts}"
        assert _count(parts[0]) == 1, f"expected 1 {entity} record"


def test_landing2raw_partition_stamped_with_ingestion_date(tmp_path):
    raw_root = tmp_path / "raw"

    mock_accounts = {"results": [{"id": "acc-1", "name": "Checking", "type": "BANK"}]}
    mock_transactions = {"results": [{"id": "txn-1", "amount": -1.0, "date": "2026-01-15"}], "total": 1}
    mock_investments = {"results": [], "total": 0}

    with patch("requests.post") as mock_post, patch("requests.get") as mock_get:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"apiKey": "test-token"}
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.side_effect = [mock_accounts, mock_transactions, mock_investments]

        run_script("landing2raw", ["--target", "raw"], overrides={
            "ITEMS_ID": ["item-111"],
            **_raw_overrides(raw_root),
        })

    parts = _partition_files(raw_root, "transactions")
    assert len(parts) == 1
    # partition directory must be the hive-style ingestion_date=YYYY-MM-DD stamp
    assert parts[0].parent.name.startswith("ingestion_date=")
    # the written records must carry ingested_at
    cols = pl.read_parquet(str(parts[0])).columns
    assert "ingested_at" in cols


def test_landing2raw_stamps_ingested_at_as_date(tmp_path):
    # A16: ingested_at is day-granular ("YYYY-MM-DD"), not a full timestamp. Nothing downstream
    # consumes sub-day precision (fact snapshot_date truncates to [:10]) and the raw layer is
    # one-overwriting-partition-per-day, so the date is the honest lineage + dedup-ordering
    # stamp. The name is kept; no separate ingestion_date data column is introduced.
    raw_root = tmp_path / "raw"
    mock_accounts = {"results": [{"id": "acc-1", "name": "Checking", "type": "BANK"}]}
    mock_transactions = {"results": [{"id": "txn-1", "amount": -1.0, "date": "2026-01-15"}], "total": 1}
    mock_investments = {"results": [], "total": 0}

    with patch("requests.post") as mock_post, patch("requests.get") as mock_get:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"apiKey": "test-token"}
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.side_effect = [mock_accounts, mock_transactions, mock_investments]

        run_script("landing2raw", ["--target", "raw"], overrides={
            "ITEMS_ID": ["item-111"],
            **_raw_overrides(raw_root),
        })

    part = _partition_files(raw_root, "transactions")[0]
    val = str(pl.read_parquet(str(part))["ingested_at"].unique().to_list()[0])
    msg = f"ingested_at must be a 'YYYY-MM-DD' date, got {val!r}"
    assert len(val) == 10, msg
    assert "T" not in val, msg
    assert val.count("-") == 2, msg


def test_landing2raw_fails_on_a_failed_item(tmp_path):
    # df-ahua.10.1: a failed Pluggy call fails the run (it used to skip the item and end with success).
    raw_root = tmp_path / "raw"
    overrides = {"ITEMS_ID": ["item-111"], **_raw_overrides(raw_root)}

    with patch("requests.post") as mock_post, patch("requests.get") as mock_get:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"apiKey": "test-token"}
        mock_get.return_value.status_code = 500
        mock_get.return_value.text = "Server Error"

        with pytest.raises(RuntimeError, match="GET /accounts answered HTTP 500"):
            run_script("landing2raw", ["--target", "raw"], overrides=overrides)

    # nothing fetched -> no partitions written at all
    assert not raw_root.exists() or not list(raw_root.glob("*/ingestion_date=*/data.parquet"))
