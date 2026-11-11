import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from typer.testing import CliRunner

from dataforge.cli import app, _cron_to_ps_trigger

runner = CliRunner()


# ── cron translation unit tests ──────────────────────────────────────────────

def test_cron_daily_at_6am():
    assert _cron_to_ps_trigger("0 6 * * *") == '-Daily -At "06:00"'


def test_cron_daily_padded():
    assert _cron_to_ps_trigger("30 8 * * *") == '-Daily -At "08:30"'


def test_cron_weekdays():
    result = _cron_to_ps_trigger("0 9 * * 1-5")
    assert "-Weekly" in result
    assert "Monday" in result
    assert "Friday" in result
    assert "Sunday" not in result
    assert 'At "09:00"' in result


def test_cron_specific_day():
    result = _cron_to_ps_trigger("0 7 * * 1")
    assert "-Weekly" in result
    assert "Monday" in result


def test_cron_weekend():
    result = _cron_to_ps_trigger("0 10 * * 0,6")
    assert "-Weekly" in result
    assert "Saturday" in result
    assert "Sunday" in result
    assert "Monday" not in result


def test_cron_wrong_field_count():
    with pytest.raises(ValueError, match="5 fields"):
        _cron_to_ps_trigger("0 6 * *")


def test_cron_wildcard_minute_unsupported():
    with pytest.raises(ValueError, match="Unsupported"):
        _cron_to_ps_trigger("*/15 * * * *")


def test_cron_dom_specific_unsupported():
    with pytest.raises(ValueError, match="Unsupported"):
        _cron_to_ps_trigger("0 6 1 * *")


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_workspace(root: Path, ws: str = "test_ws", job: str = "test_job") -> Path:
    ws_dir = root / "workspaces" / ws
    (ws_dir / "jobs" / job).mkdir(parents=True)
    (ws_dir / "workspace.yaml").write_text(f"name: {ws}\nversion: '1.0'\nengine: duckdb\n")
    (ws_dir / "jobs" / job / "job.yaml").write_text(
        f"name: {job}\ntasks:\n  - name: t1\n    script: t1.py\n    depends_on: []\n"
    )
    return ws_dir


# ── dataforge run command ────────────────────────────────────────────────────

def test_run_command_success(tmp_path, monkeypatch):
    _make_workspace(tmp_path)
    monkeypatch.setenv("DATAFORGE_HOME", str(tmp_path))
    monkeypatch.setattr("dataforge.engine.run_job", lambda *a, **kw: {"t1": 0})

    result = runner.invoke(app, ["run", "test_ws", "test_job"])
    assert result.exit_code == 0


def test_run_command_failure(tmp_path, monkeypatch):
    _make_workspace(tmp_path)
    monkeypatch.setenv("DATAFORGE_HOME", str(tmp_path))
    monkeypatch.setattr("dataforge.engine.run_job", lambda *a, **kw: {"t1": 1})

    result = runner.invoke(app, ["run", "test_ws", "test_job"])
    assert result.exit_code == 1


def test_run_creates_job_run_orm(tmp_path, monkeypatch):
    from dataforge.db import init_db, JobRunORM
    from sqlalchemy.orm import Session

    _make_workspace(tmp_path)
    monkeypatch.setenv("DATAFORGE_HOME", str(tmp_path))
    monkeypatch.setattr("dataforge.engine.run_job", lambda *a, **kw: {"t1": 0})

    runner.invoke(app, ["run", "test_ws", "test_job"])

    from dataforge.compat import db_url as make_db_url
    engine = init_db(make_db_url(tmp_path / "dataforge.db"))
    with Session(engine) as session:
        runs = session.query(JobRunORM).filter_by(workspace="test_ws", job="test_job").all()
    assert len(runs) == 1


def test_run_updates_schedule_last_run_at(tmp_path, monkeypatch):
    from dataforge.db import init_db, ScheduleORM
    from sqlalchemy.orm import Session

    _make_workspace(tmp_path)
    monkeypatch.setenv("DATAFORGE_HOME", str(tmp_path))
    monkeypatch.setattr("dataforge.engine.run_job", lambda *a, **kw: {"t1": 0})

    from dataforge.compat import db_url as make_db_url
    global_db_url = make_db_url(tmp_path / "dataforge.db")
    engine = init_db(global_db_url)
    with Session(engine) as session:
        session.add(ScheduleORM(
            id="test_ws:test_job", workspace="test_ws", job="test_job", cron="0 6 * * *",
        ))
        session.commit()

    runner.invoke(app, ["run", "test_ws", "test_job"])

    with Session(engine) as session:
        row = session.get(ScheduleORM, "test_ws:test_job")
    assert row.last_run_at is not None


def test_run_unknown_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("DATAFORGE_HOME", str(tmp_path))
    result = runner.invoke(app, ["run", "nonexistent_ws", "some_job"])
    assert result.exit_code == 1


# ── schedule-sync command ─────────────────────────────────────────────────────

def test_schedule_sync_registers_task(tmp_path, monkeypatch):
    from dataforge.models import Job, Task

    _make_workspace(tmp_path)
    monkeypatch.setenv("DATAFORGE_HOME", str(tmp_path))

    job_with_schedule = Job(
        name="test_job",
        schedule="0 6 * * *",
        tasks=[Task(name="t1", script="t1.py", depends_on=[])],
    )
    mock_proc = MagicMock(returncode=0, stderr="")

    with patch("dataforge.compat.list_workspaces", return_value=[tmp_path / "workspaces" / "test_ws"]), \
         patch("dataforge.parser.load_jobs", return_value=[job_with_schedule]), \
         patch("subprocess.run", return_value=mock_proc) as mock_sub:
        result = runner.invoke(app, ["schedule-sync"])

    assert result.exit_code == 0
    assert mock_sub.called
    ps_script = mock_sub.call_args[0][0][-1]
    assert "DataForge" in ps_script
    assert "test_ws" in ps_script
    assert "test_job" in ps_script
    assert "StartWhenAvailable" in ps_script
    assert "Register-ScheduledTask" in ps_script


