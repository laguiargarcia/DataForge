"""run_background wiring that can be checked without a real process."""
import os
from typing import ClassVar

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dataforge import instance
from dataforge.gui import background


def test_tick_quits_only_when_rule_says_so(monkeypatch):
    monkeypatch.setenv("DATAFORGE_GRACE_SECONDS", "60")
    quits = []
    monkeypatch.setattr(background.registry, "empty_since", lambda now: 0.0)
    monkeypatch.setattr(instance, "server_jobs", lambda: 0)
    background.check_auto_shutdown(lambda: quits.append(1), now=30.0)
    assert quits == []
    background.check_auto_shutdown(lambda: quits.append(1), now=61.0)
    assert quits == [1]


def test_shutdown_handler_from_another_thread_quits_the_qt_loop():
    import threading

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    handler = background.connect_quit(app)
    result = {}

    def from_request_thread():
        result["thread"] = threading.current_thread()
        handler()  # what a FastAPI threadpool thread does

    QTimer.singleShot(0, lambda: threading.Thread(target=from_request_thread).start())
    watchdog = QTimer()
    watchdog.setSingleShot(True)
    watchdog.timeout.connect(lambda: app.exit(99))
    watchdog.start(5000)
    try:
        assert app.exec() == 0
    finally:
        watchdog.stop()  # a pending watchdog would exit some later test's event loop
    assert result["thread"] is not threading.main_thread()


class _FakeDaemon:
    """Stands in for the uvicorn thread; ``alive`` is what is_alive() says after stop()."""

    def __init__(self, events, alive):
        self._events, self._alive = events, alive

    def start(self):
        self._events.append("daemon.start")

    def stop(self):
        self._events.append("daemon.stop")

    def is_alive(self):
        return self._alive


class _FakeTray:
    """Records what run_background does with the tray icon (no real tray under offscreen)."""

    instances: ClassVar[list] = []

    def __init__(self, icon):
        self.icon, self.menu, self.shown, self.tooltip = icon, None, False, None
        _FakeTray.instances.append(self)

    @staticmethod
    def isSystemTrayAvailable():
        return True

    def setToolTip(self, text):
        self.tooltip = text

    def setContextMenu(self, menu):
        self.menu = menu

    def show(self):
        self.shown = True


def _patch_run_background(monkeypatch, events, alive, in_loop=None, tray=True):
    """run_background with its process-wide side effects replaced; ``in_loop(app)`` runs as app.exec."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    _FakeTray.instances = []
    monkeypatch.setattr(background.signal, "signal", lambda *a: None)  # keep SIGINT handler out of pytest
    monkeypatch.setattr(background, "DaemonThread", lambda: _FakeDaemon(events, alive))
    # df-3yh: the guard goes in before the daemon thread exists (recorded, so no timer leaks)
    monkeypatch.setattr(background.gc_guard, "install", lambda parent: events.append("gc_guard.install"))
    monkeypatch.setattr(background.supervisor, "stop_all", lambda: events.append("stop_all"))
    if tray:
        monkeypatch.setattr(background, "QSystemTrayIcon", _FakeTray)
    else:
        monkeypatch.setattr(background.QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: False))

    def fake_exec():
        events.append(("handler set", instance._State.shutdown_handler is not None))
        if in_loop is not None:
            in_loop(app)
        return 0

    monkeypatch.setattr(app, "exec", fake_exec, raising=False)
    monkeypatch.setattr(app, "quit", lambda: events.append("app.quit"), raising=False)
    return app


def test_run_background_para_o_daemon_e_os_backends_se_ele_nao_terminou(monkeypatch):
    events = []
    _patch_run_background(monkeypatch, events, alive=True)
    assert background.run_background() == 0
    assert events == ["gc_guard.install", "daemon.start", ("handler set", True), "daemon.stop", "stop_all"]
    assert instance._State.shutdown_handler is None  # cleared on the way out


def test_run_background_deixa_os_backends_para_o_lifespan(monkeypatch):
    events = []
    _patch_run_background(monkeypatch, events, alive=False)
    assert background.run_background() == 0
    assert events == ["gc_guard.install", "daemon.start", ("handler set", True), "daemon.stop"]
    assert instance._State.shutdown_handler is None


def test_run_background_sem_bandeja_nao_cria_icone(monkeypatch):
    events = []
    _patch_run_background(monkeypatch, events, alive=False, tray=False)
    assert background.run_background() == 0
    assert _FakeTray.instances == []


def test_run_background_icone_visivel_e_menu(monkeypatch, dataforge_home):
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QMessageBox

    events, opened, answers = [], [], []
    monkeypatch.setattr(background.instance, "spawn_detached", lambda cmd, log: opened.append((cmd, log)))
    monkeypatch.setattr(background.QMessageBox, "question", lambda *a: answers.pop(0))
    jobs = {"n": 0}
    monkeypatch.setattr(background.instance, "server_jobs", lambda: jobs["n"])

    def in_loop(_app):
        tray = _FakeTray.instances[0]
        assert tray.shown
        assert tray.tooltip == "DataForge em segundo plano"
        image = tray.icon.pixmap(32, 32).toImage()
        assert image.pixelColor(1, 1) == QColor("#4A90D9")  # not a blank white square
        assert len({image.pixel(x, y) for x in range(32) for y in range(32)}) > 1  # the "D" is drawn
        open_action, quit_action = tray.menu.actions()
        assert (open_action.text(), quit_action.text()) == ("Abrir DataForge", "Sair")
        open_action.trigger()
        quit_action.trigger()  # no job: quits without asking
        jobs["n"] = 1
        answers.extend([QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes])
        quit_action.trigger()  # job running, answered No: stays
        quit_action.trigger()  # answered Yes: quits
        events.append("checked")

    _patch_run_background(monkeypatch, events, alive=False, in_loop=in_loop)
    assert background.run_background() == 0
    assert events == ["gc_guard.install", "daemon.start", ("handler set", True), "app.quit", "app.quit", "checked",
                      "daemon.stop"]
    assert len(opened) == 1
    assert opened[0][0][-2:] == ["dataforge", "gui"]
    assert opened[0][1].name == "gui.log"
    assert answers == []


def test_icone_padrao_nao_e_branco():
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QApplication

    from dataforge.gui.icons import default_icon

    QApplication.instance() or QApplication([])
    image = default_icon().pixmap(32, 32).toImage()
    assert image.pixelColor(0, 0) == QColor("#4A90D9")
    assert QColor("white").rgb() in {image.pixel(x, y) for x in range(32) for y in range(32)}  # the "D"
