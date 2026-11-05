from unittest.mock import MagicMock, patch

import pytest

from dataforge.tasks.message.channels.twilio_sms import SmsChannel
from dataforge.tasks.message.channels.twilio_whatsapp import WhatsAppChannel


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
