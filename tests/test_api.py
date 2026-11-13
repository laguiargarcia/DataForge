"""Orchestration API tests."""
import pytest
from fastapi.testclient import TestClient
from dataforge import compat
from dataforge.api import app

_client = TestClient(app)


def test_health_identifica_o_dataforge(dataforge_home):
    """GET /health says which DataForge this is: service, code version and home."""
    response = _client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "dataforge"
    assert body["version"]
    assert body["home"] == str(compat.data_dir().resolve())


# ---------------------------------------------------------------------------
# API-01: GET /api/workspaces
# ---------------------------------------------------------------------------

def test_list_workspaces_returns_200(test_client, dataforge_home):
    """GET /api/workspaces returns 200 with a list of workspaces."""
    response = test_client.get("/api/workspaces")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


def test_list_workspaces_includes_jobs(test_client, dataforge_home):
    """GET /api/workspaces lists jobs and last_run_status per workspace."""
    ws = dataforge_home / "workspaces" / "acme"
    ws.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: acme\n")
    (ws / "jobs" / "etl").mkdir(parents=True)
    (ws / "jobs" / "etl" / "job.yaml").write_text(
        "name: etl\ntasks:\n  - name: step1\n    script: run.py\n"
    )

    response = test_client.get("/api/workspaces")
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 1
    ws_data = next((w for w in data if w["name"] == "acme"), None)
    assert ws_data is not None
    assert "jobs" in ws_data
    job = next((j for j in ws_data["jobs"] if j["name"] == "etl"), None)
    assert job is not None
    assert "last_run_status" in job  # None for never-run jobs


# ---------------------------------------------------------------------------
# API-02: POST /api/workspaces/{ws}/jobs/{job}/run
# ---------------------------------------------------------------------------

def test_trigger_job_returns_202(test_client, dataforge_home):
    """POST /api/workspaces/{ws}/jobs/{job}/run returns 202 with execution_id."""
    ws = dataforge_home / "workspaces" / "acme"
    ws.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: acme\n")
    (ws / "jobs" / "noop").mkdir(parents=True)
    (ws / "jobs" / "noop" / "job.yaml").write_text(
        "name: noop\ntasks:\n  - name: step1\n    script: run.py\n"
        "    retry: {attempts: 1, delay_seconds: 0}\n"
    )

    response = test_client.post("/api/workspaces/acme/jobs/noop/run")
    assert response.status_code == 202
    data = response.json()
    assert "execution_id" in data
    assert isinstance(data["execution_id"], str)
    assert len(data["execution_id"]) == 36  # UUID format


def test_trigger_job_unknown_workspace_returns_404(test_client, dataforge_home):
    """POST /api/workspaces/{ws}/jobs/{job}/run returns 404 for missing workspace."""
    response = test_client.post("/api/workspaces/nonexistent/jobs/noop/run")
    assert response.status_code == 404


@pytest.mark.parametrize("job", ["C:%5Cfora", "a.b", "a%5Cb"])
def test_trigger_job_rejects_an_unsafe_job_name(test_client, dataforge_home, job):
    """df-7ds: {job} becomes a path segment, so a name like C:\\fora must not reach the disk."""
    ws = dataforge_home / "workspaces" / "acme"
    ws.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: acme\n", encoding="utf-8")
    response = test_client.post(f"/api/workspaces/acme/jobs/{job}/run")
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# API-03: GET /api/executions/{id}/stream (SSE)
# ---------------------------------------------------------------------------

