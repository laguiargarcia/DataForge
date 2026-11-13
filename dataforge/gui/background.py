"""The long-lived "DataForge em segundo plano" process.

It hosts the API server, shows a tray icon (when the desktop has one) and quits by itself once
no window is present and no job runs inside it for the grace period.
"""
import signal
import sys
import time
from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from dataforge import compat, instance
from dataforge.app_backends import supervisor
from dataforge.gui import gc_guard
from dataforge.gui.daemon import DaemonThread
from dataforge.gui.icons import default_icon
from dataforge.presence import registry

CHECK_INTERVAL_MS = 10_000
CONFIRM_QUIT_TEXT = "Há job rodando. Sair mesmo assim?"


class _QuitSignal(QObject):
    """Emitting from any thread queues ``app.quit`` onto the Qt thread."""

    requested = Signal()


def connect_quit(app: QApplication) -> Callable[[], None]:
    """Return the shutdown handler: callable from any thread, it only queues ``app.quit`` on the Qt thread."""
    quitter = _QuitSignal(app)  # parented to the app, so it lives as long as the app
    quitter.requested.connect(app.quit)  # a QObject receiver: the call is queued onto its own thread
    return quitter.requested.emit


def check_auto_shutdown(quit_now: Callable[[], None], now: float | None = None) -> bool:
    """Quit when the grace period ran out. True when it asked to quit."""
    now = time.time() if now is None else now
    if not instance.should_auto_shutdown(registry.empty_since(now), instance.server_jobs(), now):
        return False
    quit_now()
    return True


def _open_admin() -> None:
    instance.spawn_detached(
        [sys.executable, "-m", "dataforge", "gui"], compat.logs_dir() / "gui.log"
    )


def run_background() -> int:
    compat.force_offscreen_without_display()
    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    signal.signal(signal.SIGINT, lambda *_: app.quit())
    keepalive = QTimer()  # lets Python see pending signals while Qt runs
    keepalive.timeout.connect(lambda: None)
    keepalive.start(200)

    # Before the daemon thread exists: a collection it triggers would destroy Qt objects (tray,
    # menu) off the GUI thread and crash (df-3yh). The app owns the timer, so it lives as long as the app.
    gc_guard.install(app)

    daemon = DaemonThread()
    daemon.start()
    instance.set_shutdown_handler(connect_quit(app))

    tray = None
    menu = None
    if QSystemTrayIcon.isSystemTrayAvailable():
        tray = QSystemTrayIcon(default_icon())
        tray.setToolTip("DataForge em segundo plano")
        menu = QMenu()
        open_action = QAction("Abrir DataForge", menu)
        open_action.triggered.connect(_open_admin)
        quit_action = QAction("Sair", menu)

        def on_quit() -> None:
            if instance.server_jobs() > 0 and QMessageBox.question(
                None, "DataForge", CONFIRM_QUIT_TEXT
            ) != QMessageBox.StandardButton.Yes:
                return
            app.quit()

        quit_action.triggered.connect(on_quit)
        menu.addAction(open_action)
        menu.addAction(quit_action)
        tray.setContextMenu(menu)
        tray.show()

    timer = QTimer()
    timer.timeout.connect(lambda: check_auto_shutdown(app.quit))
    timer.start(CHECK_INTERVAL_MS)

    code = app.exec()
    instance.set_shutdown_handler(None)
    # daemon.stop() ends uvicorn; the API lifespan then stops the app backends (supervisor.stop_all).
    daemon.stop()
    if daemon.is_alive():  # the lifespan did not finish in time: do not leave backends behind
        supervisor.stop_all()
    return code
