from unittest.mock import MagicMock, patch

from sqlalchemy.orm import Session

from dataforge.db import JobRunORM
from dataforge.engine import run_job
from dataforge.models import Job, RetryConfig, Task


def _write(p, name, content):
    (p / name).write_text(content)


@patch("dataforge.tasks.message.channels.email.smtplib.SMTP_SSL")
def test_failure_alert_e2e(smtp_cls, tmp_path, db_url, dataforge_home, global_engine):
    smtp_cls.return_value.__enter__.return_value = MagicMock()

    _write(tmp_path, "fail.py", "import sys; sys.exit(1)\n")
    (tmp_path / ".env").write_text(
        "SMTP_HOST=h\nSMTP_PORT=465\nSMTP_USER=u\nSMTP_PASS=p\nSMTP_FROM=u@x\n"
    )

    t1 = Task(name="landing", script="fail.py",
              retry=RetryConfig(attempts=1, delay_seconds=0))
    notify = Task(
        name="notify_on_failure", type="message", match="any",
        depends_on=[{"task": "landing", "when": "failed"}],
        params={
            "channels": ["email"],
            "recipients": {"email": "ops@example.com"},
            "subject": "FAILED {run_id}",
            "body": "failed tasks: {failed_tasks}",
        },
        retry=RetryConfig(attempts=1, delay_seconds=0),
    )
    job = Job(name="getfinance_daily", tasks=[t1, notify], env_file=".env",
              retry=RetryConfig(attempts=1, delay_seconds=0))

    run_id = "e2e-1"
    with Session(global_engine) as s:
        s.add(JobRunORM(id=run_id, workspace="ws", job=job.name,
                        status="pending", trigger="daily_06"))
        s.commit()

    results = run_job(job, tmp_path, db_url, run_id=run_id)
    assert results["landing"] != 0
    assert results["notify_on_failure"] == 0

    # landing is non-leaf (notify depends on it). notify (leaf) succeeded.
    # Leaf-only outcome rule → run finalized as success.
    with Session(global_engine) as s:
        assert s.get(JobRunORM, run_id).status == "success"

    sendmail_call = smtp_cls.return_value.__enter__.return_value.sendmail.call_args
    body_payload = sendmail_call[0][2]
    assert "FAILED e2e-1" in body_payload
    assert "landing" in body_payload