def test_stream_unknown_run_returns_404(test_client, dataforge_home):
    """GET /api/executions/{id}/stream returns 404 for unknown run_id."""
    response = test_client.get(
        "/api/executions/00000000-0000-0000-0000-000000000000/stream"
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# API-04: GET /api/workspaces/{ws}/executions (history)
# ---------------------------------------------------------------------------

def test_execution_history_returns_200(test_client, dataforge_home, workspace_engine):
    """GET /api/workspaces/{ws}/executions returns 200 with paginated list."""
    response = test_client.get("/api/workspaces/test_ws/executions")
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data
    assert isinstance(data["items"], list)


def test_execution_history_pagination(test_client, dataforge_home, workspace_engine):
    """GET /api/workspaces/{ws}/executions supports offset/limit query params."""
    response = test_client.get(
        "/api/workspaces/test_ws/executions?offset=0&limit=10"
    )
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# API-05: GET /api/executions/{id}/output
# ---------------------------------------------------------------------------

def test_output_unknown_run_returns_404(test_client, dataforge_home):
    """GET /api/executions/{id}/output returns 404 for unknown run_id."""
    response = test_client.get(
        "/api/executions/00000000-0000-0000-0000-000000000000/output"
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Plan 01.5-06: GET /api/workspaces/{ws}/jobs (new endpoint)
# ---------------------------------------------------------------------------

def test_list_jobs_happy_path(test_client, workspace_dir):
    """GET /api/workspaces/{ws}/jobs returns 200 with name+parameters per job."""
    import yaml as _yaml

    # Job with declared parameters
    param_job_dir = workspace_dir / "jobs" / "param_job"
    param_job_dir.mkdir(parents=True)
    (param_job_dir / "job.yaml").write_text(_yaml.safe_dump({
        "name": "param_job",
        "tasks": [],
        "parameters": [{"name": "start_date", "type": "date", "default": "2026-01-01"}],
    }))

    # Legacy job without parameters key
    legacy_job_dir = workspace_dir / "jobs" / "legacy_job"
    legacy_job_dir.mkdir(parents=True)
    (legacy_job_dir / "job.yaml").write_text(
        "name: legacy_job\ntasks:\n  - name: s1\n    script: run.py\n"
    )

    response = test_client.get("/api/workspaces/test_ws/jobs")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2

    # Results should be sorted by name
    names = [j["name"] for j in data]
    assert names == sorted(names)

    legacy = next(j for j in data if j["name"] == "legacy_job")
    assert legacy["parameters"] == []

    parameterized = next(j for j in data if j["name"] == "param_job")
    assert len(parameterized["parameters"]) == 1
    assert parameterized["parameters"][0]["name"] == "start_date"
    assert parameterized["parameters"][0]["type"] == "date"


def test_list_jobs_nonexistent_workspace(test_client, dataforge_home):
    """GET /api/workspaces/{ws}/jobs returns 404 for nonexistent workspace."""
    response = test_client.get("/api/workspaces/does_not_exist/jobs")
    assert response.status_code == 404
    data = response.json()
    assert "does_not_exist" in data["detail"]


def test_output_returns_per_task_logs(test_client, dataforge_home, global_engine, workspace_engine):
    """GET /api/executions/{id}/output returns list of per-task stdout/stderr."""
    import uuid
    from sqlalchemy.orm import Session
    from dataforge.db import JobRunORM, ExecucaoORM, utcnow

    run_id = str(uuid.uuid4())

    with Session(global_engine) as s:
        s.add(JobRunORM(
            id=run_id, workspace="test_ws", job="etl",
            status="success", started_at=utcnow(),
        ))
        s.commit()

    with Session(workspace_engine) as s:
        s.add(ExecucaoORM(
            job="etl", task="step1", status="success",
            inicio=utcnow(), output="hello world\n", run_id=run_id,
        ))
        s.commit()

    response = test_client.get(f"/api/executions/{run_id}/output")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["task"] == "step1"
    assert "output" in data[0]


def test_background_run_that_raises_marks_the_run_failed(dataforge_home, workspace_dir, monkeypatch):
    """If loading or running the job raises, the run row and the broadcast both end as failed."""
    import asyncio
    from sqlalchemy.orm import Session
    from dataforge.api.broadcaster import get_broadcaster
    from dataforge.api.routers import jobs as jobs_router
    from dataforge.compat import db_url
    from dataforge.db import JobRunORM, init_db

    global_url = db_url(dataforge_home / "dataforge.db")
    with Session(init_db(global_url)) as session:
        session.add(JobRunORM(id="r1", workspace="test_ws", job="boom", status="running"))
        session.commit()

    def _raise(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(jobs_router, "load_job", _raise)

    async def _run():
        broadcaster = get_broadcaster("test_ws")
        q = broadcaster.subscribe()
        try:
            await jobs_router._run_job_background(
                "r1", "boom", workspace_dir, db_url(workspace_dir / "dataforge.db"), global_url, "test_ws")
            return [q.get_nowait()["status"] for _ in range(q.qsize())]
        finally:
            broadcaster.unsubscribe(q)

    assert asyncio.run(_run()) == ["pending", "failed"]
    with Session(init_db(global_url)) as session:
        run = session.get(JobRunORM, "r1")
        assert run.status == "failed"
        assert run.finished_at is not None


def test_background_run_publishes_pending_running_then_terminal(dataforge_home, workspace_dir):
    import asyncio
    from sqlalchemy.orm import Session
    from dataforge.api.broadcaster import get_broadcaster
    from dataforge.api.routers import jobs as jobs_router
    from dataforge.compat import db_url
    from dataforge.db import JobRunORM, init_db

    (workspace_dir / "logs").mkdir(exist_ok=True)
    (workspace_dir / "scripts" / "ok.py").write_text("print('ok')\n", encoding="utf-8")
    (workspace_dir / "jobs" / "okjob").mkdir()
    (workspace_dir / "jobs" / "okjob" / "job.yaml").write_text(
        "name: okjob\ntasks:\n  - name: t1\n    script: ok.py\n"
        "    retry:\n      attempts: 1\n      delay_seconds: 0\n",
        encoding="utf-8",
    )
    global_url = db_url(dataforge_home / "dataforge.db")
    with Session(init_db(global_url)) as session:
        session.add(JobRunORM(id="r9", workspace="test_ws", job="okjob", status="pending"))
        session.commit()

    async def _run():
        broadcaster = get_broadcaster("test_ws")
        q = broadcaster.subscribe()
        try:
            await jobs_router._run_job_background(
                "r9", "okjob", workspace_dir, db_url(workspace_dir / "dataforge.db"), global_url, "test_ws")
            await asyncio.sleep(0.1)
            return [q.get_nowait()["status"] for _ in range(q.qsize())]
        finally:
            broadcaster.unsubscribe(q)

    assert asyncio.run(_run()) == ["pending", "running", "success"]
