"""Rules of the two Twilio channels (dataforge/tasks/message/channels/twilio_sms.py and
twilio_whatsapp.py): each refuses to send without every variable it requires, naming the missing
one; it signs in with the account SID and token; it sends from its own sender to the recipient
(WhatsApp addresses prefixed 'whatsapp:'); and the text is the subject, a blank line and the body,
or the body alone when there is no subject. Twilio's client is replaced: nothing leaves the test."""
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import example, given, settings, strategies as st

from dataforge.tasks.message.channels.twilio_sms import SmsChannel
from dataforge.tasks.message.channels.twilio_whatsapp import WhatsAppChannel

_ENV = {"TWILIO_SID": "AC1", "TWILIO_TOKEN": "tok",
        "TWILIO_WHATSAPP_FROM": "whatsapp:+5511900000001", "TWILIO_SMS_FROM": "+5511900000002"}
# channel class, module whose Client is patched, sender variable, how the recipient is addressed
_CHANNELS = [
    (SmsChannel, "dataforge.tasks.message.channels.twilio_sms.Client", "TWILIO_SMS_FROM", "{}"),
    (WhatsAppChannel, "dataforge.tasks.message.channels.twilio_whatsapp.Client",
     "TWILIO_WHATSAPP_FROM", "whatsapp:{}"),
]


@patch("dataforge.tasks.message.channels.twilio_whatsapp.Client")
def test_whatsapp_send(client_cls):
    client = MagicMock()
    client_cls.return_value = client
    WhatsAppChannel().send(
        recipient="+5511999999999",
        subject="hi", body="hello",
        env={"TWILIO_SID": "AC1", "TWILIO_TOKEN": "tok",
             "TWILIO_WHATSAPP_FROM": "whatsapp:+5511900000001"},
    )
    client_cls.assert_called_once_with("AC1", "tok")
    client.messages.create.assert_called_once_with(
        from_="whatsapp:+5511900000001",
        to="whatsapp:+5511999999999",
        body="hi\n\nhello",
    )


@patch("dataforge.tasks.message.channels.twilio_sms.Client")
def test_sms_send(client_cls):
    client = MagicMock()
    client_cls.return_value = client
    SmsChannel().send(
        recipient="+5511999999999",
        subject="hi", body="hello",
        env={"TWILIO_SID": "AC1", "TWILIO_TOKEN": "tok",
             "TWILIO_SMS_FROM": "+5511900000002"},
    )
    client.messages.create.assert_called_once_with(
        from_="+5511900000002",
        to="+5511999999999",
        body="hi\n\nhello",
    )


def test_whatsapp_missing_env_raises():
    with pytest.raises(ValueError, match="TWILIO_SID"):
        WhatsAppChannel().send("+551", "s", "b", env={})


@pytest.mark.parametrize(("cls", "client_path", "_sender", "_to"), _CHANNELS)
@pytest.mark.parametrize("missing", ["TWILIO_SID", "TWILIO_TOKEN", "SENDER"])
def test_a_missing_variable_is_named_and_nothing_is_sent(cls, client_path, _sender, _to, missing):
    """Without one of its required variables a Twilio channel raises ValueError naming that
    variable and its channel, before it builds a client."""
    missing = _sender if missing == "SENDER" else missing
    env = {k: v for k, v in _ENV.items() if k != missing}
    channel = cls()
    with patch(client_path) as client_cls, pytest.raises(ValueError) as info:
        channel.send("+5511999999999", "s", "b", env=env)
    assert missing in str(info.value)
    assert cls.name in str(info.value)
    client_cls.assert_not_called()


@settings(max_examples=40)
@given(subject=st.text(max_size=10), body=st.text(max_size=10),
       recipient=st.from_regex(r"\+[0-9]{8,13}", fullmatch=True))
@example(subject="", body="only the body", recipient="+5511999999999")
@pytest.mark.parametrize("spec", _CHANNELS, ids=["sms", "whatsapp"])
def test_a_twilio_channel_sends_subject_then_body_from_its_sender(spec, subject, body, recipient):
    """A Twilio channel signs in with (SID, token) and sends one message from its sender to the
    recipient; its text is 'subject\\n\\nbody', or the body alone when the subject is empty."""
    cls, client_path, sender, to = spec
    text = body if subject == "" else subject + "\n\n" + body
    with patch(client_path) as client_cls:
        cls().send(recipient, subject, body, env=dict(_ENV))
    client_cls.assert_called_once_with("AC1", "tok")
    client_cls.return_value.messages.create.assert_called_once_with(
        from_=_ENV[sender], to=to.format(recipient), body=text)
