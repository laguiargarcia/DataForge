from pathlib import Path
from unittest.mock import MagicMock, patch

from sqlalchemy.orm import Session

from dataforge.compat import db_url as make_db_url
from dataforge.db import ExecucaoORM, init_db
from dataforge.models import Job, RetryConfig, Task
from dataforge.tasks.message.handler import MessageHandler


def _write_env(ws: Path) -> str:
    (ws / ".env").write_text(
        "SMTP_HOST=smtp.example.com\nSMTP_PORT=465\n"
        "SMTP_USER=u\nSMTP_PASS=p\nSMTP_FROM=u@example.com\n"
    )
    return ".env"


@patch("dataforge.tasks.message.channels.email.smtplib.SMTP_SSL")
def test_message_handler_sends_and_writes_success_row(smtp_cls, tmp_path, dataforge_home):
    smtp_cls.return_value.__enter__.return_value = MagicMock()
    env_file = _write_env(tmp_path)

    url = make_db_url(tmp_path / "dataforge.db")
    init_db(url)
    init_db(make_db_url(dataforge_home / "dataforge.db"))

    task = Task(
        name="notify", type="message",
        params={
            "channels": ["email"],
            "recipients": {"email": "ops@example.com"},
            "subject": "Run {run_id}",
            "body": "Status: {status}",
        },
        retry=RetryConfig(attempts=1, delay_seconds=0),
    )
    job = Job(name="j", tasks=[task], env_file=env_file,
              retry=RetryConfig(attempts=1, delay_seconds=0))

    rc = MessageHandler().run(task, job, tmp_path, url, run_id="r1")
    assert rc == 0

    eng = init_db(url)
    with Session(eng) as s:
        row = s.query(ExecucaoORM).filter_by(task="notify").one()
        assert row.status == "success"


@patch("dataforge.tasks.message.channels.email.smtplib.SMTP_SSL")
def test_message_handler_strict_all_fails_on_channel_error(smtp_cls, tmp_path, dataforge_home):
    smtp_cls.side_effect = RuntimeError("SMTP refused")
    env_file = _write_env(tmp_path)

    url = make_db_url(tmp_path / "dataforge.db")
    init_db(url)
    init_db(make_db_url(dataforge_home / "dataforge.db"))

    task = Task(
        name="notify", type="message",
        params={
            "channels": ["email"],
            "recipients": {"email": "ops@example.com"},
            "subject": "x", "body": "y",
        },
        retry=RetryConfig(attempts=1, delay_seconds=0),
    )
    job = Job(name="j", tasks=[task], env_file=env_file,
              retry=RetryConfig(attempts=1, delay_seconds=0))
    rc = MessageHandler().run(task, job, tmp_path, url, run_id="r1")
    assert rc == 1

    eng = init_db(url)
    with Session(eng) as s:
        row = s.query(ExecucaoORM).filter_by(task="notify").one()
        assert row.status == "failed"
        assert "SMTP refused" in (row.output or "")


def test_message_handler_missing_recipient_raises_clear_error(tmp_path, dataforge_home):
    env_file = _write_env(tmp_path)
    url = make_db_url(tmp_path / "dataforge.db")
    init_db(url)
    init_db(make_db_url(dataforge_home / "dataforge.db"))
    task = Task(
        name="notify", type="message",
        params={"channels": ["email"], "recipients": {}, "subject": "x", "body": "y"},
        retry=RetryConfig(attempts=1, delay_seconds=0),
    )
    job = Job(name="j", tasks=[task], env_file=env_file,
              retry=RetryConfig(attempts=1, delay_seconds=0))
    rc = MessageHandler().run(task, job, tmp_path, url, run_id="r1")
    assert rc == 1
    eng = init_db(url)
    with Session(eng) as s:
        row = s.query(ExecucaoORM).filter_by(task="notify").one()
        assert "recipient" in (row.output or "").lower()
