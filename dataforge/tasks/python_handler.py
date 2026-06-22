import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from ..db import ExecucaoORM, init_db
from ..models import Job, Task


def _load_env_file(path: Path) -> dict[str, str]:
    env = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


class PythonHandler:
    type_name = "python"
    required_fields = ("script",)
    forces_run_failure = False

    def run(self, task: Task, job: Job, workspace_dir: Path, db_url: str, run_id: str | None) -> int:
        if not task.script:
            raise ValueError(f"Task '{task.name}' type=python requires 'script:'")

        db_engine = init_db(db_url)
        # task.script is RELATIVE TO scripts/ (bare, no 'scripts/' prefix); scripts/ is the
        # enforced root for all workspace scripts.
        script_path = workspace_dir / "scripts" / task.script

        with Session(db_engine) as session:
            execucao = ExecucaoORM(
                job=job.name, task=task.name,
                status="running", inicio=datetime.utcnow(),
                run_id=run_id,
            )
            session.add(execucao)
            session.commit()
            execucao_id = execucao.id

        env = os.environ.copy()
        if job.env_file:
            env.update(_load_env_file(workspace_dir / job.env_file))

        args = [sys.executable, str(script_path)]
        for k, v in task.params.items():
            args += [f"--{k}", str(v)]

        proc = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, env=env,
        )
        stdout_output, _ = proc.communicate()
        fim = datetime.utcnow()

        with Session(db_engine) as session:
            execucao = session.get(ExecucaoORM, execucao_id)
            if execucao is None:
                logging.getLogger(__name__).error(
                    "ExecucaoORM row %s not found during update", execucao_id
                )
                return proc.returncode
            execucao.status = "success" if proc.returncode == 0 else "failed"
            execucao.fim = fim
            execucao.duracao_segundos = (fim - execucao.inicio).total_seconds()
            execucao.output = stdout_output
            session.commit()

        return proc.returncode
