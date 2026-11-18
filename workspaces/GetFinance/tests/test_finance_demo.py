"""The demo job (finance_demo, DataForge df-bsb2.2.1): the finance_daily pipeline on synthetic data,
with no .env and no Pluggy call, so a fresh public clone runs it end to end.

What is pinned: the demo is finance_daily with only the fetch swapped (so the two never drift
apart); the demo and the real data never mix: the synthetic fetch refuses (exit 1) a raw layer it did
not write, no demo task runs before that fetch passed, and the real landing refuses a raw layer the
demo wrote (the prod checkout holds real raw data that cannot be rebuilt); the synthetic holdings
map is found from any working directory; and the whole demo job runs to the end on a fresh copy, so a
new task that cannot read the synthetic data fails here (df-bsb2.2.9: the balance check needed the
accounts' updatedAt, which the generator did not write).
"""
import os
import shutil
import subprocess
import sys
from unittest.mock import patch

import polars as pl
import pytest

from dataforge.parser import load_job
from tests import _delta_util
from tests.test_cleansed2curated_investments import (
    _read_dim,
    _seed_cleansed_investments,
    _seed_institution_via_accounts,
)

WORKSPACE = _delta_util.ROOT
SYNTHETIC_FETCH = "synthetic/generate_synthetic_data.py"
SYNTHETIC_HOLDINGS = "config/investments.synthetic.yaml"
# The demo's own tasks: the one-time seed of the editable tables, which prod already has filled.
DEMO_ONLY = {"seed_edicao"}
# The demo's own edges: calendar waits for the synthetic fetch, so a refused fetch writes nothing.
DEMO_EDGES = {"calendar": {"fetch"}}


def _tasks(job_name):
    return {t.name: t for t in load_job(WORKSPACE, job_name).tasks}


def _comparable(task, drop_edges=()):
    """The task as finance_daily and finance_demo must share it."""
    d = task.model_dump(exclude={"depends_on"})
    d["depends_on"] = sorted((e.task, str(e.when)) for e in task.depends_on if e.task not in drop_edges)
    return d


def test_demo_needs_no_credentials():
    job = load_job(WORKSPACE, "finance_demo")
    assert job.env_file is None
    tasks = {t.name: t for t in job.tasks}
    assert tasks["fetch"].script == SYNTHETIC_FETCH
    assert all(t.script != "landing/landing2raw.py" for t in job.tasks)
    assert job.schedule is None   # never armed: a trigger runs finance_daily only


def test_demo_runs_the_same_pipeline_as_finance_daily():
    daily, demo = _tasks("finance_daily"), _tasks("finance_demo")
    assert set(demo) == set(daily) | DEMO_ONLY
    for name, task in daily.items():
        if name == "fetch":
            continue
        shared = _comparable(demo[name], drop_edges=DEMO_ONLY | DEMO_EDGES.get(name, set()))
        expected = _comparable(task)
        if name == "dim_investments":
            assert shared["params"].pop("investments-config") == SYNTHETIC_HOLDINGS
        if name == "job_failed":   # the message names its job: the engine does not fill {job} in
            assert shared["params"].pop("message").startswith("finance_demo:")
            expected["params"].pop("message")
        assert shared == expected, name


def test_demo_seeds_the_editable_tables_after_fetch_and_before_classify():
    demo = _tasks("finance_demo")
    seed = demo["seed_edicao"]
    assert [e.task for e in seed.depends_on] == ["fetch"]
    for param in ("dicionario", "mapeamento"):
        assert (WORKSPACE / seed.params[param]).is_file()
    assert "seed_edicao" in {e.task for e in demo["classify"].depends_on}


def _runs_only_after_fetch_succeeded(name, tasks):
    """True when `name` cannot start unless fetch succeeded: every way in goes through an edge that
    needs its task to have run (`succeeded`, `failed` or `completed`, never `skipped`) and that task
    itself waits for fetch. A task that ran at all, ran after fetch passed."""
    if name == "fetch":
        return True
    task = tasks[name]
    gated = ["skipped" not in e.when and _runs_only_after_fetch_succeeded(e.task, tasks)
             for e in task.depends_on]
    return bool(gated) and (all(gated) if task.match == "any" else any(gated))


def test_no_demo_script_runs_before_the_synthetic_fetch_passes():
    tasks = _tasks("finance_demo")
    scripts = [name for name, t in tasks.items() if t.script and name != "fetch"]
    assert scripts
    assert [n for n in scripts if not _runs_only_after_fetch_succeeded(n, tasks)] == []


def test_the_synthetic_fetch_is_not_retried():
    # Its refusal is deterministic: a retry only repeats it, 30 s later.
    assert _tasks("finance_demo")["fetch"].retry.attempts == 1


def _raw_files(raw):
    return sorted(p.relative_to(raw) for p in raw.rglob("*") if p.is_file())


def _raw_contents(raw):
    return {p.relative_to(raw): p.read_bytes() for p in raw.rglob("*") if p.is_file()}


