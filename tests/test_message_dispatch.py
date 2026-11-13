"""Rules of dataforge/tasks/message/dispatch.py: every channel gets the message at the same time,
and the task fails when any of them failed (strict-all, D15), naming each failure."""
import threading
from unittest.mock import MagicMock

import pytest
from hypothesis import example, given, settings, strategies as st

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


class _Recorder:
    """A channel that records each send and fails when told to."""

    def __init__(self, name: str, fails: bool):
        self.name, self.fails, self.calls = name, fails, []

    def send(self, recipient, subject, body, env):
        self.calls.append((recipient, subject, body, env))
        if self.fails:
            raise RuntimeError(f"{self.name} down")


@settings(max_examples=50)
@given(st.lists(st.booleans(), min_size=1, max_size=6))
@example([False])
@example([True, False, True])
def test_every_target_is_sent_once_and_the_failures_are_named(fails):
    """deliver_all sends to every (channel, recipient) once with the same subject, body and env;
    it raises iff some send failed, and the error lists exactly the failed ones as
    '<channel>: <error>', separated by '; '."""
    channels = [_Recorder(f"ch{i}", f) for i, f in enumerate(fails)]
    targets = [(c, f"to-{c.name}") for c in channels]
    env = {"K": "v"}
    failed = {f"{c.name}: {c.name} down" for c in channels if c.fails}
    if failed:
        with pytest.raises(RuntimeError) as info:
            deliver_all(targets, subject="s", body="b", env=env)
        prefix = "channel(s) failed (strict-all): "
        message = str(info.value)
        assert message.startswith(prefix)
        assert sorted(message.removeprefix(prefix).split("; ")) == sorted(failed)
    else:
        deliver_all(targets, subject="s", body="b", env=env)
    for c in channels:
        assert c.calls == [(f"to-{c.name}", "s", "b", env)]


def test_no_target_is_refused_naming_the_missing_channels():
    """An empty target list raises ValueError saying no channel is configured."""
    with pytest.raises(ValueError, match="no channels configured"):
        deliver_all([], subject="s", body="b", env={})


def test_all_sends_run_at_once_whatever_their_number():
    """Every send runs concurrently: 33 sends that each wait for all the others all get through,
    which a pool smaller than the target count (the executor's default caps at 32) cannot do."""
    count = 33
    barrier = threading.Barrier(count, timeout=5)

    class _Waiter:
        name = "waiter"

        def send(self, recipient, subject, body, env):
            barrier.wait()

    deliver_all([(_Waiter(), str(i)) for i in range(count)], subject="s", body="b", env={})
