"""DataForge desktop application entry point.

run_app() is the sole public symbol. It is referenced by both:
  - pyproject.toml [project.scripts]: dataforge-gui = "dataforge.gui:run_app"
  - dataforge/cli.py gui() command

The admin window is a plain client of the single local server: it asks
``instance.ensure_server`` for it (starting the background process if needed),
announces its presence by heartbeat and closes for real.
"""
import signal
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from dataforge import instance
from dataforge.gui import gc_guard
from dataforge.gui.presence_client import PresenceHeartbeat


def run_app() -> int:
    """Start the DataForge admin window.

    Returns the Qt exit code (0 = success, 1 = no server could be reached).
    """
    app = QApplication.instance() or QApplication(sys.argv)

    # Deliver SIGINT (Ctrl+C) to Qt's event loop so the app can exit cleanly.
    # Qt's exec() never yields to Python between events, so a periodic no-op timer
    # forces the interpreter to check pending signals on each tick.
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    _sigint_timer = QTimer()
    _sigint_timer.start(200)
    _sigint_timer.timeout.connect(lambda: None)

    # The cyclic collector runs on this (GUI) thread only, so a collection can never destroy Qt
    # objects on another thread and crash (df-3yh). No Python thread of ours runs here today (the
    # server is another process), but any later one would bring the race back. The app owns the
    # timer, so it lives as long as the app.
    gc_guard.install(app)

    try:
        port = instance.ensure_server()
    except instance.InstanceError as exc:
        QMessageBox.critical(None, "DataForge", str(exc))
        return 1

    # Imported here: MainWindow pulls in the panels, which need a live QApplication.
    from dataforge.gui.main_window import MainWindow  # noqa: PLC0415 -- needs a live QApplication

    window = MainWindow()
    heartbeat = PresenceHeartbeat(port, "admin")
    heartbeat.server_lost.connect(window.show_server_lost)
    heartbeat.server_ok.connect(window.hide_server_lost)
    heartbeat.start()
    window.show()
    try:
        return app.exec()
    finally:
        heartbeat.stop()
        _sigint_timer.stop()
