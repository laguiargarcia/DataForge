"""API CRUD tests for job create/edit/delete (Phase B, Tasks 5-10).

Mirrors tests/test_triggers.py: test_client + workspace_dir + dataforge_home fixtures.
"""
from pathlib import Path


def _write_job(workspace_dir: Path, name: str, body: str) -> None:
    d = workspace_dir / "jobs" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "job.yaml").write_text(body)


# ---------------------------------------------------------------------------
# Task 5: GET one + job-name validation
# ---------------------------------------------------------------------------

def test_get_job_returns_full_definition(test_client, workspace_dir):
    """GET /{ws}/jobs/{job} returns the full job dict including tasks."""
    _write_job(
        workspace_dir, "etl",
        "name: etl\ntasks:\n  - name: t1\n    script: a.py\n",
    )
    resp = test_client.get("/api/workspaces/test_ws/jobs/etl")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "etl"
    assert [t["name"] for t in body["tasks"]] == ["t1"]


def test_get_job_404_when_missing(test_client, workspace_dir):
    resp = test_client.get("/api/workspaces/test_ws/jobs/nope")
    assert resp.status_code == 404


def test_get_job_404_when_workspace_missing(test_client):
    resp = test_client.get("/api/workspaces/no_ws/jobs/etl")
    assert resp.status_code == 404


def test_get_job_no_path_leak_on_404(test_client):
    """404 detail must EQUAL the clean fixed string — no fs path leak from resolve_workspace."""
    resp = test_client.get("/api/workspaces/ghost_ws/jobs/etl")
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert detail == "Workspace 'ghost_ws' not found"
    assert "workspaces/" not in detail
    assert not detail.startswith("/")
    assert ":\\" not in detail  # no Windows absolute path either


# ---------------------------------------------------------------------------
# Task 6: DAG validation helper
# ---------------------------------------------------------------------------

def test_validate_job_dag_rejects_cycle():
    from dataforge.models import Job, Task
    from dataforge.api.routers.jobs import _validate_job_dag
    from fastapi import HTTPException
    import pytest
    job = Job(name="j", tasks=[
        Task(name="a", script="s/a.py", depends_on=["b"]),
        Task(name="b", script="s/b.py", depends_on=["a"]),
    ])
    with pytest.raises(HTTPException) as exc:
        _validate_job_dag(job)
    assert exc.value.status_code == 400
    assert "cycle" in exc.value.detail.lower()


def test_validate_job_dag_rejects_dangling_dep():
    from dataforge.models import Job, Task
    from dataforge.api.routers.jobs import _validate_job_dag
    from fastapi import HTTPException
    import pytest
    job = Job(name="j", tasks=[Task(name="a", script="s/a.py", depends_on=["ghost"])])
    with pytest.raises(HTTPException) as exc:
        _validate_job_dag(job)
    assert exc.value.status_code == 400
    assert "ghost" in exc.value.detail


def test_validate_job_dag_rejects_duplicate_task_names():
    from dataforge.models import Job, Task
    from dataforge.api.routers.jobs import _validate_job_dag
    from fastapi import HTTPException
    import pytest
    job = Job(name="j", tasks=[
        Task(name="dup", script="s/a.py"),
        Task(name="dup", script="s/b.py"),
    ])
    with pytest.raises(HTTPException) as exc:
        _validate_job_dag(job)
    assert exc.value.status_code == 400
    assert "duplicate" in exc.value.detail.lower()


def test_validate_job_dag_accepts_valid_dag():
    from dataforge.models import Job, Task
    from dataforge.api.routers.jobs import _validate_job_dag
    job = Job(name="j", tasks=[
        Task(name="a", script="s/a.py"),
        Task(name="b", script="s/b.py", depends_on=["a"]),
    ])
    warnings = _validate_job_dag(job)
    assert isinstance(warnings, list)


# ---------------------------------------------------------------------------
# Task 7: POST create (201 / 409) + missing-script / missing-job warnings
# ---------------------------------------------------------------------------

