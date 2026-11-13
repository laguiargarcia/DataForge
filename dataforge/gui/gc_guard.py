"""Run Python's cyclic garbage collector on the GUI thread only (df-3yh).

CPython starts an automatic collection in whichever thread crosses the allocation threshold.
In the desktop app that is often the embedded API daemon's thread (DaemonThread), which
allocates on every request the panels make. When such a collection frees a Qt object that only
a reference cycle kept alive, Shiboken runs the C++ destructor on the daemon thread and drops
the GIL while doing it, so the GUI thread runs Qt at the same time: a data race that segfaults.
Qt objects must be destroyed on the thread that owns them.

install() turns the automatic collector off and runs the same generational policy from a
QTimer on the GUI thread instead.
"""
import gc

from PySide6.QtCore import QObject, QTimer

INTERVAL_MS = 1000


def collect_if_due() -> int | None:
    """Collect the oldest generation whose count passed its threshold, as CPython would.

    Returns the generation collected, or None when nothing was due.
    """
    counts = gc.get_count()
    thresholds = gc.get_threshold()
    for generation in (2, 1, 0):
        if 0 < thresholds[generation] < counts[generation]:
            gc.collect(generation)
            return generation
    return None


def install(parent: QObject) -> QTimer:
    """Disable automatic collection and collect from a timer on the calling (GUI) thread.

    Call it on the GUI thread before starting any other Python thread. The collector stays off
    for the rest of the process: turning it back on while the daemon thread lives would bring
    the race back.
    """
    gc.disable()
    timer = QTimer(parent)
    timer.timeout.connect(collect_if_due)
    timer.start(INTERVAL_MS)
    return timer
