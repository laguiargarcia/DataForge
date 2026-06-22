"""DataForge desktop application entry point.

run_app() is the sole public symbol. It is referenced by both:
  - pyproject.toml [project.scripts]: dataforge-gui = "dataforge.gui:run_app"
  - dataforge/cli.py gui() command
"""
import signal
import sys

from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from dataforge.gui.daemon import DaemonThread


def run_app() -> int:
    """Start the DataForge desktop application.

    Returns the Qt exit code (0 = success).
    """
    # QApplication must be created before any widget or tray object
    app = QApplication.instance() or QApplication(sys.argv)

    # Deliver SIGINT (Ctrl+C) to Qt's event loop so the app can exit cleanly.
    # Qt's exec() never yields to Python between events, so a periodic no-op timer
    # forces the interpreter to check pending signals on each tick.
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    _sigint_timer = QTimer()
    _sigint_timer.start(200)
    _sigint_timer.timeout.connect(lambda: None)

    # Only suppress auto-quit when a real system tray is available to take over.
    if QSystemTrayIcon.isSystemTrayAvailable():
        QGuiApplication.setQuitOnLastWindowClosed(False)

    daemon = DaemonThread()
    daemon.start()

    # Import MainWindow here — not at module level — because MainWindow
    # references panel modules that import PySide6 widgets; safe only after
    # QApplication exists. This is an accepted lazy import for the GUI entry path.
    from dataforge.gui.main_window import MainWindow  # noqa: PLC0415

    window = MainWindow(daemon=daemon)
    window.show()

    exit_code = app.exec()
    # Ensure daemon stops cleanly when Qt exits via SIGINT (which skips _shutdown_and_quit).
    if daemon.is_alive():
        daemon.stop()
    return exit_code