def test_post_job_creates_201(test_client, workspace_dir):
    payload = {
        "name": "newjob",
        "tasks": [{"name": "t1", "script": "a.py"}],
    }
    resp = test_client.post("/api/workspaces/test_ws/jobs", json=payload)
    assert resp.status_code == 201
    assert (workspace_dir / "jobs" / "newjob" / "job.yaml").exists()


def test_post_job_409_on_existing(test_client, workspace_dir):
    _write_job(workspace_dir, "dupe", "name: dupe\ntasks:\n  - name: t1\n    script: a.py\n")
    payload = {"name": "dupe", "tasks": [{"name": "t1", "script": "a.py"}]}
    resp = test_client.post("/api/workspaces/test_ws/jobs", json=payload)
    assert resp.status_code == 409


def test_post_job_400_on_cycle(test_client, workspace_dir):
    payload = {"name": "cyc", "tasks": [
        {"name": "a", "script": "s/a.py", "depends_on": ["b"]},
        {"name": "b", "script": "s/b.py", "depends_on": ["a"]},
    ]}
    resp = test_client.post("/api/workspaces/test_ws/jobs", json=payload)
    assert resp.status_code == 400


def test_post_job_400_on_dangling_dep(test_client, workspace_dir):
    payload = {"name": "dang", "tasks": [
        {"name": "a", "script": "s/a.py", "depends_on": ["ghost"]},
    ]}
    resp = test_client.post("/api/workspaces/test_ws/jobs", json=payload)
    assert resp.status_code == 400


def test_post_job_400_on_duplicate_tasks(test_client, workspace_dir):
    payload = {"name": "dups", "tasks": [
        {"name": "x", "script": "s/a.py"},
        {"name": "x", "script": "s/b.py"},
    ]}
    resp = test_client.post("/api/workspaces/test_ws/jobs", json=payload)
    assert resp.status_code == 400


def test_post_job_400_on_bad_name(test_client, workspace_dir):
    for bad in ["../evil", "a/b"]:
        payload = {"name": bad, "tasks": [{"name": "t1", "script": "a.py"}]}
        resp = test_client.post("/api/workspaces/test_ws/jobs", json=payload)
        assert resp.status_code == 400, f"name={bad!r}"


def test_post_job_warns_on_missing_script_but_saves(test_client, workspace_dir):
    """Missing script file is WARN-only: save succeeds (201) with warnings[].

    `script` is BARE (relative to scripts/); the check resolves it at
    workspace_dir/scripts/<bare>, so a bare value with no file under scripts/ warns.
    """
    payload = {"name": "draft", "tasks": [{"name": "t1", "script": "not_yet.py"}]}
    resp = test_client.post("/api/workspaces/test_ws/jobs", json=payload)
    assert resp.status_code == 201
    assert (workspace_dir / "jobs" / "draft" / "job.yaml").exists()
    warnings = resp.json().get("warnings", [])
    assert any("not_yet.py" in w for w in warnings)


def test_post_job_no_warn_when_nested_bare_script_exists(test_client, workspace_dir):
    """A NESTED bare script that EXISTS under scripts/ produces NO warning.

    Proves the new resolution: task.script 'cleansed2curated/x.py' resolves to
    workspace_dir/scripts/cleansed2curated/x.py (no 'scripts/' prefix in the value).
    """
    _write_script(workspace_dir, "scripts/cleansed2curated/x.py")
    payload = {"name": "wired", "tasks": [{"name": "t1", "script": "cleansed2curated/x.py"}]}
    resp = test_client.post("/api/workspaces/test_ws/jobs", json=payload)
    assert resp.status_code == 201
    warnings = resp.json().get("warnings", [])
    assert warnings == []
    # Round-trip: the bare nested value is persisted and read back verbatim.
    got = test_client.get("/api/workspaces/test_ws/jobs/wired").json()
    assert got["tasks"][0]["script"] == "cleansed2curated/x.py"


def test_post_job_warns_on_missing_job_target(test_client, workspace_dir):
    """A type=job task pointing at a non-existent job is WARN-only. Save succeeds (201)."""
    payload = {"name": "caller", "tasks": [{"name": "c1", "job": "no_such_job"}]}
    resp = test_client.post("/api/workspaces/test_ws/jobs", json=payload)
    assert resp.status_code == 201
    assert (workspace_dir / "jobs" / "caller" / "job.yaml").exists()
    warnings = resp.json().get("warnings", [])
    assert any("no_such_job" in w for w in warnings)


