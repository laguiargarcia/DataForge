import os
import subprocess
import sys
import time
import requests
import yaml
import polars as pl
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
ITEMS_ID = [item.strip() for item in os.getenv("ITEM_IDS", "").split(",") if item.strip()]

# Pluggy paginated endpoints: max page size (used by list_transactions / list_investments).
PLUGGY_PAGE_SIZE = 500

_api_token = None
_api_token_created_at = 0


def get_api_token():
    global _api_token, _api_token_created_at
    now = time.time()
    if _api_token is not None and (now - _api_token_created_at) < 7200:
        return _api_token
    url = "https://api.pluggy.ai/auth"
    body = {"clientId": CLIENT_ID, "clientSecret": CLIENT_SECRET}
    headers = {"accept": "application/json", "content-type": "application/json"}
    response = requests.post(url, json=body, headers=headers)
    if response.status_code == 200:
        _api_token = response.json().get("apiKey")
        _api_token_created_at = now
        return _api_token
    print(f"Failed to get token: {response.status_code} - {response.text}")
    return None


def list_accounts(item_id):
    token = get_api_token()
    if token is None:
        print("list_accounts: skipped — no valid API token")
        return None
    url = f"https://api.pluggy.ai/accounts?itemId={item_id}"
    headers = {"accept": "application/json", "X-API-KEY": token}
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        return response.json()
    print(f"Failed to get accounts: {response.status_code} - {response.text}")
    return None


def list_transactions(account_id):
    token = get_api_token()
    if token is None:
        print("list_transactions: skipped — no valid API token")
        return None
    headers = {"accept": "application/json", "X-API-KEY": token}
    all_results = []
    base = "https://api.pluggy.ai/v2/transactions"
    url = f"{base}?accountId={account_id}&pageSize={PLUGGY_PAGE_SIZE}"
    page = 1
    while True:
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            print(f"Failed to get transactions (page {page}): {response.status_code} - {response.text}")
            return None
        data = response.json()
        results = data.get("results", [])
        all_results.extend(results)
        print(f"  account {account_id}: page {page}, fetched {len(all_results)} so far")
        next_cursor = data.get("next")
        if next_cursor is None:
            break
        if next_cursor.startswith("http"):
            url = next_cursor
        else:
            url = base + "?" + next_cursor.lstrip("?")
        page += 1
    return {"results": all_results, "total": len(all_results)}


def list_bills(account_id):
    token = get_api_token()
    if token is None:
        print("list_bills: skipped — no valid API token")
        return None
    headers = {"accept": "application/json", "X-API-KEY": token}
    all_results = []
    page = 1
    while True:
        url = f"https://api.pluggy.ai/bills?accountId={account_id}&pageSize={PLUGGY_PAGE_SIZE}&page={page}"
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            print(f"Failed to get bills (page {page}): {response.status_code} - {response.text}")
            return None
        data = response.json()
        results = data.get("results", [])
        all_results.extend(results)
        total = data.get("total", 0)
        print(f"  account {account_id}: bills page {page}, fetched {len(all_results)}/{total}")
        if len(all_results) >= total:
            break
        page += 1
    return {"results": all_results, "total": len(all_results)}


def list_investments(item_id):
    token = get_api_token()
    if token is None:
        print("list_investments: skipped — no valid API token")
        return None
    headers = {"accept": "application/json", "X-API-KEY": token}
    all_results = []
    page = 1
    while True:
        url = f"https://api.pluggy.ai/investments?itemId={item_id}&pageSize={PLUGGY_PAGE_SIZE}&page={page}"
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            print(f"Failed to get investments (page {page}): {response.status_code} - {response.text}")
            return None
        data = response.json()
        results = data.get("results", [])
        all_results.extend(results)
        total = data.get("total", 0)
        print(f"  item {item_id}: page {page}, fetched {len(all_results)}/{total}")
        if len(all_results) >= total:
            break
        page += 1
    return {"results": all_results, "total": len(all_results)}


