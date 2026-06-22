import re
import shutil
from pathlib import Path
import yaml
from .models import (
    Workspace,
    Task,
    Job,
    Connection,
    TriggerModel,
    TriggerJobEntry,
    RetryConfig,
    DependencyEdge,
)

_DEFAULT_RETRY = RetryConfig().model_dump()  # {"attempts": 3, "delay_seconds": 30}
_VALID_JOB_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


def is_valid_job_name(name: str) -> bool:
    """A job name must match ^[A-Za-z0-9_-]+$.

    The job name is a directory name (``jobs/<name>/``), so this is path-traversal
    defense — it rejects ``../``, ``/``, spaces, and any other unsafe characters.
    """
    return bool(_VALID_JOB_NAME.match(name))


def _compact_edge(edge: DependencyEdge):
    """Collapse a single 'succeeded' edge to a bare task-name string; else {task, when}."""
    if edge.when == ["succeeded"]:
        return edge.task
    return {"task": edge.task, "when": list(edge.when)}


def _compact_task_dict(task: Task) -> dict:
    out: dict = {"name": task.name}
    # type: omit when it equals what model inference would produce
    inferred = (
        "python"
        if (task.script and not task.job)
        else ("job" if (task.job and not task.script) else None)
    )
    if task.type is not None and task.type != inferred:
        out["type"] = task.type
    if task.script is not None:
        out["script"] = task.script
    if task.job is not None:
        out["job"] = task.job
    if task.params:
        out["params"] = dict(task.params)
    if task.depends_on:
        out["depends_on"] = [_compact_edge(e) for e in task.depends_on]
    if task.match != "all":
        out["match"] = task.match
    if task.retry.model_dump() != _DEFAULT_RETRY:
        out["retry"] = task.retry.model_dump()
    if task.active is not True:
        out["active"] = task.active
    return out


def _compact_job_dict(job: Job) -> dict:
    out: dict = {"name": job.name}
    if job.retry.model_dump() != _DEFAULT_RETRY:
        out["retry"] = job.retry.model_dump()
    if job.env_file is not None:
        out["env_file"] = job.env_file
    if job.schedule is not None:
        out["schedule"] = job.schedule
    if job.parameters:
        out["parameters"] = [p.model_dump() for p in job.parameters]
    out["tasks"] = [_compact_task_dict(t) for t in job.tasks]
    return out


def load_workspace(workspace_dir: Path) -> Workspace:
    with open(workspace_dir / "workspace.yaml") as f:
        data = yaml.safe_load(f)
    return Workspace(**data)


def load_job(workspace_dir: Path, job_name: str) -> Job:
    job_dir = workspace_dir / "jobs" / job_name
    with open(job_dir / "job.yaml") as f:
        job_data = yaml.safe_load(f)
    tasks = [Task(**t) for t in job_data.pop("tasks", [])]
    return Job(tasks=tasks, **job_data)


def save_job(workspace_dir: Path, job: Job) -> None:
    """Write jobs/<job.name>/job.yaml as compact YAML.

    Creates the job dir with mkdir(parents=True, exist_ok=True) — the jobs/ parent
    may not exist in a fresh workspace. sort_keys=False is MANDATORY: PyYAML's default
    re-alphabetizes every mapping, which would reorder keys (moving tasks: above name:)
    and defeat the compact serializer.
    """
    job_dir = workspace_dir / "jobs" / job.name
    job_dir.mkdir(parents=True, exist_ok=True)
    with open(job_dir / "job.yaml", "w") as f:
        yaml.dump(_compact_job_dict(job), f, allow_unicode=True, sort_keys=False)


def delete_job(workspace_dir: Path, job_name: str) -> None:
    """Remove the jobs/<job_name>/ directory. Idempotent: missing dir is a no-op."""
    job_dir = workspace_dir / "jobs" / job_name
    if job_dir.exists():
        shutil.rmtree(job_dir)


def load_jobs(workspace_dir: Path) -> list[Job]:
    jobs_dir = workspace_dir / "jobs"
    jobs = []
    if jobs_dir.exists():
        for job_dir in sorted(d for d in jobs_dir.iterdir() if d.is_dir()):
            jobs.append(load_job(workspace_dir, job_dir.name))
    return jobs


def load_trigger(workspace_dir: Path, trigger_name: str) -> TriggerModel:
    path = workspace_dir / "triggers" / f"{trigger_name}.yaml"
    with open(path) as f:
        data = yaml.safe_load(f)
    return TriggerModel(**data)


def load_triggers(workspace_dir: Path) -> list[TriggerModel]:
    triggers_dir = workspace_dir / "triggers"
    if not triggers_dir.exists():
        return []
    result = []
    for p in sorted(triggers_dir.glob("*.yaml")):
        result.append(load_trigger(workspace_dir, p.stem))
    return result


def save_trigger(workspace_dir: Path, trigger: TriggerModel) -> None:
    triggers_dir = workspace_dir / "triggers"
    triggers_dir.mkdir(exist_ok=True)
    path = triggers_dir / f"{trigger.name}.yaml"
    with open(path, "w") as f:
        yaml.dump(trigger.model_dump(), f, allow_unicode=True)


def delete_trigger(workspace_dir: Path, trigger_name: str) -> None:
    path = workspace_dir / "triggers" / f"{trigger_name}.yaml"
    path.unlink(missing_ok=True)


def load_connections(workspace_dir: Path) -> dict[str, Connection]:
    connections_file = workspace_dir / "connections" / "connections.yaml"
    with open(connections_file) as f:
        data = yaml.safe_load(f)
    return {name: Connection(**conn) for name, conn in data["connections"].items()}
