from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from ..db import ExecucaoORM, init_db
from ..models import Job, Task


class ErrorHandler:
    type_name = "error"
    required_fields = ()
    forces_run_failure = True

    def run(self, task: Task, job: Job, workspace_dir: Path, db_url: str, run_id: str | None) -> int:
        msg = task.params.get("message", "error task triggered")
        now = datetime.utcnow()
        db_engine = init_db(db_url)
        with Session(db_engine) as session:
            row = ExecucaoORM(
                job=job.name, task=task.name,
                status="failed",
                inicio=now, fim=now,
                duracao_segundos=0.0,
                output=msg,
                run_id=run_id,
            )
            session.add(row)
            session.commit()
        return 1