def test_schedule_sync_skips_unsupported_cron(tmp_path, monkeypatch):
    from dataforge.models import Job, Task

    monkeypatch.setenv("DATAFORGE_HOME", str(tmp_path))

    job_bad_cron = Job(
        name="test_job",
        schedule="*/15 * * * *",
        tasks=[Task(name="t1", script="t1.py", depends_on=[])],
    )

    with patch("dataforge.compat.list_workspaces", return_value=[tmp_path / "workspaces" / "test_ws"]), \
         patch("dataforge.parser.load_jobs", return_value=[job_bad_cron]), \
         patch("subprocess.run") as mock_sub:
        result = runner.invoke(app, ["schedule-sync"])

    assert result.exit_code == 0
    assert not mock_sub.called


def test_schedule_sync_registers_task_windows_native(tmp_path, monkeypatch):
    from dataforge.models import Job, Task

    _make_workspace(tmp_path)
    monkeypatch.setenv("DATAFORGE_HOME", str(tmp_path))

    job_with_schedule = Job(
        name="test_job",
        schedule="0 6 * * *",
        tasks=[Task(name="t1", script="t1.py", depends_on=[])],
    )
    mock_proc = MagicMock(returncode=0, stderr="")

    with patch("dataforge.cli._is_wsl", return_value=False), \
         patch("dataforge.compat.list_workspaces", return_value=[tmp_path / "workspaces" / "test_ws"]), \
         patch("dataforge.parser.load_jobs", return_value=[job_with_schedule]), \
         patch("subprocess.run", return_value=mock_proc) as mock_sub:
        result = runner.invoke(app, ["schedule-sync"])

    assert result.exit_code == 0
    ps_script = mock_sub.call_args[0][0][-1]
    assert "wsl.exe" not in ps_script
    assert "powershell.exe" in ps_script
    assert "$env:DATAFORGE_HOME" in ps_script
    assert "StartWhenAvailable" in ps_script


def test_schedule_sync_skips_jobs_without_schedule(tmp_path, monkeypatch):
    from dataforge.models import Job, Task

    monkeypatch.setenv("DATAFORGE_HOME", str(tmp_path))

    job_no_schedule = Job(
        name="test_job",
        tasks=[Task(name="t1", script="t1.py", depends_on=[])],
    )

    with patch("dataforge.compat.list_workspaces", return_value=[tmp_path / "workspaces" / "test_ws"]), \
         patch("dataforge.parser.load_jobs", return_value=[job_no_schedule]), \
         patch("subprocess.run") as mock_sub:
        result = runner.invoke(app, ["schedule-sync"])

    assert result.exit_code == 0
    assert not mock_sub.called


# ── battery-flag hardening (df-413) ──────────────────────────────────────────

def test_register_windows_task_sets_battery_flags(tmp_path, monkeypatch):
    """_register_windows_task must not inherit battery-blocking defaults (df-413)."""
    from dataforge.cli import _register_windows_task

    monkeypatch.setenv("DATAFORGE_HOME", str(tmp_path))
    mock_proc = MagicMock(returncode=0, stderr="")
    with patch("subprocess.run", return_value=mock_proc) as mock_sub:
        _register_windows_task("test_ws", "test_job", "0 6 * * *")

    ps_script = mock_sub.call_args[0][0][-1]
    assert "-AllowStartIfOnBatteries" in ps_script
    assert "-DontStopIfGoingOnBatteries" in ps_script


def test_register_windows_trigger_sets_battery_flags(tmp_path, monkeypatch):
    """_register_windows_trigger must not inherit battery-blocking defaults (df-413)."""
    from dataforge.cli import _register_windows_trigger

    monkeypatch.setenv("DATAFORGE_HOME", str(tmp_path))
    mock_proc = MagicMock(returncode=0, stderr="")
    with patch("subprocess.run", return_value=mock_proc) as mock_sub:
        _register_windows_trigger(
            "test_ws", "daily", "0 6 * * *", "dataforge", str(tmp_path)
        )

    ps_script = mock_sub.call_args[0][0][-1]
    assert "-AllowStartIfOnBatteries" in ps_script
    assert "-DontStopIfGoingOnBatteries" in ps_script


# ── GET /schedule API endpoint ───────────────────────────────────────────────

def test_get_schedule_returns_data(test_client, dataforge_home):
    from dataforge.db import init_db, ScheduleORM
    from sqlalchemy.orm import Session

    from dataforge.compat import db_url as make_db_url
    engine = init_db(make_db_url(dataforge_home / "dataforge.db"))
    with Session(engine) as session:
        session.add(ScheduleORM(
            id="test_ws:finance_daily",
            workspace="test_ws",
            job="finance_daily",
            cron="0 6 * * *",
        ))
        session.commit()

    resp = test_client.get("/api/workspaces/test_ws/jobs/finance_daily/schedule")
    assert resp.status_code == 200
    data = resp.json()
    assert data["cron"] == "0 6 * * *"
    assert data["enabled"] is True
    assert data["last_run_at"] is None


def test_get_schedule_not_found(test_client):
    resp = test_client.get("/api/workspaces/test_ws/jobs/no_schedule_job/schedule")
    assert resp.status_code == 404
