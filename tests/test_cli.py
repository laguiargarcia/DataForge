import pytest
from unittest.mock import patch

from typer.testing import CliRunner
from dataforge.cli import app

runner = CliRunner()


def test_validate_missing_workspace(tmp_path, monkeypatch):
    """validate with no workspace.yaml in CWD and no --workspace flag must exit non-zero."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["validate"])
    assert result.exit_code != 0


def test_validate_with_workspace_flag(tmp_path, monkeypatch):
    """validate --workspace <name> resolves workspace when workspaces/<name>/ exists under CWD."""
    # resolve_workspace("myws") returns Path("workspaces") / "myws" — relative to CWD
    ws_dir = tmp_path / "workspaces" / "myws"
    ws_dir.mkdir(parents=True)
    (ws_dir / "workspace.yaml").write_text(
        "name: myws\nversion: '1.0'\nengine: duckdb\ndatabase: sqlite\n"
    )
    (ws_dir / "jobs").mkdir()

    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["validate", "--workspace", "myws"])
    # connections.yaml missing → exit 1, but workspace.yaml parses correctly
    # Either 0 (everything valid) or 1 (connections missing) — must not crash
    assert result.exit_code in (0, 1)
    # workspace line must appear in output
    assert "myws" in result.output


def test_jobs_list_no_workspace(tmp_path, monkeypatch):
    """jobs list with no workspace context must exit non-zero."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["jobs", "list"])
    assert result.exit_code != 0


def test_init_creates_workspace(tmp_path, monkeypatch):
    """init <name> creates workspaces/<name>/workspace.yaml under data_dir()."""
    monkeypatch.setenv("DATAFORGE_HOME", str(tmp_path))
    result = runner.invoke(app, ["init", "myws"])
    assert result.exit_code == 0
    assert (tmp_path / "workspaces" / "myws" / "workspace.yaml").exists()


def test_cli_run_reports_skipped_and_succeeds_when_no_leaf_failure(
    tmp_path, monkeypatch
):
    """t1 succeeds; t2 has `when: failed` so it must be skipped.
    Exit 0 (no int task failed) and t2 must show neutral PULADO label,
    not FALHOU."""
    ws = tmp_path / "workspaces" / "ws"
    (ws / "jobs" / "j").mkdir(parents=True)
    (ws / "scripts").mkdir()
    (ws / "logs").mkdir()
    (ws / "workspace.yaml").write_text(
        "name: ws\nversion: '1.0'\nengine: duckdb\ndatabase: sqlite\n"
    )
    (ws / "scripts" / "ok.py").write_text("print('ok')\n")
    (ws / "scripts" / "fail.py").write_text("import sys; sys.exit(1)\n")
    (ws / "jobs" / "j" / "job.yaml").write_text(
        "name: j\n"
        "tasks:\n"
        "  - name: t1\n    script: ok.py\n"
        "    retry: {attempts: 1, delay_seconds: 0}\n"
        "  - name: t2\n    script: fail.py\n"
        "    depends_on: [{task: t1, when: failed}]\n"
        "    retry: {attempts: 1, delay_seconds: 0}\n"
    )
    monkeypatch.setenv("DATAFORGE_HOME", str(tmp_path))

    result = runner.invoke(app, ["run", "ws", "j"])
    assert "t2" in result.stdout
    assert "PULADO" in result.stdout
    t2_line = next(line for line in result.stdout.splitlines() if "t2:" in line)
    assert "FALHOU" not in t2_line
    assert result.exit_code == 0


# --- df-h864: the exit code follows the run's status --------------------------------------------

# fetch fails and takes its chain down with it; calendar is an independent root that succeeds.
_UNHANDLED = (
    "  - name: fetch\n    script: fail.py\n"
    "  - name: calendar\n    script: ok.py\n"
    "  - name: transform\n    script: ok.py\n    depends_on: [fetch]\n"
    "  - name: report\n    script: ok.py\n    depends_on: [transform, calendar]\n"
)
# Same failure, but a `when: failed` dependent takes it over (the alert pattern, D10).
_HANDLED = _UNHANDLED + (
    "  - name: notify\n    script: ok.py\n    depends_on: [{task: fetch, when: failed}]\n"
)


