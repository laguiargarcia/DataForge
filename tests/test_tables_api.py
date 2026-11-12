import polars as pl
import pytest
from datetime import date

from dataforge.compat import curated_dir


def _write_vendas(workspace_dir, tipo):
    pl.DataFrame({
        "id": pl.Series([1, 2, 3], dtype=pl.Int64),
        "dia": [date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3)],
        "tipo": pl.Series(tipo, dtype=pl.String),
        "valor": [1.5, 2.5, float("nan")],
        "ok": [True, True, False],
    }).write_delta(str(curated_dir(workspace_dir) / "vendas"))


@pytest.fixture
def seeded(workspace_dir):
    _write_vendas(workspace_dir, ["a", "b", None])
    (curated_dir(workspace_dir) / "pasta_sem_delta").mkdir()
    return workspace_dir


@pytest.fixture
def seeded_with_colon(workspace_dir):
    _write_vendas(workspace_dir, ["a:b", "b", None])
    return workspace_dir


def test_reads_rows_with_iso_dates(seeded, test_client):
    body = test_client.get("/api/workspaces/test_ws/tables/vendas").json()
    assert body["columns"] == ["id", "dia", "tipo", "valor", "ok"]
    assert body["rows"][0]["dia"] == "2026-06-01"
    assert body["truncated"] is False


def test_nan_becomes_null_not_500(seeded, test_client):
    r = test_client.get("/api/workspaces/test_ws/tables/vendas", params={"eq": "id:3"})
    assert r.status_code == 200 and r.json()["rows"][0]["valor"] is None


def test_inf_becomes_null_not_500(workspace_dir, test_client):
    pl.DataFrame({
        "id": pl.Series([1, 2, 3, 4], dtype=pl.Int64),
        "f64": [float("inf"), float("-inf"), 2.5, None],
        "f32": pl.Series([float("-inf"), float("inf"), 1.5, None], dtype=pl.Float32),
    }).write_delta(str(curated_dir(workspace_dir) / "medidas"))
    r = test_client.get("/api/workspaces/test_ws/tables/medidas")
    assert r.status_code == 200
    assert r.json()["rows"] == [
        {"id": 1, "f64": None, "f32": None},
        {"id": 2, "f64": None, "f32": None},
        {"id": 3, "f64": 2.5, "f32": 1.5},
        {"id": 4, "f64": None, "f32": None},
    ]


def test_eq_casts_to_column_type(seeded, test_client):
    for eq, n in [("id:2", 1), ("tipo:a", 1), ("ok:true", 2), ("dia:2026-06-01", 1)]:
        r = test_client.get("/api/workspaces/test_ws/tables/vendas", params={"eq": eq})
        assert len(r.json()["rows"]) == n, eq


def test_value_may_contain_colon(seeded_with_colon, test_client):
    r = test_client.get("/api/workspaces/test_ws/tables/vendas", params={"eq": "tipo:a:b"})
    assert len(r.json()["rows"]) == 1


def test_range_is_inclusive(seeded, test_client):
    params = {"gte": "dia:2026-06-02", "lte": "dia:2026-06-03"}
    assert len(test_client.get("/api/workspaces/test_ws/tables/vendas", params=params).json()["rows"]) == 2


def test_limit_reports_truncation(seeded, test_client):
    body = test_client.get("/api/workspaces/test_ws/tables/vendas", params={"limit": 2}).json()
    assert len(body["rows"]) == 2 and body["truncated"] is True
    body = test_client.get("/api/workspaces/test_ws/tables/vendas", params={"limit": 3}).json()
    assert body["truncated"] is False


@pytest.mark.parametrize("params", [
    {"eq": "nao_existe:1"}, {"eq": "id:abc"}, {"eq": "semdoispontos"},
    {"gte": "dia:ontem"}, {"limit": 0}, {"limit": 100001},
])
def test_bad_filter_is_422(seeded, test_client, params):
    assert test_client.get("/api/workspaces/test_ws/tables/vendas", params=params).status_code == 422


@pytest.mark.parametrize("url", [
    "/api/workspaces/nao/tables/vendas",
    "/api/workspaces/test_ws/tables/nao",
    "/api/workspaces/test_ws/tables/..",
    "/api/workspaces/test_ws/tables/..%2f..%2fworkspace",
    "/api/workspaces/test_ws/tables/pasta_sem_delta",
])
def test_not_found_is_404(seeded, test_client, url):
    assert test_client.get(url).status_code == 404


def test_only_curated_is_readable(seeded, workspace_dir, test_client):
    pl.DataFrame({"x": [1]}).write_delta(str(workspace_dir / "data/raw_tabela"))
    assert test_client.get("/api/workspaces/test_ws/tables/raw_tabela").status_code == 404


def test_table_name_outside_the_allowed_charset_is_404(workspace_dir, test_client):
    """A real Delta table exists at curated/ven-das, so only the name check can turn it into 404."""
    pl.DataFrame({"x": [1]}).write_delta(str(curated_dir(workspace_dir) / "ven-das"))
    r = test_client.get("/api/workspaces/test_ws/tables/ven-das")
    assert r.status_code == 404
    assert r.json() == {"detail": "Not found"}
