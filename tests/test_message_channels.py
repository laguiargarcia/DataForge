import pytest
from dataforge.tasks.message.channels import get_channel


class _Dummy:
    name = "dummy"
    required_env = ()

    def send(self, recipient, subject, body, env):
        return None


def test_lookup_known_channel(monkeypatch):
    from dataforge.tasks.message.channels import _REGISTRY
    monkeypatch.setitem(_REGISTRY, "dummy", _Dummy())
    assert get_channel("dummy").name == "dummy"


def test_unknown_channel_raises():
    with pytest.raises(KeyError, match="unknown channel"):
        get_channel("smoke_signal")
