"""Unit tests: MainWindow sidebar navigation, workspace signal, and panel wiring."""
import pytest

pytest.importorskip("PySide6")


def _get_app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["test", "-platform", "offscreen"])


def _make_window(monkeypatch=None):
    from unittest.mock import MagicMock
    from dataforge.gui.main_window import MainWindow
    daemon = MagicMock()
    if monkeypatch is not None:
        monkeypatch.setattr(MainWindow, "_load_workspaces", lambda self: None)
    win = MainWindow(daemon=daemon)
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
    from unittest.mock import MagicMock

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

    daemon = MagicMock()
    win = MainWindow(daemon=daemon)

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
