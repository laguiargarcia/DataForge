"""Smoke tests: dataforge.gui importable; QApplication starts offscreen."""
import pytest

PySide6 = pytest.importorskip("PySide6")


def test_run_app_importable():
    from dataforge.gui import run_app
    assert callable(run_app)


def test_qapplication_offscreen():
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QGuiApplication
    app = QApplication.instance() or QApplication(["test", "-platform", "offscreen"])
    QGuiApplication.setQuitOnLastWindowClosed(False)
    assert app is not None


def test_run_app_mostra_erro_de_instancia(monkeypatch):
    from unittest.mock import MagicMock
    from PySide6.QtWidgets import QApplication
    import dataforge.gui as gui
    from dataforge.instance import InstanceError
    QApplication.instance() or QApplication(["test", "-platform", "offscreen"])
    def boom(*a, **k):
        raise InstanceError("a porta 8000 está ocupada por outro programa; defina DATAFORGE_PORT")
    box = MagicMock()
    monkeypatch.setattr(gui.signal, "signal", lambda *a: None)  # keep SIGINT handler out of pytest
    monkeypatch.setattr(gui.gc_guard, "install", lambda parent: None)  # no guard timer left on the app
    monkeypatch.setattr(gui.instance, "ensure_server", boom)
    monkeypatch.setattr(gui.QMessageBox, "critical", box)
    assert gui.run_app() == 1
    assert "ocupada" in box.call_args.args[2]


def test_run_app_caminho_feliz(monkeypatch):
    from unittest.mock import MagicMock
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QApplication
    import dataforge.gui as gui
    import dataforge.gui.main_window as mw
    app = QApplication.instance() or QApplication(["test", "-platform", "offscreen"])
    events = []

    class FakeHeartbeat(QObject):
        server_lost = Signal()
        server_ok = Signal()
        def __init__(self, port, kind, *a, **k):
            super().__init__()
            events.append(("init", port, kind))
        def start(self): events.append("start")
        def stop(self): events.append("stop")

    window = MagicMock()
    monkeypatch.setattr(gui.signal, "signal", lambda *a: None)
    # df-3yh: the collector is moved to the GUI thread before anything else starts
    monkeypatch.setattr(gui.gc_guard, "install", lambda parent: events.append(("gc_guard", parent is app)))
    monkeypatch.setattr(gui.instance, "ensure_server", lambda: events.append("ensure_server") or 8123)
    monkeypatch.setattr(gui, "PresenceHeartbeat", FakeHeartbeat)
    monkeypatch.setattr(mw, "MainWindow", lambda: window)
    monkeypatch.setattr(app, "exec", lambda: 0, raising=False)
    assert gui.run_app() == 0
    assert events == [("gc_guard", True), "ensure_server", ("init", 8123, "admin"), "start", "stop"]
    window.show.assert_called_once()
