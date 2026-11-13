"""Rules of dataforge/tasks/python_handler.py, the `python` task: it runs scripts/<task.script> with
the interpreter running DataForge, each param as '--<name> <value>', and the process environment plus
the job's env file (whose variables win); its row reads `running` while the script runs and then
`success` or `failed` by the exit code, with the script's stdout and stderr as its output; the
handler returns the script's exit code.

Three tests start a short Python child each; the scripts only touch the test's tmp_path."""
import json
import logging
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from dataforge.compat import db_url as make_db_url
from dataforge.db import ExecucaoORM, init_db, utcnow
from dataforge.models import Job, RetryConfig, Task
from dataforge.tasks.python_handler import PythonHandler

_NO_RETRY = RetryConfig(attempts=1, delay_seconds=0)
# Prints what the script was given and what its own task row holds while it runs.
_REPORT = """\
import json, os, sqlite3, sys
db = sys.argv[sys.argv.index("--db") + 1]
con = sqlite3.connect(db)
rows = con.execute("select status, fim, run_id from execucoes").fetchall()
con.close()
print(json.dumps({"argv": sys.argv[1:], "rows": rows, "shared": os.environ.get("SHARED"),
                  "only_os": os.environ.get("ONLY_OS"), "only_file": os.environ.get("ONLY_FILE")}))
"""


def _plant_script(ws: Path, rel: str, code: str) -> None:
    path = ws / "scripts" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(code.encode("utf-8"))


def _rows(url: str) -> list[ExecucaoORM]:
    engine = init_db(url)
    with Session(engine, expire_on_commit=False) as s:
        rows = s.query(ExecucaoORM).all()
        s.expunge_all()
    engine.dispose()
    return rows


def _run(ws: Path, script: str, params: dict, env_file=None, run_id="r1"):
    url = make_db_url(ws / "dataforge.db")
    task = Task(name="step", type="python", script=script, params=params, retry=_NO_RETRY)
    job = Job(name="nightly", tasks=[task], env_file=env_file, retry=_NO_RETRY)
    rc = PythonHandler().run(task, job, ws, url, run_id=run_id)
    return rc, url


def test_a_script_gets_its_params_and_environment_and_its_row_succeeds(tmp_path, monkeypatch):
    """A script under scripts/ gets '--<param> <str(value)>' in order and the process environment
    overlaid with the env file; meanwhile its row reads `running` for its run, with no end. When it
    exits 0 the handler returns 0 and the row is `success`, ended, its duration the time between
    start and end, and its output exactly what the script printed."""
    monkeypatch.setenv("SHARED", "from the os")
    monkeypatch.setenv("ONLY_OS", "os")
    (tmp_path / "secrets.env").write_bytes(b"SHARED=from the file\nONLY_FILE='file'\n")
    _plant_script(tmp_path, "etl/report.py", _REPORT)
    db = str(tmp_path / "dataforge.db")
    before = utcnow()

    rc, url = _run(tmp_path, "etl/report.py", {"db": db, "date": "2026-01-01", "n": 3, "flag": True},
                   env_file="secrets.env")

    assert rc == 0
    [row] = _rows(url)
    seen = json.loads(row.output)
    assert row.output == json.dumps(seen) + "\n"
    assert seen["argv"] == ["--db", db, "--date", "2026-01-01", "--n", "3", "--flag", "True"]
    assert seen["rows"] == [["running", None, "r1"]]
    assert (seen["shared"], seen["only_os"], seen["only_file"]) == ("from the file", "os", "file")
    assert (row.job, row.task, row.status, row.run_id) == ("nightly", "step", "success", "r1")
    assert before <= row.inicio <= row.fim
    assert row.duracao_segundos == (row.fim - row.inicio).total_seconds()


def test_a_failing_script_returns_its_code_and_its_row_keeps_stdout_and_stderr(tmp_path):
    """A script that exits non-zero makes the handler return that code and its row `failed`, ended,
    with what it wrote to stdout and to stderr in the output."""
    _plant_script(tmp_path, "boom.py",
                  "import sys\nprint('to stdout', flush=True)\nprint('to stderr', file=sys.stderr)\n"
                  "sys.exit(3)\n")

    rc, url = _run(tmp_path, "boom.py", {}, run_id=None)

    assert rc == 3
    [row] = _rows(url)
    assert (row.status, row.run_id) == ("failed", None)
    assert "to stdout" in row.output
    assert "to stderr" in row.output
    assert row.duracao_segundos == (row.fim - row.inicio).total_seconds()


def test_a_row_gone_while_the_script_ran_is_logged_and_the_code_returned(tmp_path, caplog):
    """If the task's row disappears while its script runs, the handler logs an error naming the row
    and still returns the script's exit code, writing no row back."""
    _plant_script(tmp_path, "vanish.py",
                  "import sqlite3, sys\ncon = sqlite3.connect(sys.argv[2])\n"
                  "con.execute('delete from execucoes')\ncon.commit()\ncon.close()\nsys.exit(4)\n")

    with caplog.at_level(logging.ERROR, logger="dataforge.tasks.python_handler"):
        rc, url = _run(tmp_path, "vanish.py", {"db": str(tmp_path / "dataforge.db")})

    assert rc == 4
    assert _rows(url) == []
    [record] = [r for r in caplog.records if r.name == "dataforge.tasks.python_handler"]
    assert record.levelno == logging.ERROR
    assert "row 1 not found" in record.getMessage()   # the first row of a fresh store has id 1


def test_a_python_task_without_a_script_is_refused_before_anything_runs(tmp_path):
    """A python task with no script raises ValueError naming the task, and creates no task store."""
    task = Task(name="orphan", type="python", retry=_NO_RETRY)
    job = Job(name="nightly", tasks=[task], retry=_NO_RETRY)
    with pytest.raises(ValueError, match="'orphan'"):
        PythonHandler().run(task, job, tmp_path, make_db_url(tmp_path / "dataforge.db"), run_id="r1")
    assert not (tmp_path / "dataforge.db").exists()


def test_the_script_runs_with_the_interpreter_running_dataforge(tmp_path, monkeypatch):
    """The script is started as [sys.executable, <workspace>/scripts/<script>, params...], without a
    shell, capturing stdout and stderr together as text."""
    calls = []

    class _Done:
        returncode = 0

        def __init__(self, args, **kwargs):
            calls.append((args, kwargs))

        def communicate(self):
            return "done\n", None

    monkeypatch.setattr("dataforge.tasks.python_handler.subprocess.Popen", _Done)
    rc, url = _run(tmp_path, "a/b.py", {"x": 1})
    assert rc == 0
    [(args, kwargs)] = calls
    assert args == [sys.executable, str(tmp_path / "scripts" / "a" / "b.py"), "--x", "1"]
    assert kwargs["stdout"] == subprocess.PIPE
    assert kwargs["stderr"] == subprocess.STDOUT
    assert kwargs["text"] is True
    assert kwargs.get("shell", False) is False
    assert _rows(url)[0].output == "done\n"
