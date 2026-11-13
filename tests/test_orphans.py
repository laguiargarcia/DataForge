"""Rules of dataforge/orphans.py: in-progress rows whose owner process has ended become `failed`
with the reason; every other row is left exactly as it was (df-bw7, df-ahua.5.6, df-413)."""
import logging
from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from dataforge import orphans
from dataforge.compat import db_url
from dataforge.db import ExecucaoORM, JobRunORM, init_db
from dataforge.orphans import REASON, reconcile, reconcile_or_log, reconcile_task_rows
from tests.process_owners import dead_owner_text

T0 = datetime(2026, 11, 1, 6, 0, 0)
LIVE = None   # owner_process left to its default: this (live) test process
LEGACY = ""   # stored as NULL: a row written before owners were recorded


def _add(path, *rows):
    engine = init_db(db_url(path))
    with Session(engine) as s:
        s.add_all(rows)
        s.commit()
    engine.dispose()


def _owner(kind):
    if kind is LIVE:
        return {}
    return {"owner_process": None} if kind == LEGACY else {"owner_process": dead_owner_text()}


def _task(task, status, inicio, owner, **fields):
    """A task row of run 'r' unless `fields` says otherwise (run_id, output, fim)."""
    return ExecucaoORM(job="j", task=task, status=status, inicio=inicio,
                       **{"run_id": "r", **fields, **_owner(owner)})


def _run(run_id, status, owner, workspace="test_ws", started=T0):
    return JobRunORM(id=run_id, workspace=workspace, job="j", status=status, started_at=started,
                     **_owner(owner))


def _tasks(path):
    engine = init_db(db_url(path))
    with Session(engine) as s:
        rows = {r.task: (r.status, r.fim, r.output, r.duracao_segundos) for r in s.query(ExecucaoORM)}
    engine.dispose()
    return rows


def _runs(home):
    engine = init_db(db_url(home / "dataforge.db"))
    with Session(engine) as s:
        rows = {r.id: (r.status, r.finished_at, r.reason) for r in s.query(JobRunORM)}
    engine.dispose()
    return rows


# --- task rows ----------------------------------------------------------------------------------

def test_dead_in_progress_task_rows_fail_and_the_rest_are_untouched(tmp_path):
    path = tmp_path / "dataforge.db"
    _add(path,
         _task("dead", "running", T0, "dead"),
         _task("dead-out", "running", T0, "dead", output="partial"),
         _task("dead-pending", "pending", T0, "dead"),
         _task("live", "running", T0, LIVE),
         _task("legacy", "running", T0, LEGACY),
         _task("done", "success", T0, "dead", output="ok", fim=T0 + timedelta(seconds=3)))

    assert reconcile_task_rows(db_url(path)) == 3

    assert _tasks(path) == {
        "dead": ("failed", T0, REASON, None),
        "dead-out": ("failed", T0, f"partial\n{REASON}", None),
        "dead-pending": ("failed", T0, REASON, None),
        "live": ("running", None, None, None),
        "legacy": ("running", None, None, None),
        "done": ("success", T0 + timedelta(seconds=3), "ok", None),
    }
    assert reconcile_task_rows(db_url(path)) == 0   # a second pass changes nothing
    assert _tasks(path)["dead"] == ("failed", T0, REASON, None)


# --- run rows -----------------------------------------------------------------------------------

def test_a_dead_run_fails_at_the_last_moment_its_tasks_show(workspace_dir, dataforge_home):
    """Its run row ends at the latest start/end of its own task rows (here a task's start,
    since the dead task row gets fim = inicio), never "now"."""
    _add(workspace_dir / "dataforge.db",
         _task("a", "success", T0 + timedelta(seconds=1), "dead", fim=T0 + timedelta(seconds=5)),
         _task("b", "running", T0 + timedelta(seconds=9), "dead"),
         _task("other-run", "success", T0 + timedelta(hours=1), "dead", run_id="x",
               fim=T0 + timedelta(hours=2)))
    _add(dataforge_home / "dataforge.db", _run("r", "running", "dead"))

    assert reconcile() == ["r"]

    assert _runs(dataforge_home) == {"r": ("failed", T0 + timedelta(seconds=9), REASON)}
    assert _tasks(workspace_dir / "dataforge.db")["b"][:2] == ("failed", T0 + timedelta(seconds=9))


def test_a_dead_run_with_no_tasks_ends_at_its_start(workspace_dir, dataforge_home):
    """Killed before its first task (a 'pending' row, df-ahua.5.6), or its workspace is gone."""
    _add(dataforge_home / "dataforge.db",
         _run("pending", "pending", "dead"),
         _run("gone-ws", "running", "dead", workspace="deleted_ws"),
         _run("bad-ws", "running", "dead", workspace="../x"))

    assert sorted(reconcile()) == ["bad-ws", "gone-ws", "pending"]
    assert {k: v[1] for k, v in _runs(dataforge_home).items()} == dict.fromkeys(
        ["pending", "gone-ws", "bad-ws"], T0)