def test_post_job_no_path_leak_on_404(test_client):
    """404 detail must EQUAL the clean fixed string — no fs path from resolve_workspace's ValueError."""
    payload = {"name": "x", "tasks": [{"name": "t1", "script": "s/a.py"}]}
    resp = test_client.post("/api/workspaces/ghost_ws/jobs", json=payload)
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert detail == "Workspace 'ghost_ws' not found"
    assert "workspaces/" not in detail
    assert not detail.startswith("/")


def test_post_job_422_on_neither_script_nor_job(test_client, workspace_dir):
    """D-422: a task with neither script nor job is a model-level Pydantic failure -> 422, NOT 400."""
    payload = {"name": "bad", "tasks": [{"name": "t1"}]}
    resp = test_client.post("/api/workspaces/test_ws/jobs", json=payload)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Task 8: PUT update (404 if missing, 400 on rename / dup, D-PRESERVE)
# ---------------------------------------------------------------------------

def test_put_job_updates(test_client, workspace_dir):
    _write_job(workspace_dir, "edit", "name: edit\ntasks:\n  - name: t1\n    script: a.py\n")
    payload = {"name": "edit", "tasks": [
        {"name": "t1", "script": "a.py"},
        {"name": "t2", "script": "b.py", "depends_on": ["t1"]},
    ]}
    resp = test_client.put("/api/workspaces/test_ws/jobs/edit", json=payload)
    assert resp.status_code == 200
    from dataforge.parser import load_job
    reloaded = load_job(workspace_dir, "edit")
    assert [t.name for t in reloaded.tasks] == ["t1", "t2"]


def test_put_job_404_when_missing(test_client, workspace_dir):
    payload = {"name": "ghost", "tasks": [{"name": "t1", "script": "a.py"}]}
    resp = test_client.put("/api/workspaces/test_ws/jobs/ghost", json=payload)
    assert resp.status_code == 404


def test_put_job_404_when_workspace_missing(test_client):
    payload = {"name": "etl", "tasks": [{"name": "t1", "script": "s/a.py"}]}
    resp = test_client.put("/api/workspaces/ghost_ws/jobs/etl", json=payload)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Workspace 'ghost_ws' not found"


def test_put_preserves_parameters(test_client, workspace_dir):
    """D-PRESERVE (API round-trip): a job with job-level parameters + schedule, PUT with a
    task-only change whose body STILL carries those keys, keeps them intact on reload."""
    from dataforge.parser import load_job
    _write_job(
        workspace_dir, "withp",
        "name: withp\n"
        "schedule: '0 2 * * *'\n"
        "parameters:\n  - name: env\n    type: string\n    default: dev\n"
        "tasks:\n  - name: t1\n    script: a.py\n",
    )
    payload = {
        "name": "withp",
        "schedule": "0 2 * * *",
        "parameters": [{"name": "env", "type": "string", "default": "dev", "description": None}],
        "tasks": [
            {"name": "t1", "script": "a.py"},
            {"name": "t2", "script": "b.py", "depends_on": ["t1"]},
        ],
    }
    resp = test_client.put("/api/workspaces/test_ws/jobs/withp", json=payload)
    assert resp.status_code == 200
    reloaded = load_job(workspace_dir, "withp")
    assert [t.name for t in reloaded.tasks] == ["t1", "t2"]   # task change applied
    assert reloaded.schedule == "0 2 * * *"                    # schedule NOT wiped
    assert len(reloaded.parameters) == 1 and reloaded.parameters[0].name == "env"  # parameters kept


def test_put_job_400_on_duplicate_tasks(test_client, workspace_dir):
    _write_job(workspace_dir, "edit2", "name: edit2\ntasks:\n  - name: t1\n    script: a.py\n")
    payload = {"name": "edit2", "tasks": [
        {"name": "x", "script": "a.py"},
        {"name": "x", "script": "b.py"},
    ]}
    resp = test_client.put("/api/workspaces/test_ws/jobs/edit2", json=payload)
    assert resp.status_code == 400


