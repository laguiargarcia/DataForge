"""Rules of dataforge/tasks/message/handler.py, the `message` task (docs/subsystems/tasks-message.md):
it reads the other tasks of its own job and run, renders {status} as failed iff one of them failed
(B-F2), takes {trigger} and {started_at} from the run's row, sends the rendered subject and body to
each channel's recipient with the env file's variables only, and records its own row: `running`
while it sends, then `success` or `failed` with the error.

Channels are fakes registered for the example: nothing leaves the test. Each example builds its
stores in a temporary directory created inside the example, with DATAFORGE_HOME pointing there."""
import os
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from hypothesis import example, given, settings, strategies as st
from sqlalchemy.orm import Session

from dataforge.compat import db_url as make_db_url
from dataforge.db import ExecucaoORM, JobRunORM, init_db, utcnow
from dataforge.models import Job, RetryConfig, Task
from dataforge.tasks.message.channels import _REGISTRY
from dataforge.tasks.message.handler import MessageHandler

_NO_RETRY = RetryConfig(attempts=1, delay_seconds=0)
_SUBJECT = "{workspace}|{job}|{trigger}|{run_id}|{status}"
_BODY = "{failed_tasks}/{succeeded_tasks}/{skipped_tasks}\n{failed_tasks_summary}"
_RUN = "r1"
# The rendered timestamps carry an offset but name the wrong instant outside UTC (df-ahua.23.12), so
# the tests below that are not about that bug only check the instant to within a day.
_A_DAY = timedelta(hours=24)


def _instant(rendered: str) -> datetime:
    """A rendered timestamp as naive UTC, the form the stores keep."""
    return datetime.fromisoformat(rendered).astimezone(timezone.utc).replace(tzinfo=None)


class _Outbox:
    """A channel that records what it sends, and the message task's own row at that moment."""

    def __init__(self, name: str, url: str, fails: bool = False):
        self.name, self.url, self.fails = name, url, fails
        self.sent: list[tuple] = []
        self.own_rows: list[tuple] = []

    def send(self, recipient, subject, body, env):
        engine = init_db(self.url)
        with Session(engine) as s:
            self.own_rows = [(r.status, r.inicio, r.fim, r.run_id)
                             for r in s.query(ExecucaoORM).filter_by(task="notify")]
        engine.dispose()
        self.sent.append((recipient, subject, body, env))
        if self.fails:
            raise RuntimeError(f"{self.name} down")


