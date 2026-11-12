import pytest
import yaml
from pathlib import Path
from pydantic import ValidationError
from dataforge.parser import load_workspace, load_job
from dataforge.models import Job, Task


def write_job_yaml(job_dir: Path, content: str):
    """Write job.yaml content to a job directory."""
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "job.yaml").write_text(content)


def test_task_missing_script_and_job(workspace_dir):
    """Task with neither script nor job must fail validation."""
    job_dir = workspace_dir / "jobs" / "bad_job"
    write_job_yaml(job_dir, "name: bad_job\ntasks:\n  - name: t1\n")
    with pytest.raises(ValidationError):
        load_job(workspace_dir, "bad_job")


def test_task_both_script_and_job(workspace_dir):
    """Task with both script and job must fail validation."""
    job_dir = workspace_dir / "jobs" / "both_job"
    write_job_yaml(
        job_dir,
        "name: both_job\ntasks:\n"
        "  - name: t1\n    script: x.py\n    job: other_job\n",
    )
    with pytest.raises(ValidationError):
        load_job(workspace_dir, "both_job")


def test_valid_job_loads(workspace_dir):
    """Valid job YAML with a script task loads without error."""
    job_dir = workspace_dir / "jobs" / "good_job"
    write_job_yaml(
        job_dir,
        "name: good_job\ntasks:\n"
        "  - name: t1\n    script: hello.py\n"
        "    retry:\n      attempts: 1\n      delay_seconds: 0\n",
    )
    job = load_job(workspace_dir, "good_job")
    assert job.name == "good_job"
    assert len(job.tasks) == 1
    assert job.tasks[0].name == "t1"


def test_load_workspace_valid(workspace_dir):
    """Valid workspace.yaml loads without error."""
    ws = load_workspace(workspace_dir)
    assert ws.name == "test_ws"