def _no_retry(tasks_yaml):
    """Give every task one attempt and no delay (a task does not inherit the job's retry)."""
    return tasks_yaml.replace("\n    script:", "\n    retry: {attempts: 1, delay_seconds: 0}\n    script:")


def _workspace_with_job(tmp_path, monkeypatch, tasks_yaml):
    """DATAFORGE_HOME=tmp_path with workspace `ws`, job `j` and a trigger `daily` that runs it."""
    ws = tmp_path / "workspaces" / "ws"
    for d in ("jobs/j", "scripts", "logs", "triggers"):
        (ws / d).mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    (ws / "scripts" / "ok.py").write_text("print('ok')\n")
    (ws / "scripts" / "fail.py").write_text("import sys; sys.exit(1)\n")
    (ws / "jobs" / "j" / "job.yaml").write_text(
        "name: j\ntasks:\n" + _no_retry(tasks_yaml))
    (ws / "triggers" / "daily.yaml").write_text(
        "name: daily\ncron: '0 9 * * *'\nenabled: true\njobs:\n"
        "  - {workspace: ws, job: j, params: {}}\n")
    monkeypatch.setenv("DATAFORGE_HOME", str(tmp_path))
    return ws


def _recorded_statuses(tmp_path):
    from sqlalchemy.orm import Session
    from dataforge.compat import db_url
    from dataforge.db import JobRunORM, init_db
    with Session(init_db(db_url(tmp_path / "dataforge.db"))) as s:
        return [r.status for r in s.query(JobRunORM).all()]


_COMMANDS = {
    "run": ["run", "ws", "j"],
    "scheduled-run": ["scheduled-run", "ws", "daily"],
    "jobs run": ["jobs", "run", "j", "--workspace", "ws"],
}


@pytest.mark.parametrize("command", list(_COMMANDS))
def test_a_failure_nobody_takes_over_exits_1(tmp_path, monkeypatch, command):
    """The 2026-11-12 incident: every leaf skipped behind a failed fetch exited 0 under
    scheduled-run, so the OS scheduler recorded success."""
    _workspace_with_job(tmp_path, monkeypatch, _UNHANDLED)
    with patch("httpx.post"):
        result = runner.invoke(app, _COMMANDS[command])
    assert result.exit_code == 1, result.output
    if command != "jobs run":   # jobs run writes no global JobRunORM row
        assert _recorded_statuses(tmp_path) == ["failed"]


@pytest.mark.parametrize("command", list(_COMMANDS))
def test_a_failure_a_handler_takes_over_exits_0(tmp_path, monkeypatch, command):
    """A handled failure is a successful run (D10), and the exit code agrees with job_runs."""
    _workspace_with_job(tmp_path, monkeypatch, _HANDLED)
    with patch("httpx.post"):
        result = runner.invoke(app, _COMMANDS[command])
    assert result.exit_code == 0, result.output
    assert "FALHOU" in result.output or command == "scheduled-run"
    if command != "jobs run":
        assert _recorded_statuses(tmp_path) == ["success"]


def test_scheduled_run_exits_1_when_a_job_entry_raises(tmp_path, monkeypatch):
    """A trigger entry naming a job that does not exist is logged and fails the process."""
    ws = _workspace_with_job(tmp_path, monkeypatch, _HANDLED)
    (ws / "triggers" / "daily.yaml").write_text(
        "name: daily\ncron: '0 9 * * *'\nenabled: true\njobs:\n"
        "  - {workspace: ws, job: j, params: {}}\n"
        "  - {workspace: ws, job: missing, params: {}}\n")
    with patch("httpx.post") as notify:
        result = runner.invoke(app, ["scheduled-run", "ws", "daily"])
    assert result.exit_code == 1
    notify.assert_called_once()   # the daemon is still told the trigger ran