@contextmanager
def _stores(rows=(), run=None):
    """A workspace 'ws' with its task store holding `rows` (job, task, status, run_id, output) and a
    global store holding the JobRunORM `run` (trigger, started_at) for run r1, if given."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        home = Path(d)
        ws = home / "workspaces" / "ws"
        ws.mkdir(parents=True)
        url = make_db_url(ws / "dataforge.db")
        engine = init_db(url)
        with Session(engine) as s:
            for job, task, status, run_id, output in rows:
                s.add(ExecucaoORM(job=job, task=task, status=status, run_id=run_id, output=output))
            s.commit()
        engine.dispose()
        with patch.dict(os.environ, {"DATAFORGE_HOME": str(home)}):
            if run is not None:
                trigger, started = run
                global_engine = init_db(make_db_url(home / "dataforge.db"))
                with Session(global_engine) as s:
                    s.add(JobRunORM(id=_RUN, workspace="ws", job="nightly", trigger=trigger,
                                    started_at=started))
                    s.commit()
                global_engine.dispose()
            yield ws, url


def _send(stores: tuple[Path, str], run_id=_RUN, params=None, env_file=None, fails=False):
    """Run a message task 'notify' of job 'nightly' of the workspace and task store `stores` through
    one fake channel; return (rc, outbox)."""
    ws, url = stores
    outbox = _Outbox("outbox", url, fails)
    params = {"channels": ["outbox"], "recipients": {"outbox": "ops"},
              "subject": _SUBJECT, "body": _BODY} if params is None else params
    task = Task(name="notify", type="message", params=params, retry=_NO_RETRY)
    job = Job(name="nightly", tasks=[task], env_file=env_file, retry=_NO_RETRY)
    with patch.dict(_REGISTRY, {"outbox": outbox}):
        rc = MessageHandler().run(task, job, ws, url, run_id=run_id)
    return rc, outbox


def _own_row(url: str) -> ExecucaoORM:
    engine = init_db(url)
    with Session(engine, expire_on_commit=False) as s:
        row = s.query(ExecucaoORM).filter_by(task="notify").order_by(ExecucaoORM.id.desc()).first()
        s.expunge_all()
    engine.dispose()
    return row


_SIBLING = st.tuples(st.sampled_from(["extract", "load", "report"]),
                     st.sampled_from(["success", "failed", "skipped", "running"]),
                     st.sampled_from([None, "", "last words", "one\ntwo"]))
# Rows that are not this run's siblings: another job in the same run, the same job in another run,
# and an earlier attempt of the message task itself.
_NOISE = st.sampled_from([("other", "extract", "failed", _RUN), ("nightly", "extract", "failed", "r0"),
                          ("nightly", "notify", "failed", _RUN), ("other", "load", "success", "r0")])


@settings(max_examples=25)
@given(siblings=st.lists(_SIBLING, max_size=5), noise=st.lists(_NOISE, max_size=3))
@example(siblings=[], noise=[("other", "extract", "failed", _RUN),
                             ("nightly", "extract", "failed", "r0"), ("nightly", "notify", "failed", _RUN)])
@example(siblings=[("extract", "failed", "one\ntwo"), ("load", "success", None),
                   ("report", "skipped", None), ("load", "failed", None)], noise=[])
def test_status_and_task_lists_come_from_this_run_s_other_tasks(siblings, noise):
    """{status} is failed iff another task of the same job and run failed; {failed,succeeded,
    skipped}_tasks list those tasks' names in row order and the summary gives each failed one rc 1
    and its output's last lines. Rows of other jobs, other runs and the message task itself count
    for nothing, and {workspace}, {job} and {run_id} are the task's own."""
    rows = [(job, task, status, run_id, None) for job, task, status, run_id in noise]
    rows += [("nightly", name, status, _RUN, output) for name, status, output in siblings]
    with _stores(rows, run=("cron-daily", datetime(2026, 1, 2, 3, 4, 5))) as (ws, url):
        rc, outbox = _send((ws, url))
    assert rc == 0
    status = "failed" if any(s == "failed" for _, s, _ in siblings) else "success"

    def names(wanted):
        return ", ".join(n for n, s, _ in siblings if s == wanted)

    blocks = []
    for name, s, output in siblings:
        if s == "failed":
            lines = (output or "").splitlines()
            blocks.append(f"- {name} (rc=1)" + (":\n  " + "\n".join(lines) if lines else ""))
    body = f"{names('failed')}/{names('success')}/{names('skipped')}\n" + "\n".join(blocks)
    assert outbox.sent == [("ops", f"ws|nightly|cron-daily|{_RUN}|{status}", body, {})]


@settings(max_examples=15)
@given(trigger=st.none() | st.sampled_from(["cron-daily", "manual", "hourly"]),
       started=st.datetimes(min_value=datetime(2020, 1, 1), max_value=datetime(2024, 12, 31)))
def test_trigger_and_start_come_from_the_run_s_row(trigger, started):
    """With a JobRunORM row for its run, {trigger} is that row's trigger ('manual' when it has none)
    and {started_at} is that row's start, not the message task's (years apart here)."""
    params = {"channels": ["outbox"], "recipients": {"outbox": "ops"},
              "subject": "{trigger}", "body": "{started_at}"}
    with _stores(run=(trigger, started)) as (ws, url):
        rc, outbox = _send((ws, url), params=params)
    assert rc == 0
    [(_, subject, body, _)] = outbox.sent
    assert subject == (trigger or "manual")
    assert abs(_instant(body) - started) < _A_DAY


@pytest.mark.parametrize("run_id", [_RUN, None], ids=["no-run-row", "no-run-id"])
def test_a_run_without_a_row_or_an_id_reads_manual_and_starts_with_the_task(run_id):
    """Without a run row for its run id, or without a run id, {trigger} is 'manual' and {started_at}
    is when the message task started; without a run id, {run_id} is empty."""
    params = {"channels": ["outbox"], "recipients": {"outbox": "ops"},
              "subject": "{trigger}|{run_id}", "body": "{started_at}"}
    with _stores() as (ws, url):
        now = utcnow()
        rc, outbox = _send((ws, url), run_id=run_id, params=params)
    assert rc == 0
    [(_, subject, body, _)] = outbox.sent
    assert subject == f"manual|{run_id or ''}"
    assert abs(_instant(body) - now) < _A_DAY