def _workspace_root() -> Path:
    # Scripts at scripts/<layer>/x.py (depth 2) sit two dirs under the workspace root. Keep that
    # as the fast path (unchanged behaviour). Scripts directly under scripts/ (e.g. reprocess.py,
    # depth 1) are one dir shallower, so fall back to walking up to the ancestor that actually
    # holds connections/connections.yaml — making path resolution depth-agnostic.
    base = Path(__file__).parent.parent.parent
    if (base / "connections" / "connections.yaml").exists():
        return base
    for anc in Path(__file__).resolve().parents:
        if (anc / "connections" / "connections.yaml").exists():
            return anc
    return base


def _connection_config(name: str) -> dict:
    connections_path = _workspace_root() / "connections" / "connections.yaml"
    with open(connections_path, encoding="utf-8") as f:
        return yaml.safe_load(f)["connections"][name]


def _connection_root(name: str) -> Path:
    return _workspace_root() / _connection_config(name)["path"]


def write_parquet_partition(connection_name, entity, records, ingestion_date):
    cfg = _connection_config(connection_name)
    if cfg["type"] != "parquet_dir":
        raise ValueError(f"connection {connection_name!r} is type {cfg['type']!r}, not parquet_dir")
    base = _connection_root(connection_name)
    partition_dir = base / entity / f"ingestion_date={ingestion_date.isoformat()}"
    partition_dir.mkdir(parents=True, exist_ok=True)
    out_path = partition_dir / "data.parquet"
    all_keys, seen = [], set()
    for r in records:
        for k in r:
            if k not in seen:
                all_keys.append(k); seen.add(k)
    rows = {k: [(str(r[k]) if r.get(k) is not None else None) for r in records] for k in all_keys}
    pl.DataFrame(rows, schema={k: pl.Utf8 for k in all_keys}).write_parquet(str(out_path))
    return out_path


def run_transform_subprocess(transform_path, source, target):
    # Run a sibling transform in its own process with the standard --source/--target argv —
    # exactly as the daily job invokes it. sys.executable is the active (.venv) interpreter, so
    # no hardcoded venv path. Used by reprocess.py (df-fmc) to re-derive a layer from upstream.
    subprocess.run(
        [sys.executable, str(transform_path), "--source", source, "--target", target],
        check=True,
    )


def delta_table_path(layer, table):
    return _connection_root(layer) / table


def delta_table_exists(layer, table):
    return (delta_table_path(layer, table) / "_delta_log").exists()


def read_delta_table(layer, table):
    return pl.read_delta(str(delta_table_path(layer, table)))


def write_delta_overwrite(layer, table, df):
    path = delta_table_path(layer, table)
    path.mkdir(parents=True, exist_ok=True)
    # schema_mode="overwrite": DROP+CREATE tables intentionally accept schema drift (a
    # rebuilt-every-run table redefines its own schema), unlike accumulating upserts below.
    df.write_delta(str(path), mode="overwrite", delta_write_options={"schema_mode": "overwrite"})


def upsert_delta_table(layer, table, df, keys):
    path = delta_table_path(layer, table)
    if not delta_table_exists(layer, table):
        path.mkdir(parents=True, exist_ok=True)
        # No schema_mode here (unlike write_delta_overwrite): accumulating tables must fail loud
        # on schema drift rather than silently redefine an evolving history. (First run on an
        # empty df still creates the empty, typed table — the empty-seam contract.)
        df.write_delta(str(path), mode="overwrite")
        return
    # A11: once history exists, an empty source must never lose it — an empty MERGE source is a
    # no-op that leaves prior rows intact (and skips a pointless merge).
    if df.is_empty():
        return
    predicate = " AND ".join(f"t.{k} = s.{k}" for k in keys)
    (
        df.write_delta(str(path), mode="merge",
                       delta_merge_options={"predicate": predicate, "source_alias": "s", "target_alias": "t"})
        .when_matched_update_all()
        .when_not_matched_insert_all()
        .execute()
    )
