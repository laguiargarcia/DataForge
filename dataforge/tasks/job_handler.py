from pathlib import Path

from ..models import Job, Task


class JobHandler:
    type_name = "job"
    required_fields = ("job",)
    forces_run_failure = False

    def run(self, task: Task, job: Job, workspace_dir: Path, db_url: str, run_id: str | None) -> int:
        if not task.job:
            raise ValueError(f"Task '{task.name}' type=job requires 'job:'")
        from ..parser import load_job
        from ..engine import run_job, run_status
        referenced_job = load_job(workspace_dir, task.job)
        results = run_job(referenced_job, workspace_dir, db_url, run_id=run_id)
        return 0 if run_status(referenced_job, results) == "success" else 1