@pytest.fixture
def three_hours_behind_utc():
    """The process's local zone set to UTC-3 (no DST) for the test, then restored."""
    saved = os.environ.get("TZ")
    os.environ["TZ"] = "Etc/GMT+3"
    time.tzset()
    yield
    if saved is None:
        del os.environ["TZ"]
    else:
        os.environ["TZ"] = saved
    time.tzset()


@pytest.mark.skipif(sys.platform == "win32", reason="time.tzset, which sets the local zone, is POSIX-only")
@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="df-ahua.23.12: render reads the naive UTC times the handler passes as local")
def test_started_at_names_the_run_s_start_instant_outside_utc(three_hours_behind_utc):
    """{started_at} names the instant the run started (stored as naive UTC) in any local zone."""
    started = datetime(2026, 1, 2, 9, 0, 0)
    params = {"channels": ["outbox"], "recipients": {"outbox": "ops"}, "body": "{started_at}"}
    with _stores(run=("cron-daily", started)) as (ws, url):
        rc, outbox = _send((ws, url), params=params)
    assert rc == 0
    [(_, _, body, _)] = outbox.sent
    assert _instant(body) == started


def test_the_task_s_row_reads_running_while_it_sends_then_success():
    """While the channels send, the message task's row is `running` with its start and run id and
    no end; afterwards it is `success`, ended, its duration the time between start and end, and
    its output names every channel it sent through."""
    with _stores() as (ws, url):
        rc, outbox = _send((ws, url))
        row = _own_row(url)
    assert rc == 0
    [(status, inicio, fim, run_id)] = outbox.own_rows
    assert (status, fim, run_id) == ("running", None, _RUN)
    assert inicio is not None
    assert (row.status, row.inicio, row.run_id) == ("success", inicio, _RUN)
    assert row.fim >= row.inicio
    assert row.duracao_segundos == (row.fim - row.inicio).total_seconds()
    assert "outbox" in row.output


def test_a_failed_send_leaves_a_failed_row_with_the_error():
    """When a channel raises, the task returns 1 and its row is `failed`, ended, its duration the
    time between start and end, and its output '<ErrorType>: <message>'."""
    with _stores() as (ws, url):
        rc, outbox = _send((ws, url), fails=True)
        row = _own_row(url)
    assert rc == 1
    assert len(outbox.sent) == 1
    assert row.status == "failed"
    assert row.fim >= row.inicio
    assert row.duracao_segundos == (row.fim - row.inicio).total_seconds()
    assert row.output.startswith("RuntimeError: ")
    assert "outbox down" in row.output


def test_each_channel_gets_its_own_recipient_and_only_the_env_file_s_variables():
    """Every listed channel gets the rendered message at its own recipient, with the variables of
    the job's env file and none from the process environment; the success output names them all."""
    with _stores() as (ws, url):
        (ws / ".env").write_bytes(b"# secrets\nSMTP_HOST=smtp.example.com\nTOKEN='t0k'\n")
        second = _Outbox("pager", url)
        params = {"channels": ["outbox", "pager"], "recipients": {"outbox": "ops", "pager": "+55"},
                  "subject": "S {job}", "body": "B {status}"}
        with patch.dict(_REGISTRY, {"pager": second}), \
                patch.dict(os.environ, {"FROM_THE_OS": "leak"}):
            rc, outbox = _send((ws, url), params=params, env_file=".env")
        row = _own_row(url)
    assert rc == 0
    env = {"SMTP_HOST": "smtp.example.com", "TOKEN": "t0k"}
    assert outbox.sent == [("ops", "S nightly", "B success", env)]
    assert second.sent == [("+55", "S nightly", "B success", env)]
    assert "outbox" in row.output
    assert "pager" in row.output


def test_subject_and_body_default_to_empty():
    """A message task without subject or body sends an empty subject and an empty body."""
    with _stores() as (ws, url):
        rc, outbox = _send((ws, url), params={"channels": ["outbox"], "recipients": {"outbox": "ops"}})
    assert rc == 0
    assert outbox.sent == [("ops", "", "", {})]


def test_a_task_without_channels_fails_saying_so():
    """A message task with no channels sends nothing, returns 1 and records a failed row whose
    output is a ValueError naming params.channels."""
    with _stores() as (ws, url):
        rc, outbox = _send((ws, url), params={"recipients": {}, "subject": "s", "body": "b"})
        row = _own_row(url)
    assert rc == 1
    assert outbox.sent == []
    assert row.status == "failed"
    assert row.output.startswith("ValueError: ")
    assert "params.channels" in row.output
