"""`dataforge execucoes logs <id>` prints the output a task stored (df-ahua.16.2).

It used to look for logs/execucao_<id>_*.log, which nothing writes, so it always failed: the
engine keeps each task's stdout and stderr in execucoes.output."""
import pytest
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from dataforge.cli import app
from dataforge.compat import db_url
from dataforge.db import ExecucaoORM, init_db, utcnow


def _logs(*args: str):
    return CliRunner().invoke(app, ["execucoes", "logs", *args, "--workspace", "test_ws"])


def _only_row(url: str) -> ExecucaoORM:
    with Session(init_db(url)) as session:
        (row,) = session.query(ExecucaoORM).all()
        return row


def test_logs_prints_the_output_of_a_task_run_by_jobs_run(simple_job_dir, workspace_dir):
    """`jobs run` writes <ws>/data/dataforge.db, the store `execucoes list` and `logs` read."""
    (workspace_dir / "scripts" / "hello.py").write_bytes(
        b"print('hello')\nprint('[bold]not markup[/bold]')\n")
    ran = CliRunner().invoke(app, ["jobs", "run", "test_job", "--workspace", "test_ws"])
    assert ran.exit_code == 0, ran.output
    row = _only_row(db_url(workspace_dir / "data" / "dataforge.db"))

    result = _logs(str(row.id))

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0] == f"--- execução #{row.id} · test_job/t1 · success ---"
    assert lines[1:] == ["hello", "[bold]not markup[/bold]"]   # printed as stored, not as markup


def test_logs_of_an_unknown_id_says_so_and_exits_1(workspace_dir):
    result = _logs("999")
    assert result.exit_code == 1
    assert "Execução #999 não encontrada." in result.output


@pytest.mark.parametrize("output", [None, ""])
def test_logs_of_a_task_without_output_says_so_and_exits_1(workspace_dir, output):
    (workspace_dir / "data").mkdir()
    url = db_url(workspace_dir / "data" / "dataforge.db")
    with Session(init_db(url)) as session:
        session.add(ExecucaoORM(job="j", task="t", status="running", inicio=utcnow(), output=output))
        session.commit()
    row = _only_row(url)

    result = _logs(str(row.id))

    assert result.exit_code == 1
    assert f"Execução #{row.id} não tem saída gravada (status: running)" in result.output


def test_logs_reads_the_database_given_by_db(tmp_path, workspace_dir):
    url = db_url(tmp_path / "other.db")
    with Session(init_db(url)) as session:
        session.add(ExecucaoORM(job="j", task="t", status="failed", inicio=utcnow(),
                                output="boom\n"))
        session.commit()
    row = _only_row(url)

    result = _logs(str(row.id), "--db", url)

    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[1:] == ["boom"]


@pytest.mark.xfail(strict=True, reason=(
    "df-ahua.16.14: `dataforge run` writes <ws>/dataforge.db, `execucoes list/logs` read "
    "<ws>/data/dataforge.db"))
def test_logs_finds_a_task_run_by_dataforge_run(simple_job_dir, workspace_dir):
    ran = CliRunner().invoke(app, ["run", "test_ws", "test_job"])
    assert ran.exit_code == 0, ran.output
    row = _only_row(db_url(workspace_dir / "dataforge.db"))

    result = _logs(str(row.id))

    assert result.exit_code == 0, result.output
    assert "hello" in result.output