def test_put_job_400_when_body_name_differs_from_path(test_client, workspace_dir):
    """D1: job name is immutable. A PUT whose body.name != path {job} is 400, NOT a silent rename."""
    _write_job(workspace_dir, "keep", "name: keep\ntasks:\n  - name: t1\n    script: a.py\n")
    payload = {"name": "renamed", "tasks": [{"name": "t1", "script": "a.py"}]}
    resp = test_client.put("/api/workspaces/test_ws/jobs/keep", json=payload)
    assert resp.status_code == 400
    assert "immutable" in resp.json()["detail"].lower()
    # the original job is untouched (no rename, no new dir)
    assert (workspace_dir / "jobs" / "keep" / "job.yaml").exists()
    assert not (workspace_dir / "jobs" / "renamed").exists()


# ---------------------------------------------------------------------------
# Task 9: reference scan (D2) + DELETE (204)
# ---------------------------------------------------------------------------

def test_scan_references_finds_trigger_task_schedule(test_client, workspace_dir, dataforge_home):
    """references endpoint reports a referencing trigger, a type=job task, and a schedule row."""
    from dataforge.db import init_db, ScheduleORM
    from dataforge.compat import db_url as make_db_url
    from sqlalchemy.orm import Session

    # the target job
    _write_job(workspace_dir, "target", "name: target\ntasks:\n  - name: t1\n    script: a.py\n")
    # another job that calls target via a type=job task
    _write_job(
        workspace_dir, "caller",
        "name: caller\ntasks:\n  - name: c1\n    job: target\n",
    )
    # a trigger referencing target
    trig_dir = workspace_dir / "triggers"
    trig_dir.mkdir(exist_ok=True)
    (trig_dir / "nightly.yaml").write_text(
        "name: nightly\ncron: '0 2 * * *'\nenabled: true\n"
        "jobs:\n  - workspace: test_ws\n    job: target\n    params: {}\n"
    )
    # a schedule row keyed test_ws:target
    engine = init_db(make_db_url(dataforge_home / "dataforge.db"))
    with Session(engine) as s:
        s.add(ScheduleORM(id="test_ws:target", workspace="test_ws", job="target",
                          cron="0 2 * * *", enabled=True))
        s.commit()

    resp = test_client.get("/api/workspaces/test_ws/jobs/target/references")
    assert resp.status_code == 200
    refs = resp.json()
    assert "nightly" in refs["triggers"]
    assert any("caller" in t for t in refs["tasks"])
    assert refs["schedule"] is True


def test_scan_references_empty_for_unreferenced(test_client, workspace_dir, dataforge_home):
    """An unreferenced job reports empty triggers/tasks and schedule False."""
    _write_job(workspace_dir, "lonely", "name: lonely\ntasks:\n  - name: t1\n    script: a.py\n")
    resp = test_client.get("/api/workspaces/test_ws/jobs/lonely/references")
    assert resp.status_code == 200
    refs = resp.json()
    assert refs["triggers"] == []
    assert refs["tasks"] == []
    assert refs["schedule"] is False


def test_get_references_404_when_missing(test_client, workspace_dir):
    resp = test_client.get("/api/workspaces/test_ws/jobs/nope/references")
    assert resp.status_code == 404


def test_delete_job_204(test_client, workspace_dir):
    _write_job(workspace_dir, "trash", "name: trash\ntasks:\n  - name: t1\n    script: a.py\n")
    resp = test_client.delete("/api/workspaces/test_ws/jobs/trash")
    assert resp.status_code == 204
    assert not (workspace_dir / "jobs" / "trash").exists()


def test_delete_job_404_when_missing(test_client, workspace_dir):
    resp = test_client.delete("/api/workspaces/test_ws/jobs/nope")
    assert resp.status_code == 404


def test_delete_job_404_when_workspace_missing(test_client):
    resp = test_client.delete("/api/workspaces/ghost_ws/jobs/etl")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Workspace 'ghost_ws' not found"


