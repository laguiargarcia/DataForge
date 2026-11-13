"""df-3yh: the cyclic garbage collector must run on the GUI thread only.

A collection started by another thread (the API daemon) frees leftover Qt objects there, and
PySide6 crashes destroying them off the GUI thread.
"""
import gc
import threading

import pytest

pytest.importorskip("PySide6")


class _Cycle:
    def __init__(self):
        self.me = self


def _threads_that_collect(work):
    """Names of the threads that start a collection while `work` runs on a worker thread."""
    seen = []

    def on_gc(phase, _info):
        if phase == "start":
            seen.append(threading.current_thread().name)

    gc.callbacks.append(on_gc)
    try:
        worker = threading.Thread(target=work, name="worker")
        worker.start()
        worker.join()
    finally:
        gc.callbacks.remove(on_gc)
    return seen


def _make_cyclic_garbage():
    for _ in range(50 * gc.get_threshold()[0]):
        _Cycle()


@pytest.fixture
def gc_enabled_like_a_fresh_process():
    """conftest keeps the collector off for the session; start from CPython's default here."""
    gc.enable()
    yield
    gc.disable()


def test_without_the_guard_another_thread_collects(gc_enabled_like_a_fresh_process):
    # The mechanism behind the crash: CPython collects in whichever thread allocates.
    assert "worker" in _threads_that_collect(_make_cyclic_garbage)


def test_with_the_guard_only_the_gui_thread_collects(gc_enabled_like_a_fresh_process):
    from PySide6.QtWidgets import QApplication
    from dataforge.gui import gc_guard
    app = QApplication.instance() or QApplication(["test", "-platform", "offscreen"])
    timer = gc_guard.install(app)
    try:
        assert not gc.isenabled()
        assert timer.isActive()
        assert _threads_that_collect(_make_cyclic_garbage) == []
        assert gc_guard.collect_if_due() is not None   # the GUI thread picks the garbage up
    finally:
        timer.stop()
        timer.deleteLater()


@pytest.mark.parametrize(("counts", "expected"), [
    ((701, 0, 0), 0),
    ((5, 11, 0), 1),
    ((800, 11, 11), 2),
    ((700, 10, 10), None),     # a threshold must be passed, not reached
])
def test_collect_if_due_picks_the_oldest_generation_past_its_threshold(monkeypatch, counts, expected):
    from dataforge.gui import gc_guard
    collected = []
    monkeypatch.setattr(gc_guard.gc, "get_threshold", lambda: (700, 10, 10))
    monkeypatch.setattr(gc_guard.gc, "get_count", lambda: counts)
    monkeypatch.setattr(gc_guard.gc, "collect", collected.append)
    assert gc_guard.collect_if_due() == expected
    assert collected == ([] if expected is None else [expected])


def test_collect_if_due_does_nothing_when_thresholds_are_zero(monkeypatch):
    from dataforge.gui import gc_guard
    collected = []
    monkeypatch.setattr(gc_guard.gc, "get_threshold", lambda: (0, 0, 0))
    monkeypatch.setattr(gc_guard.gc, "get_count", lambda: (10**6, 10**6, 10**6))
    monkeypatch.setattr(gc_guard.gc, "collect", collected.append)
    assert gc_guard.collect_if_due() is None
    assert collected == []
