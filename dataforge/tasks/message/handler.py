from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from ...compat import global_db_url
from ...db import ExecucaoORM, JobRunORM, init_db
from ...models import Job, Task
from .channels import get_channel
from .dispatch import deliver_all
from .render import build_variables, render


def _load_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _collect_task_results(job_name: str, run_id: str | None,
                          db_url: str) -> list[dict]:
    """Read all ExecucaoORM rows for this run."""
    eng = init_db(db_url)
    with Session(eng) as s:
        q = s.query(ExecucaoORM).filter(ExecucaoORM.job == job_name)
        if run_id is not None:
            q = q.filter(ExecucaoORM.run_id == run_id)
        rows = q.all()
        return [
            {
                "name": r.task,
                "status": r.status,
                "rc": 0 if r.status == "success" else (None if r.status == "skipped" else 1),
                "output": r.output,
            }
            for r in rows
        ]


def _run_summary(run_id: str | None, fallback_started: datetime) -> tuple[datetime, str]:
    if not run_id:
        return fallback_started, "manual"
    geng = init_db(global_db_url())
    with Session(geng) as s:
        run = s.get(JobRunORM, run_id)
        if not run:
            return fallback_started, "manual"
        return (run.started_at or fallback_started), (run.trigger or "manual")


class MessageHandler:
    type_name = "message"
    required_fields = ()
    forces_run_failure = False

    def run(self, task: Task, job: Job, workspace_dir: Path,
            db_url: str, run_id: str | None) -> int:
        started_at = datetime.utcnow()
        db_engine = init_db(db_url)
        with Session(db_engine) as s:
            row = ExecucaoORM(
                job=job.name, task=task.name,
                status="running", inicio=started_at,
                run_id=run_id,
            )
            s.add(row)
            s.commit()
            row_id = row.id

        try:
            channels = task.params.get("channels") or []
            recipients = task.params.get("recipients") or {}
            subject_tpl = task.params.get("subject", "")
            body_tpl = task.params.get("body", "")

            if not channels:
                raise ValueError("message task requires params.channels (list)")

            targets = []
            for ch_name in channels:
                ch = get_channel(ch_name)
                if ch_name not in recipients:
                    raise ValueError(
                        f"missing recipient for channel '{ch_name}' in params.recipients"
                    )
                targets.append((ch, recipients[ch_name]))

            env: dict[str, str] = {}
            if job.env_file:
                env.update(_load_env_file(workspace_dir / job.env_file))

            run_started, trigger = _run_summary(run_id, started_at)
            task_results = _collect_task_results(
                job_name=job.name, run_id=run_id, db_url=db_url,
            )
            pre_status = "failed" if any(
                r["status"] == "failed" for r in task_results
                if r["name"] != task.name
            ) else "success"

            siblings = [r for r in task_results if r["name"] != task.name]
            vars = build_variables(
                workspace=workspace_dir.name, job=job.name,
                trigger=trigger, run_id=run_id or "",
                status=pre_status,
                started_at=run_started, finished_at=datetime.utcnow(),
                task_results=siblings,
            )
            subject = render(subject_tpl, vars)
            body = render(body_tpl, vars)

            deliver_all(targets, subject=subject, body=body, env=env)

            fim = datetime.utcnow()
            with Session(db_engine) as s:
                row = s.get(ExecucaoORM, row_id)
                row.status = "success"
                row.fim = fim
                row.duracao_segundos = (fim - started_at).total_seconds()
                row.output = f"sent via: {', '.join(channels)}"
                s.commit()
            return 0
        except Exception as e:
            fim = datetime.utcnow()
            with Session(db_engine) as s:
                row = s.get(ExecucaoORM, row_id)
                row.status = "failed"
                row.fim = fim
                row.duracao_segundos = (fim - started_at).total_seconds()
                row.output = f"{type(e).__name__}: {e}"
                s.commit()
            return 1
