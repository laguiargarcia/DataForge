"""Channel registry for the message task. Built-ins register lazily on import."""
from __future__ import annotations
from typing import Protocol, ClassVar


class Channel(Protocol):
    name: ClassVar[str]
    required_env: ClassVar[tuple[str, ...]]

    def send(self, recipient: str, subject: str, body: str, env: dict[str, str]) -> None:
        """Send one message. Raise on any failure."""


_REGISTRY: dict[str, Channel] = {}


def _register(channel: Channel) -> None:
    _REGISTRY[channel.name] = channel


def get_channel(name: str) -> Channel:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown channel '{name}'; registered: {sorted(_REGISTRY)}")


from .email import EmailChannel
from .twilio_sms import SmsChannel
from .twilio_whatsapp import WhatsAppChannel

for _cls in (EmailChannel, WhatsAppChannel, SmsChannel):
    _register(_cls())
