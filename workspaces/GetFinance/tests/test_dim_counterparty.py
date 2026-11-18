import polars as pl
import pytest

from tests import _delta_util

pytestmark = pytest.mark.skipif(
    not (_delta_util.SCRIPTS / "cleansed2curated" / "dim_counterparty.py").exists(),
    reason="dim_counterparty.py not in this checkout",
)

DIM_COLS = ["counterparty_document", "counterparty_name", "counterparty_document_type"]

# Only the counterparty columns dim_counterparty reads (plus id for realism). All-typed Delta.
_SCHEMA = {
    "id": pl.Utf8, "counterparty_name": pl.Utf8, "counterparty_document": pl.Utf8,
    "counterparty_document_type": pl.Utf8,
}


def _seed_cleansed(tmp_path, rows):
    df = pl.DataFrame(
        {
            "id": [r.get("id", f"t{i}") for i, r in enumerate(rows)],
            "counterparty_name": [r.get("counterparty_name") for r in rows],
            "counterparty_document": [r.get("counterparty_document") for r in rows],
            "counterparty_document_type": [r.get("counterparty_document_type") for r in rows],
        },
        schema=_SCHEMA,
    )
    _delta_util.seed(tmp_path, "cleansed", "transactions", df)


def _run(tmp_path):
    _delta_util.run_script("cleansed2curated", "dim_counterparty",
                           ["--source", "cleansed", "--target", "curated"], tmp_path)


def _dim(tmp_path):
    return _delta_util.read(tmp_path, "curated", "dim_counterparty")


def test_one_row_per_document(tmp_path):
    _seed_cleansed(tmp_path, [
        {"counterparty_document": "11.222.333/0001-44", "counterparty_name": "IFOOD", "counterparty_document_type": "PJ"},
        {"counterparty_document": "11.222.333/0001-44", "counterparty_name": "IFOOD", "counterparty_document_type": "PJ"},
        {"counterparty_document": "11.445.778/0001-99", "counterparty_name": "MUNICIPIO", "counterparty_document_type": "PJ"},
    ])
    _run(tmp_path)
    dim = _dim(tmp_path)
    assert dim.height == 2
    assert set(dim["counterparty_document"]) == {"11.222.333/0001-44", "11.445.778/0001-99"}


def test_canonical_name_is_most_frequent(tmp_path):
    _seed_cleansed(tmp_path, [
        {"counterparty_document": "D", "counterparty_name": "IFOOD", "counterparty_document_type": "PJ"},
        {"counterparty_document": "D", "counterparty_name": "IFOOD", "counterparty_document_type": "PJ"},
        {"counterparty_document": "D", "counterparty_name": "Ifood Ltda", "counterparty_document_type": "PJ"},
    ])
    _run(tmp_path)
    row = _dim(tmp_path).row(0, named=True)
    assert row["counterparty_name"] == "IFOOD"


def test_type_mapping_and_columns(tmp_path):
    _seed_cleansed(tmp_path, [
        {"counterparty_document": "CNPJ-1", "counterparty_name": "ACME", "counterparty_document_type": "PJ"},
        {"counterparty_document": "CPF-1", "counterparty_name": None, "counterparty_document_type": "PF"},
    ])
    _run(tmp_path)
    dim = _dim(tmp_path).sort("counterparty_document")
    assert dim.columns == DIM_COLS
    rows = {r["counterparty_document"]: r for r in dim.to_dicts()}
    assert rows["CNPJ-1"]["counterparty_document_type"] == "PJ"
    assert rows["CPF-1"]["counterparty_document_type"] == "PF"
    assert rows["CPF-1"]["counterparty_name"] is None, "person counterparties have no name"


def test_excludes_null_document(tmp_path):
    _seed_cleansed(tmp_path, [
        {"counterparty_document": "D", "counterparty_name": "ACME", "counterparty_document_type": "PJ"},
        {"counterparty_document": None, "counterparty_name": None, "counterparty_document_type": None},
    ])
    _run(tmp_path)
    dim = _dim(tmp_path)
    assert dim.height == 1
    assert dim["counterparty_document"].to_list() == ["D"]


def test_empty_seam_produces_empty_typed_dim(tmp_path):
    _seed_cleansed(tmp_path, [
        {"counterparty_document": None, "counterparty_name": None, "counterparty_document_type": None},
    ])
    _run(tmp_path)
    dim = _dim(tmp_path)
    assert dim.height == 0
    assert dim.columns == DIM_COLS


def test_finance_daily_has_dim_counterparty_task():
    import yaml
    job = yaml.safe_load(
        (_delta_util.SCRIPTS.parent / "jobs" / "finance_daily" / "job.yaml").read_text()
    )
    tasks = {t["name"]: t for t in job["tasks"]}
    assert "dim_counterparty" in tasks, "finance_daily must build dim_counterparty"
    t = tasks["dim_counterparty"]
    assert t["script"] == "cleansed2curated/dim_counterparty.py"
    assert t["params"] == {"source": "cleansed", "target": "curated"}
    # reads cleansed.transactions (the frozen counterparty columns), so it depends on transform.
    assert "transform" in t["depends_on"]


def test_export_bridge_emits_dim_counterparty():
    bridge = (_delta_util.SCRIPTS / "report" / "export_bridge.py").read_text(encoding="utf-8")
    assert '"dim_counterparty"' in bridge, "export_bridge SUPPORTING must include dim_counterparty"
