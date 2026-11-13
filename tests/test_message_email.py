"""Rules of dataforge/tasks/message/channels/email.py: the email channel refuses to send without
every SMTP variable, naming the missing one; port 465 means implicit SSL and any other port a plain
connection upgraded with STARTTLS (B-F3); it logs in with SMTP_USER/SMTP_PASS and sends one message
from SMTP_FROM to the recipient whose headers and body are the ones given. smtplib is replaced:
nothing leaves the test."""
import email
import email.policy
from unittest.mock import MagicMock, call, patch

import pytest
from hypothesis import example, given, settings, strategies as st

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


_SSL = "dataforge.tasks.message.channels.email.smtplib.SMTP_SSL"
_PLAIN = "dataforge.tasks.message.channels.email.smtplib.SMTP"
_WORDS = st.from_regex(r"[A-Za-z0-9]([A-Za-z0-9 ]{0,18}[A-Za-z0-9])?", fullmatch=True)


@pytest.mark.parametrize("missing", EmailChannel.required_env)
def test_a_missing_smtp_variable_is_named_and_nothing_connects(missing):
    """Without one of the five SMTP variables the channel raises ValueError naming it (and the
    channel) before it opens any connection."""
    env = {k: v for k, v in _base_env("587").items() if k != missing}
    with patch(_SSL) as ssl_cls, patch(_PLAIN) as plain_cls, pytest.raises(ValueError) as info:
        EmailChannel().send("alice@example.com", "s", "b", env=env)
    assert missing in str(info.value)
    assert "email" in str(info.value)
    ssl_cls.assert_not_called()
    plain_cls.assert_not_called()


@settings(max_examples=40)
@given(port=st.sampled_from([465, 25, 587, 2525]), subject=_WORDS, body=_WORDS,
       user=st.sampled_from(["bot", "ops@example.com"]))
@example(port=465, subject="hi", body="hello", user="bot")
@example(port=587, subject="hi", body="hello", user="bot")
def test_one_message_goes_from_the_sender_to_the_recipient_over_the_port_s_transport(
        port, subject, body, user):
    """Port 465 connects with SMTP_SSL and no STARTTLS; any other port with SMTP plus STARTTLS before
    the login. Either way it logs in with SMTP_USER/SMTP_PASS and sends one message, envelope from
    SMTP_FROM to [recipient], whose From, To and Subject headers and body are those values."""
    env = {**_base_env(str(port)), "SMTP_USER": user, "SMTP_FROM": "sender@example.com"}
    with patch(_SSL) as ssl_cls, patch(_PLAIN) as plain_cls:
        EmailChannel().send("alice@example.com", subject, body, env=env)
    used, unused = (ssl_cls, plain_cls) if port == 465 else (plain_cls, ssl_cls)
    used.assert_called_once_with("smtp.example.com", port)
    unused.assert_not_called()
    smtp = used.return_value.__enter__.return_value
    upgrade = [] if port == 465 else [call.starttls()]
    assert smtp.mock_calls[:-1] == [*upgrade, call.login(user, "s3cret")]
    assert smtp.mock_calls[-1][0] == "sendmail"
    sender, rcpts, raw = smtp.sendmail.call_args.args
    assert (sender, rcpts) == ("sender@example.com", ["alice@example.com"])
    msg = email.message_from_string(raw, policy=email.policy.default)
    assert (msg["From"], msg["To"], msg["Subject"]) == ("sender@example.com", "alice@example.com",
                                                        subject)
    assert msg.get_content().rstrip("\n") == body