def test_load_workspace_missing_file(tmp_path):
    """Missing workspace.yaml raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        load_workspace(tmp_path)


def test_load_job_with_parameters_block(tmp_path):
    """Job with declared parameters block parses correctly and round-trips."""
    workspace_dir = tmp_path / "ws"
    workspace_dir.mkdir()
    (workspace_dir / "workspace.yaml").write_text("name: test\n")
    job_dir = workspace_dir / "jobs" / "demo"
    job_dir.mkdir(parents=True)

    params_data = {
        "name": "demo",
        "tasks": [],
        "parameters": [
            {"name": "start_date", "type": "date", "default": "2026-01-01"},
            {"name": "mode", "type": "string", "default": "full", "description": "ingest mode"},
        ],
    }
    (job_dir / "job.yaml").write_text(yaml.safe_dump(params_data))

    job = load_job(workspace_dir, "demo")
    assert len(job.parameters) == 2
    assert job.parameters[0].name == "start_date"
    assert job.parameters[0].type == "date"
    assert job.parameters[1].description == "ingest mode"

    # Round-trip through YAML
    dumped = yaml.safe_load(yaml.safe_dump(job.model_dump()))
    reconstructed = Job(**dumped)
    assert len(reconstructed.parameters) == 2
    assert reconstructed.parameters[0].name == job.parameters[0].name
    assert reconstructed.parameters[1].description == job.parameters[1].description


def test_load_job_without_parameters_is_backward_compatible(workspace_dir):
    """job.yaml without a parameters key still loads with parameters == []."""
    job_dir = workspace_dir / "jobs" / "legacy_job"
    write_job_yaml(
        job_dir,
        "name: legacy_job\ntasks:\n  - name: step1\n    script: run.py\n",
    )
    job = load_job(workspace_dir, "legacy_job")
    assert job.parameters == []


def test_parser_accepts_when_and_match(tmp_path):
    ws = tmp_path / "ws"
    (ws / "jobs" / "j").mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    (ws / "jobs" / "j" / "job.yaml").write_text(
        "name: j\n"
        "tasks:\n"
        "  - name: a\n    script: a.py\n"
        "  - name: b\n    type: error\n    match: any\n"
        "    depends_on:\n"
        "      - task: a\n        when: [failed, skipped]\n"
    )
    job = load_job(ws, "j")
    a, b = job.tasks
    assert a.type == "python"
    assert b.type == "error"
    assert b.match == "any"
    assert b.depends_on[0].when == ["failed", "skipped"]


# --- Phase A: compact serializer + save_job / delete_job (Tasks 1-4) ---


def test_compact_job_dict_omits_defaults():
    """A task with only name+script serializes to just those keys (no retry/active/params/match/type)."""
    from dataforge.parser import _compact_job_dict
    job = Job(name="j", tasks=[Task(name="t1", script="a.py")])
    d = _compact_job_dict(job)
    assert d == {
        "name": "j",
        "tasks": [{"name": "t1", "script": "a.py"}],
    }


def test_compact_job_dict_collapses_single_succeeded_edge():
    """depends_on with a single 'succeeded' edge collapses to a bare task-name string."""
    from dataforge.parser import _compact_job_dict
    job = Job(name="j", tasks=[
        Task(name="a", script="a.py"),
        Task(name="b", script="b.py", depends_on=["a"]),
    ])
    d = _compact_job_dict(job)
    assert d["tasks"][1]["depends_on"] == ["a"]  # NOT [{"task": "a", "when": ["succeeded"]}]


def test_compact_job_dict_keeps_multi_condition_edge_expanded():
    """A non-default 'when' (e.g. completed -> succeeded+failed) stays expanded as an object."""
    from dataforge.parser import _compact_job_dict
    job = Job(name="j", tasks=[
        Task(name="a", script="a.py"),
        Task(name="b", script="b.py", depends_on=[{"task": "a", "when": "completed"}]),
    ])
    d = _compact_job_dict(job)
    assert d["tasks"][1]["depends_on"] == [{"task": "a", "when": ["succeeded", "failed"]}]


def test_compact_job_dict_preserves_task_order():
    """Task list order is file order — preserved in serialization."""
    from dataforge.parser import _compact_job_dict
    job = Job(name="j", tasks=[
        Task(name="z", script="z.py"),
        Task(name="m", script="m.py"),
        Task(name="a", script="a.py"),
    ])
    d = _compact_job_dict(job)
    assert [t["name"] for t in d["tasks"]] == ["z", "m", "a"]


def test_compact_job_dict_preserves_parameters_and_schedule():
    """D-PRESERVE at the serializer: non-default parameters[] and schedule survive a compact dump."""
    from dataforge.parser import _compact_job_dict
    job = Job(
        name="j",
        schedule="0 2 * * *",
        parameters=[{"name": "env", "type": "string", "default": "dev"}],
        tasks=[Task(name="t1", script="a.py")],
    )
    d = _compact_job_dict(job)
    assert d["schedule"] == "0 2 * * *"
    assert len(d["parameters"]) == 1
    assert d["parameters"][0]["name"] == "env"
    assert d["parameters"][0]["default"] == "dev"


def test_save_job_creates_dir_and_roundtrips(workspace_dir):
    """save_job creates jobs/<name>/ and writes YAML that load_job reads back equivalently."""
    from dataforge.parser import save_job, load_job
    job = Job(name="etl", tasks=[
        Task(name="landing2raw", script="landing2raw.py"),
        Task(name="raw2cleansed", script="raw2cleansed.py", depends_on=["landing2raw"]),
    ])
    save_job(workspace_dir, job)
    yaml_path = workspace_dir / "jobs" / "etl" / "job.yaml"
    assert yaml_path.exists()
    reloaded = load_job(workspace_dir, "etl")
    assert reloaded.name == "etl"
    assert [t.name for t in reloaded.tasks] == ["landing2raw", "raw2cleansed"]
    assert reloaded.tasks[1].depends_on[0].task == "landing2raw"
    assert reloaded.tasks[1].depends_on[0].when == ["succeeded"]


def test_save_job_yaml_is_compact(workspace_dir):
    """The written YAML must NOT contain injected defaults like 'active:' or 'match:'."""
    from dataforge.parser import save_job
    job = Job(name="etl", tasks=[Task(name="t1", script="a.py")])
    save_job(workspace_dir, job)
    text = (workspace_dir / "jobs" / "etl" / "job.yaml").read_text()
    assert "active:" not in text
    assert "match:" not in text
    assert "attempts:" not in text  # default retry omitted
    assert "params:" not in text


def test_save_job_preserves_key_and_task_order(workspace_dir):
    """sort_keys=False: written YAML keeps job-key order (name first) and task list order.
    Without sort_keys=False, yaml.dump re-alphabetizes keys and defeats the compact serializer."""
    from dataforge.parser import save_job
    job = Job(name="etl", tasks=[
        Task(name="zeta", script="z.py"),
        Task(name="alpha", script="a.py", depends_on=["zeta"]),
    ])
    save_job(workspace_dir, job)
    text = (workspace_dir / "jobs" / "etl" / "job.yaml").read_text()
    # 'name:' appears before 'tasks:' (key order preserved, not alphabetized)
    assert text.index("name:") < text.index("tasks:")
    # task order is file order (zeta before alpha), NOT alphabetized
    assert text.index("zeta") < text.index("alpha")
    # within a task, 'name:' precedes 'script:' (script value is bare, relative to scripts/)
    assert text.index("zeta") < text.index("z.py")


def test_save_job_roundtrips_parameters_and_schedule(workspace_dir):
    """D-PRESERVE: a Job carrying parameters[] + schedule round-trips them intact through save/load."""
    from dataforge.parser import save_job, load_job
    job = Job(
        name="withp",
        schedule="0 2 * * *",
        parameters=[{"name": "env", "type": "string", "default": "dev", "description": "ingest env"}],
        tasks=[Task(name="t1", script="a.py")],
    )
    save_job(workspace_dir, job)
    reloaded = load_job(workspace_dir, "withp")
    assert reloaded.schedule == "0 2 * * *"
    assert len(reloaded.parameters) == 1
    assert reloaded.parameters[0].name == "env"
    assert reloaded.parameters[0].type == "string"
    assert reloaded.parameters[0].default == "dev"
    assert reloaded.parameters[0].description == "ingest env"


def test_save_job_completed_edge_expands_and_persists(workspace_dir):
    """when: [completed] expands to [succeeded, failed] at load and that is what's written back.
    Known lossy-but-semantic-preserving FILE rewrite: the literal 'completed' token is gone, but
    the edge semantics are identical on reload."""
    from dataforge.parser import save_job, load_job
    job = Job(name="cj", tasks=[
        Task(name="a", script="a.py"),
        Task(name="b", script="b.py", depends_on=[{"task": "a", "when": "completed"}]),
    ])
    save_job(workspace_dir, job)
    text = (workspace_dir / "jobs" / "cj" / "job.yaml").read_text()
    assert "completed" not in text  # literal token NOT written
    reloaded = load_job(workspace_dir, "cj")
    assert reloaded.tasks[1].depends_on[0].when == ["succeeded", "failed"]


def test_save_job_typed_params_survive_roundtrip(workspace_dir):
    """Typed task params survive save->load with their types intact."""
    from dataforge.parser import save_job, load_job
    job = Job(name="tp", tasks=[
        Task(name="t1", script="a.py",
             params={"retries": 5, "debug": True, "cols": ["a", "b"]}),
    ])
    save_job(workspace_dir, job)
    reloaded = load_job(workspace_dir, "tp")
    p = reloaded.tasks[0].params
    assert p["retries"] == 5
    assert isinstance(p["retries"], int)
    assert p["debug"] is True
    assert p["cols"] == ["a", "b"]


def test_delete_job_removes_dir_idempotent(workspace_dir):
    """delete_job removes jobs/<name>/ entirely; a second call does not raise."""
    from dataforge.parser import save_job, delete_job
    save_job(workspace_dir, Job(name="gone", tasks=[Task(name="t1", script="a.py")]))
    job_dir = workspace_dir / "jobs" / "gone"
    assert job_dir.exists()
    delete_job(workspace_dir, "gone")
    assert not job_dir.exists()
    delete_job(workspace_dir, "gone")  # must not raise


def test_is_valid_job_name_accepts_and_rejects():
    """Name-validation helper accepts safe names and rejects path-traversal / unsafe chars."""
    from dataforge.parser import is_valid_job_name
    for good in ["etl", "my_job", "my-job", "Job123", "a"]:
        assert is_valid_job_name(good), good
    for bad in ["../evil", "a/b", "a b", "", ".", "..", "a.b", "a/", "/a", "a\\b"]:
        assert not is_valid_job_name(bad), bad


def test_compact_job_dict_keeps_type_when_nothing_can_be_inferred():
    """A task with neither script nor job has no inferred type, so an explicit type is kept."""
    from dataforge.parser import _compact_job_dict
    job = Job(name="j", tasks=[Task(name="e", type="error")])
    assert _compact_job_dict(job)["tasks"] == [{"name": "e", "type": "error"}]
