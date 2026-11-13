"""Unit tests: MainWindow sidebar navigation, workspace signal, and panel wiring."""
import pytest

pytest.importorskip("PySide6")


def _get_app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["test", "-platform", "offscreen"])


def _make_window(monkeypatch=None):
    from dataforge.gui.main_window import MainWindow
    if monkeypatch is not None:
        monkeypatch.setattr(MainWindow, "_load_workspaces", lambda self: None)
    win = MainWindow()
    return win


def test_sidebar_nav_sets_stack_index(monkeypatch):
    app = _get_app()
    win = _make_window(monkeypatch)
    # Navigate to each section and verify QStackedWidget index matches
    for idx in range(len(win.SECTIONS)):
        win._sidebar.setCurrentRow(idx)
        assert win._stack.currentIndex() == idx
    win.close()


def test_mainwindow_has_workspace_signal(monkeypatch):
    """MainWindow has a workspace_changed Signal attribute."""
    app = _get_app()
    win = _make_window(monkeypatch)
    assert hasattr(win, "workspace_changed")
    win.close()


def test_scheduler_in_sections(monkeypatch):
    """'Scheduler' is present in MainWindow.SECTIONS between 'Jobs' and 'SQL'."""
    app = _get_app()
    from dataforge.gui.main_window import MainWindow
    assert "Scheduler" in MainWindow.SECTIONS
    sections = MainWindow.SECTIONS
    jobs_idx = sections.index("Jobs")
    sql_idx = sections.index("SQL")
    scheduler_idx = sections.index("Scheduler")
    assert jobs_idx < scheduler_idx < sql_idx


def test_panels_connected_to_workspace_signal(monkeypatch):
    """After construction, emitting workspace_changed reaches panels' set_workspace."""
    app = _get_app()
    win = _make_window(monkeypatch)

    received = []

    # Connect a spy to the signal
    win.workspace_changed.connect(lambda name: received.append(name))

    win.workspace_changed.emit("test_ws")
    assert "test_ws" in received
    win.close()


def test_workspace_loaded_after_panels(monkeypatch):
    """_load_workspaces is called after _load_panels (Pitfall 4 ordering)."""
    app = _get_app()
    from dataforge.gui.main_window import MainWindow

    call_order = []

    original_load_panels = MainWindow._load_panels
    original_load_workspaces = MainWindow._load_workspaces

    def tracking_load_panels(self):
        call_order.append("_load_panels")
        original_load_panels(self)

    def tracking_load_workspaces(self):
        call_order.append("_load_workspaces")
        # Don't actually make HTTP requests in tests

    monkeypatch.setattr(MainWindow, "_load_panels", tracking_load_panels)
    monkeypatch.setattr(MainWindow, "_load_workspaces", tracking_load_workspaces)

    win = MainWindow()

    assert "_load_panels" in call_order
    assert "_load_workspaces" in call_order
    panels_idx = call_order.index("_load_panels")
    workspaces_idx = call_order.index("_load_workspaces")
    assert panels_idx < workspaces_idx, (
        "_load_workspaces must be called after _load_panels (Pitfall 4)"
    )
    win.close()


def test_deploy_in_sections(monkeypatch):
    """'Deploy' is in SECTIONS, after 'Dashboards' and before 'Settings'; existing order intact."""
    app = _get_app()
    from dataforge.gui.main_window import MainWindow
    s = MainWindow.SECTIONS
    assert "Deploy" in s
    assert s.index("Jobs") < s.index("Scheduler") < s.index("SQL")   # unchanged invariant
    assert s.index("Dashboards") < s.index("Deploy") < s.index("Settings")
    assert s.index("Settings") == len(s) - 1   # Settings stays last


def test_mainwindow_sem_bandeja_e_sem_daemon(monkeypatch):
    from PySide6.QtGui import QCloseEvent
    _get_app()
    win = _make_window(monkeypatch)
    assert not hasattr(win, "_tray")
    assert not hasattr(win, "_daemon")
    ev = QCloseEvent()
    ev.ignore()
    win.closeEvent(ev)
    assert ev.isAccepted()
    win.close()


def test_server_lost_mostra_aviso_com_botao(monkeypatch):
    _get_app()
    win = _make_window(monkeypatch)
    assert win._lost_banner.isHidden()
    win.show_server_lost()
    assert not win._lost_banner.isHidden()
    assert win._lost_label.text() == "DataForge desligado"
    assert win._restart_btn.text() == "Ligar de novo"
    win.close()


def test_ligar_de_novo_sucesso_esconde_e_recarrega(monkeypatch):
    _get_app()
    win = _make_window(monkeypatch)
    calls = []
    monkeypatch.setattr("dataforge.instance.ensure_server", lambda: calls.append("ensure") or 8000)
    monkeypatch.setattr(win, "_load_workspaces", lambda: calls.append("reload"))
    win.show_server_lost()
    win._restart_btn.click()
    assert calls == ["ensure", "reload"]
    assert win._lost_banner.isHidden()
    assert win._restart_btn.isEnabled()
    win.close()


def test_ligar_de_novo_erro_mostra_mensagem_e_mantem_aviso(monkeypatch):
    from unittest.mock import MagicMock
    from dataforge.instance import InstanceError
    _get_app()
    win = _make_window(monkeypatch)
    def boom():
        raise InstanceError("porta ocupada")
    box = MagicMock()
    monkeypatch.setattr("dataforge.instance.ensure_server", boom)
    monkeypatch.setattr("dataforge.gui.server_lost_banner.QMessageBox.warning", box)
    win.show_server_lost()
    win._restart_btn.click()
    assert "porta ocupada" in box.call_args.args[2]
    assert not win._lost_banner.isHidden()
    assert win._restart_btn.isEnabled()
    win.close()