def test_live_legacy_and_finished_runs_are_untouched(workspace_dir, dataforge_home):
    _add(dataforge_home / "dataforge.db",
         _run("live", "running", LIVE), _run("live-pending", "pending", LIVE),
         _run("legacy", "running", LEGACY), _run("done", "success", "dead"))

    assert reconcile() == []
    assert _runs(dataforge_home) == {
        "live": ("running", None, None), "live-pending": ("pending", None, None),
        "legacy": ("running", None, None), "done": ("success", None, None)}


def test_one_workspace_is_reconciled_alone(workspace_dir, dataforge_home):
    other = dataforge_home / "workspaces" / "other"
    other.mkdir()
    (other / "workspace.yaml").write_text("name: other\n", encoding="utf-8")
    _add(other / "dataforge.db", _task("t", "running", T0, "dead", run_id="r-other"))
    _add(dataforge_home / "dataforge.db",
         _run("r-test", "running", "dead"), _run("r-other", "running", "dead", workspace="other"))

    assert reconcile("test_ws") == ["r-test"]
    assert _runs(dataforge_home)["r-other"][0] == "running"
    assert _tasks(other / "dataforge.db")["t"][0] == "running"

    assert reconcile("other") == ["r-other"]
    assert _tasks(other / "dataforge.db")["t"][0] == "failed"


def test_an_unknown_workspace_name_reconciles_nothing_and_does_not_fail(dataforge_home):
    _add(dataforge_home / "dataforge.db", _run("r", "running", "dead"))
    assert reconcile("../etc") == []
    assert _runs(dataforge_home)["r"][0] == "running"


def test_both_workspace_stores_are_reconciled(workspace_dir, dataforge_home):
    """<ws>/dataforge.db (run, scheduled-run, API) and <ws>/data/dataforge.db (jobs run, whose
    run has no run row: its task row is judged by its own owner)."""
    (workspace_dir / "data").mkdir()
    _add(workspace_dir / "dataforge.db", _task("t", "running", T0, "dead"))
    _add(workspace_dir / "data" / "dataforge.db",
         _task("t", "running", T0, "dead", run_id="local-abc"))

    assert reconcile() == []
    assert _tasks(workspace_dir / "dataforge.db")["t"][0] == "failed"
    assert _tasks(workspace_dir / "data" / "dataforge.db")["t"][0] == "failed"


def test_reconciling_never_creates_a_workspace_database(workspace_dir, dataforge_home):
    reconcile()
    reconcile("test_ws")
    assert not (workspace_dir / "dataforge.db").exists()
    assert not (workspace_dir / "data").exists()


def test_a_failing_reconciliation_is_logged_and_changes_nothing(monkeypatch, caplog):
    def boom(workspace=None):
        raise RuntimeError("disk gone")

    monkeypatch.setattr(orphans, "reconcile", boom)
    with caplog.at_level(logging.ERROR, logger="dataforge.orphans"):
        assert reconcile_or_log("w") == []
    assert "Reconciling orphaned runs failed" in caplog.text


def test_reconcile_or_log_returns_what_reconcile_failed(monkeypatch):
    seen = []
    monkeypatch.setattr(orphans, "reconcile", lambda workspace=None: seen.append(workspace) or ["r"])
    assert reconcile_or_log("w") == ["r"]
    assert seen == ["w"]


@pytest.mark.parametrize("endpoint", ["/api/workspaces/test_ws/executions", "/api/workspaces/"])
def test_reading_history_through_the_api_reconciles_first(endpoint, workspace_dir, dataforge_home,
                                                          test_client):
    """The GUI's history and job list never show a dead run in progress, even while the daemon
    that started before the crash keeps running."""
    (workspace_dir / "jobs" / "j").mkdir()
    (workspace_dir / "jobs" / "j" / "job.yaml").write_text(
        "name: j\ntasks:\n  - {name: t, script: t.py}\n", encoding="utf-8")
    _add(dataforge_home / "dataforge.db", _run("r", "running", "dead"))

    body = test_client.get(endpoint).json()

    assert _runs(dataforge_home)["r"] == ("failed", T0, REASON)
    if endpoint.endswith("executions"):
        (item,) = body["items"]
        assert (item["status"], item["reason"], item["finished_at"]) == (
            "failed", REASON, T0.isoformat())
    else:
        (ws,) = body
        assert ws["jobs"] == [{"name": "j", "last_run_status": "failed"}]


def test_the_cli_history_fails_dead_task_rows_before_listing(workspace_dir, dataforge_home):
    """`dataforge execucoes list` reads <ws>/data/dataforge.db (written by `jobs run`)."""
    from typer.testing import CliRunner
    from dataforge.cli import app

    (workspace_dir / "data").mkdir()
    store = workspace_dir / "data" / "dataforge.db"
    _add(store, _task("dead", "running", T0, "dead", run_id="local-1"),
         _task("live", "running", T0, LIVE, run_id="local-2"))

    result = CliRunner().invoke(app, ["execucoes", "list", "-w", "test_ws"])

    assert result.exit_code == 0, result.output
    assert {k: v[0] for k, v in _tasks(store).items()} == {"dead": "failed", "live": "running"}
    assert "failed" in result.output
    assert "running" in result.output