# ---------------------------------------------------------------------------
# Task 10: route-ordering regression gate
# ---------------------------------------------------------------------------

def test_existing_job_routes_still_resolve(test_client, workspace_dir):
    """New CRUD routes must NOT shadow the pre-existing /schedule and /run routes."""
    _write_job(
        workspace_dir, "etl",
        "name: etl\ntasks:\n  - name: t1\n    script: a.py\n"
        "    retry: {attempts: 1, delay_seconds: 0}\n",
    )
    # /schedule resolves (404 = no schedule row, NOT 405/route-not-found)
    r1 = test_client.get("/api/workspaces/test_ws/jobs/etl/schedule")
    assert r1.status_code in (200, 404)
    # /run resolves (202 accepted, NOT 405)
    r2 = test_client.post("/api/workspaces/test_ws/jobs/etl/run")
    assert r2.status_code == 202
    # the new GET-one still returns the full job (not swallowed by /schedule's prefix)
    r3 = test_client.get("/api/workspaces/test_ws/jobs/etl")
    assert r3.status_code == 200 and r3.json()["name"] == "etl"


# ---------------------------------------------------------------------------
# UX enhancement: GET /{ws}/scripts (BARE POSIX script paths, relative to scripts/)
# ---------------------------------------------------------------------------

def _write_script(workspace_dir: Path, rel: str) -> None:
    """Write a script file at workspace_dir/<rel> (rel includes the scripts/ dir on disk)."""
    p = workspace_dir / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("# script\n")


def test_scripts_lists_nested_sorted(test_client, workspace_dir):
    """Scripts are nested under layer folders; the endpoint recurses and emits BARE
    (relative-to-scripts/) POSIX paths, sorted — matching the task.script convention."""
    _write_script(workspace_dir, "scripts/cleansed2curated/fact_fatura_mes.py")
    _write_script(workspace_dir, "scripts/raw2cleansed/transactions.py")
    _write_script(workspace_dir, "scripts/top_level.py")
    resp = test_client.get("/api/workspaces/test_ws/scripts")
    assert resp.status_code == 200
    scripts = resp.json()["scripts"]
    assert scripts == sorted(scripts)  # sorted
    # BARE paths (relative to scripts/), never the 'scripts/' prefix.
    assert "cleansed2curated/fact_fatura_mes.py" in scripts
    assert "raw2cleansed/transactions.py" in scripts
    assert "top_level.py" in scripts
    assert not any(s.startswith("scripts/") for s in scripts)
    # forward slashes only (POSIX), never backslashes
    assert all("\\" not in s for s in scripts)


def test_scripts_excludes_pycache(test_client, workspace_dir):
    """Any path containing a __pycache__ segment is skipped (compiled artifacts)."""
    _write_script(workspace_dir, "scripts/real.py")
    _write_script(workspace_dir, "scripts/__pycache__/real.cpython-312.py")
    _write_script(workspace_dir, "scripts/sub/__pycache__/x.py")
    resp = test_client.get("/api/workspaces/test_ws/scripts")
    assert resp.status_code == 200
    scripts = resp.json()["scripts"]
    assert "real.py" in scripts
    assert not any(s.startswith("scripts/") for s in scripts)
    assert not any("__pycache__" in s for s in scripts)


def test_scripts_empty_when_no_scripts_dir(test_client, dataforge_home):
    """A workspace with NO scripts/ dir returns {'scripts': []}, not an error."""
    ws = dataforge_home / "workspaces" / "noscripts_ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: noscripts_ws\n")
    resp = test_client.get("/api/workspaces/noscripts_ws/scripts")
    assert resp.status_code == 200
    assert resp.json() == {"scripts": []}


def test_scripts_404_when_workspace_missing(test_client):
    """404 detail must EQUAL the clean fixed string — no fs path leak from resolve_workspace."""
    resp = test_client.get("/api/workspaces/ghost_ws/scripts")
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert detail == "Workspace 'ghost_ws' not found"
    assert "workspaces/" not in detail
    assert not detail.startswith("/")
    assert ":\\" not in detail
