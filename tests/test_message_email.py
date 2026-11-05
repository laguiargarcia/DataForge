from unittest.mock import MagicMock, patch

import pytest

from dataforge.tasks.message.channels.email import EmailChannel


def _base_env(port: str) -> dict[str, str]:
    return {
        "SMTP_HOST": "smtp.example.com", "SMTP_PORT": port,
        "SMTP_USER": "bot@example.com", "SMTP_PASS": "s3cret",
        "SMTP_FROM": "bot@example.com",
    }


@patch("dataforge.tasks.message.channels.email.smtplib.SMTP_SSL")
def test_email_port_465_uses_smtp_ssl(smtp_cls):
    smtp_instance = MagicMock()
    smtp_cls.return_value.__enter__.return_value = smtp_instance
    EmailChannel().send(
        recipient="alice@example.com", subject="hi", body="hello",
        env=_base_env("465"),
    )
    smtp_cls.assert_called_once_with("smtp.example.com", 465)
    smtp_instance.login.assert_called_once_with("bot@example.com", "s3cret")
    args, _ = smtp_instance.sendmail.call_args
    assert args[0] == "bot@example.com"
    assert args[1] == ["alice@example.com"]
    assert "Subject: hi" in args[2]
    assert "hello" in args[2]


@patch("dataforge.tasks.message.channels.email.smtplib.SMTP")
def test_email_port_587_uses_starttls(smtp_cls):
    smtp_instance = MagicMock()
    smtp_cls.return_value.__enter__.return_value = smtp_instance
    EmailChannel().send(
        recipient="alice@example.com", subject="hi", body="hello",
        env=_base_env("587"),
    )
    smtp_cls.assert_called_once_with("smtp.example.com", 587)
    smtp_instance.starttls.assert_called_once()
    smtp_instance.login.assert_called_once_with("bot@example.com", "s3cret")
    smtp_instance.sendmail.assert_called_once()


def test_email_missing_env_raises():
    env = _base_env("465")
    env.pop("SMTP_HOST")
    with pytest.raises(ValueError, match="SMTP_HOST"):
        EmailChannel().send("a@b", "s", "b", env=env)


def test_required_env_advertised():
    assert "SMTP_HOST" in EmailChannel.required_env
    assert "SMTP_USER" in EmailChannel.required_env
    assert "SMTP_PASS" in EmailChannel.required_env
