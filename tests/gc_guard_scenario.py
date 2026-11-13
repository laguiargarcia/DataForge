"""Child process for test_gui_gc_guard.py (df-3yh): Qt garbage, then a worker thread allocating.

Run as `python tests/gc_guard_scenario.py guard|noguard`. Dropped deploy panels sit in reference
cycles, so only the cyclic GC frees them. A worker thread then makes cyclic garbage of its own.
Without the guard the worker's collection frees the panels off the GUI thread and the process
segfaults; with gc_guard.install() only the main thread collects and the process exits 0.
"""
import gc
import sys
import threading


class _Cycle:
    def __init__(self):
        self.me = self


def main(mode: str) -> None:
    from PySide6.QtWidgets import QApplication
    from dataforge.gui import gc_guard
    from dataforge.gui.panels import deploy

    app = QApplication.instance() or QApplication(["scenario", "-platform", "offscreen"])
    deploy.Panel._load_pipelines = lambda self: None     # no network
    if mode == "guard":
        gc_guard.install(app)
    for _ in range(5):
        panel = deploy.Panel()
        panel.cycle = panel          # garbage only the cyclic GC can free
        del panel

    def allocate():
        for _ in range(50 * gc.get_threshold()[0]):
            _Cycle()

    worker = threading.Thread(target=allocate)
    worker.start()
    worker.join()
    gc.collect()                     # the panels die here, on the main thread, when guarded
    print("ok")


if __name__ == "__main__":
    main(sys.argv[1])
