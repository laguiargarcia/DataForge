from unittest.mock import MagicMock

import pytest

from dataforge.tasks.message.dispatch import deliver_all


def test_all_channels_called_in_parallel():
    ch_a, ch_b = MagicMock(), MagicMock()
    ch_a.name, ch_b.name = "a", "b"
    deliver_all([(ch_a, "alice"), (ch_b, "bob")], subject="s", body="b", env={})
    ch_a.send.assert_called_once_with("alice", "s", "b", {})
    ch_b.send.assert_called_once_with("bob", "s", "b", {})


def test_strict_all_one_failure_raises_aggregated():
    ch_a, ch_b = MagicMock(), MagicMock()
    ch_a.name, ch_b.name = "a", "b"
    ch_b.send.side_effect = RuntimeError("twilio down")
    with pytest.raises(RuntimeError, match=r"channel\(s\) failed.*b.*twilio down"):
        deliver_all([(ch_a, "alice"), (ch_b, "bob")], subject="s", body="b", env={})
    ch_a.send.assert_called_once()