def _generate(tmp_path):
    _delta_util.run_script("synthetic", "generate_synthetic_data", ["--target", "raw"], tmp_path)


@pytest.fixture
def exit_codes(monkeypatch):
    """The codes the script under test passed to sys.exit (run_script swallows the SystemExit)."""
    codes = []

    def _exit(code=0):
        codes.append(code)
        raise SystemExit(code)

    monkeypatch.setattr(sys, "exit", _exit)
    return codes


def test_synthetic_fetch_writes_every_entity_on_an_empty_raw(tmp_path):
    _generate(tmp_path)
    raw = tmp_path / "raw"
    for entity in ("accounts", "transactions", "bills", "investments"):
        parts = list((raw / entity).glob("ingestion_date=*/data.parquet"))
        assert len(parts) == 1, entity
        assert pl.read_parquet(parts[0]).height > 0, entity


def test_synthetic_fetch_runs_again_over_its_own_data(tmp_path):
    _generate(tmp_path)
    before = _raw_files(tmp_path / "raw")
    _generate(tmp_path)
    assert _raw_files(tmp_path / "raw") == before


def test_synthetic_fetch_refuses_a_raw_layer_it_did_not_write(tmp_path, capsys, exit_codes):
    real = tmp_path / "raw" / "accounts" / "ingestion_date=2026-01-02" / "data.parquet"
    real.parent.mkdir(parents=True)
    real.write_bytes(b"not synthetic")
    _generate(tmp_path)
    assert exit_codes == [1]
    assert _raw_files(tmp_path / "raw") == [real.relative_to(tmp_path / "raw")]
    assert real.read_bytes() == b"not synthetic"
    assert "refusing" in capsys.readouterr().out


def test_real_landing_refuses_a_raw_layer_the_demo_wrote(tmp_path, capsys, exit_codes):
    _generate(tmp_path)
    before = _raw_contents(tmp_path / "raw")
    with patch("requests.post") as post, patch("requests.get") as get:
        _delta_util.run_script("landing", "landing2raw", ["--target", "raw"], tmp_path,
                               overrides={"ITEMS_ID": ["item-fake-0001"]})
    assert exit_codes == [1]
    assert not post.called
    assert not get.called
    assert _raw_contents(tmp_path / "raw") == before
    assert "refusing" in capsys.readouterr().out


def test_synthetic_holdings_map_is_read_from_the_workspace_from_any_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_cleansed_investments(tmp_path, [
        {"id": "inv-cdb-0001", "itemId": "item-show-0001", "type": "FIXED_INCOME",
         "subtype": "CDB", "name": "CDB Synthetic Bank A"},
    ])
    _seed_institution_via_accounts(tmp_path, {"item-show-0001": "Banco do Brasil"})
    _delta_util.run_script("cleansed2curated", "cleansed2curated_investments",
                           ["--source", "cleansed", "--target", "curated",
                            "--investments-config", SYNTHETIC_HOLDINGS], tmp_path)
    assert [r["holding_uuid"] for r in _read_dim(tmp_path)] == ["synthetic-cdb-a"]


# Without digits the account_uuid ends in '-', a suspicious finding that fails the demo run's
# data_warnings (functions.py, "account_uuid has no account digits").
def test_every_synthetic_account_has_account_digits(tmp_path):
    _generate(tmp_path)
    accounts = pl.read_parquet(next((tmp_path / "raw" / "accounts").glob("*/data.parquet")))
    assert accounts.height == 2
    assert all(n and any(c.isdigit() for c in n) for n in accounts["number"].to_list())


@pytest.mark.parametrize("holding", ["inv-cdb-0001", "inv-cdb-0002", "inv-tesouro-0001"])
def test_every_synthetic_holding_is_mapped(tmp_path, holding):
    _generate(tmp_path)
    inv = pl.read_parquet(next((tmp_path / "raw" / "investments").glob("*/data.parquet")))
    assert holding in inv["id"].to_list()
    assert holding in (WORKSPACE / SYNTHETIC_HOLDINGS).read_text(encoding="utf-8")


def test_the_demo_job_runs_to_the_end_on_a_fresh_copy(tmp_path):
    # The public face of the workspace: `dataforge run GetFinance finance_demo` on a fresh clone. Run on a
    # copy under a scratch DATAFORGE_HOME, so nothing is written into this checkout.
    home = tmp_path / "home"
    shutil.copytree(WORKSPACE, home / "workspaces" / "GetFinance",
                    ignore=shutil.ignore_patterns("data", "tests", "apps", ".pytest_cache", "__pycache__"))
    env = {k: v for k, v in os.environ.items() if k not in ("GETFINANCE_DATA", "MERCHANT_NORM_CONFIG")}
    env["DATAFORGE_HOME"] = str(home)
    result = subprocess.run([sys.executable, "-m", "dataforge", "run", "GetFinance", "finance_demo"],
                            cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8",
                            timeout=600, check=False)
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
