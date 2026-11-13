"""Rules of dataforge/tasks/message/channels/__init__.py: the channel registry maps each built-in
channel's own name to an instance of it, and an unknown name fails naming the registered ones."""
import pytest

from dataforge.tasks.message.channels import _REGISTRY, _register, get_channel
from dataforge.tasks.message.channels.email import EmailChannel
from dataforge.tasks.message.channels.twilio_sms import SmsChannel
from dataforge.tasks.message.channels.twilio_whatsapp import WhatsAppChannel


class _Dummy:
    name = "dummy"
    required_env = ()

    def send(self, recipient, subject, body, env):
        return None


def test_lookup_known_channel(monkeypatch):
    monkeypatch.setitem(_REGISTRY, "dummy", _Dummy())
    assert get_channel("dummy").name == "dummy"


def test_a_registered_channel_is_found_by_its_name(monkeypatch):
    """_register files a channel under its own name, and get_channel returns that same object."""
    monkeypatch.setattr("dataforge.tasks.message.channels._REGISTRY", dict(_REGISTRY))
    dummy = _Dummy()
    _register(dummy)
    assert get_channel("dummy") is dummy


def test_unknown_channel_raises():
    with pytest.raises(KeyError, match="unknown channel"):
        get_channel("smoke_signal")


@pytest.mark.parametrize("cls", [EmailChannel, SmsChannel, WhatsAppChannel])
def test_each_built_in_channel_is_registered_under_its_own_name(cls):
    """Importing the package registers every built-in channel: its name returns an instance of it."""
    channel = get_channel(cls.name)
    assert isinstance(channel, cls)
    assert channel.name == cls.name


def test_an_unknown_channel_error_lists_the_registered_names():
    """The KeyError for an unknown channel lists the registered channel names, sorted."""
    with pytest.raises(KeyError) as info:
        get_channel("pager")
    assert str(sorted(_REGISTRY)) in str(info.value)
