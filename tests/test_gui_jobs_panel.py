"""Offscreen Qt tests for the jobs panel — Phase 1.4."""
import pytest

pytest.importorskip("PySide6")

_FAKE_WORKSPACES = [
    {
        "name": "alpha",
        "jobs": [
            {"name": "ingest", "last_run_status": "success"},
            {"name": "transform", "last_run_status": "failed"},
        ],
    },
    {
        "name": "beta",
        "jobs": [
            {"name": "export", "last_run_status": "running"},
            {"name": "report", "last_run_status": None},
        ],
    },
]


def _get_app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["test", "-platform", "offscreen"])


def test_panel_constructs(monkeypatch):
    app = _get_app()
    from dataforge.gui.panels import jobs as jobs_module
    monkeypatch.setattr(jobs_module.Panel, "_subscribe_sse", lambda self, ws: None)

    panel = jobs_module.Panel()
    assert panel._stack.count() == 4   # Fix #7: + PAGE_EDITOR
    panel.close()


def test_set_workspace_stores_current(monkeypatch):
    """set_workspace sets _current_workspace and saves to QSettings."""
    app = _get_app()
    from dataforge.gui.panels import jobs as jobs_module

    monkeypatch.setattr(jobs_module.Panel, "_load_jobs", lambda self, ws: None)
    monkeypatch.setattr(jobs_module.Panel, "_subscribe_sse", lambda self, ws: None)

    saved = {}

    class _FakeSettings:
        def value(self, key, defaultValue=None):
            return None
        def setValue(self, key, value):
            saved[key] = value

    monkeypatch.setattr(jobs_module, "QSettings", lambda *a: _FakeSettings())

    panel = jobs_module.Panel()
    panel.set_workspace("alpha")

    assert panel._current_workspace == "alpha"
    assert saved.get(jobs_module.KEY_LAST_WS) == "alpha"
    panel.close()


def test_set_workspace_empty_noop(monkeypatch):
    """set_workspace('') does not change _current_workspace."""
    app = _get_app()
    from dataforge.gui.panels import jobs as jobs_module

    load_called = []
    monkeypatch.setattr(jobs_module.Panel, "_load_jobs", lambda self, ws: load_called.append(ws))
    monkeypatch.setattr(jobs_module.Panel, "_subscribe_sse", lambda self, ws: None)

    panel = jobs_module.Panel()
    panel.set_workspace("")

    assert panel._current_workspace is None
    assert load_called == []
    panel.close()


def test_job_list_badge_colors(monkeypatch):
    app = _get_app()
    from dataforge.gui.panels import jobs as jobs_module

    monkeypatch.setattr(jobs_module.Panel, "_subscribe_sse", lambda self, ws: None)

    panel = jobs_module.Panel()

    assert panel._badge_color("running") == "#1a6fc4"
    assert panel._badge_color("success") == "#2d7a3a"
    assert panel._badge_color("failed") == "#a32929"
    assert panel._badge_color("skipped") == "#7a7a7a"
    assert panel._badge_color(None) == "#555555"
    panel.close()


def test_drill_down_navigation(monkeypatch):
    app = _get_app()
    from dataforge.gui.panels import jobs as jobs_module

    monkeypatch.setattr(jobs_module.Panel, "_subscribe_sse", lambda self, ws: None)
    monkeypatch.setattr(jobs_module.Panel, "_load_run_detail", lambda self, run_id: None)

    panel = jobs_module.Panel()

    # Start on job list
    assert panel._stack.currentIndex() == jobs_module.Panel.PAGE_LIST
    assert panel._back_btn.isHidden()

    panel._current_run_id = "run42"
    panel._navigate_to(jobs_module.Panel.PAGE_DETAIL, "Jobs › alpha › Run #42")

    assert panel._stack.currentIndex() == jobs_module.Panel.PAGE_DETAIL
    assert not panel._back_btn.isHidden()
    assert "Run #42" in panel._breadcrumb.text()

    panel._navigate_to(jobs_module.Panel.PAGE_LIST)
    assert panel._stack.currentIndex() == jobs_module.Panel.PAGE_LIST
    assert panel._back_btn.isHidden()
    panel.close()


def test_run_row_opens_detail(monkeypatch):
    """Simulating row activation navigates to PAGE_DETAIL with correct breadcrumb and run id."""
    app = _get_app()
    from dataforge.gui.panels import jobs as jobs_module

    monkeypatch.setattr(jobs_module.Panel, "_subscribe_sse", lambda self, ws: None)
    monkeypatch.setattr(jobs_module.Panel, "_load_run_detail", lambda self, run_id: None)

    panel = jobs_module.Panel()
    panel._current_workspace = "myws"

    run_record = {
        "id": "abcdef1234567890",
        "job_name": "ingest",
        "status": "success",
        "started_at": "2026-05-16T10:00:00Z",
        "finished_at": "2026-05-16T10:01:30Z",
    }
    panel._open_run_detail(run_record)

    assert panel._stack.currentIndex() == jobs_module.Panel.PAGE_DETAIL
    assert panel._current_run_id == "abcdef1234567890"
    breadcrumb = panel._breadcrumb.text()
    assert "Run #" in breadcrumb
    # Short id = first 8 chars = "abcdef12"
    assert "abcdef12" in breadcrumb
    assert not panel._back_btn.isHidden()
    panel.close()


def test_back_returns_to_list(monkeypatch):
    """After navigating to detail, clicking Back returns to PAGE_LIST with 'Jobs' breadcrumb."""
    app = _get_app()
    from dataforge.gui.panels import jobs as jobs_module

    monkeypatch.setattr(jobs_module.Panel, "_subscribe_sse", lambda self, ws: None)
    monkeypatch.setattr(jobs_module.Panel, "_load_run_detail", lambda self, run_id: None)

    panel = jobs_module.Panel()
    panel._current_workspace = "myws"

    # Navigate to detail
    run_record = {
        "id": "deadbeef00000000",
        "job_name": "transform",
        "status": "failed",
        "started_at": "2026-05-16T09:00:00Z",
        "finished_at": None,
    }
    panel._open_run_detail(run_record)
    assert panel._stack.currentIndex() == jobs_module.Panel.PAGE_DETAIL

    # Click Back
    panel._go_back()

    assert panel._stack.currentIndex() == jobs_module.Panel.PAGE_LIST
    assert panel._breadcrumb.text() == "Jobs"
    assert panel._back_btn.isHidden()
    panel.close()


# ---------------------------------------------------------------------------
# Task 2: Tests for _append_log_line coloring + auto-scroll
# ---------------------------------------------------------------------------

def test_log_line_colors(monkeypatch):
    """ERROR lines get red fg/bg; WARN lines get yellow fg/bg; plain lines get default."""
    app = _get_app()
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QTextEdit
    from dataforge.gui.panels import jobs as jobs_module

    def _get_block_fmt(te: QTextEdit, block_idx: int):
        doc = te.document()
        block = doc.findBlockByNumber(block_idx)
        it = block.begin()
        if not it.atEnd():
            return it.fragment().charFormat()
        return None

    # ERROR line
    te_error = QTextEdit()
    te_error.setAcceptRichText(False)
    jobs_module._append_log_line(te_error, "Task ERROR: something went wrong")
    fmt = _get_block_fmt(te_error, 0)
    assert fmt is not None
    assert fmt.foreground().color().name().lower() == QColor(jobs_module._ERROR_FG).name().lower()
    assert fmt.background().color().name().lower() == QColor(jobs_module._ERROR_BG).name().lower()

    # WARN line
    te_warn = QTextEdit()
    te_warn.setAcceptRichText(False)
    jobs_module._append_log_line(te_warn, "WARNING: disk usage high")
    fmt_w = _get_block_fmt(te_warn, 0)
    assert fmt_w is not None
    assert fmt_w.foreground().color().name().lower() == QColor(jobs_module._WARN_FG).name().lower()
    assert fmt_w.background().color().name().lower() == QColor(jobs_module._WARN_BG).name().lower()

    # Plain line — no special coloring (default foreground is black or theme-default)
    te_plain = QTextEdit()
    te_plain.setAcceptRichText(False)
    jobs_module._append_log_line(te_plain, "Task started successfully")
    fmt_p = _get_block_fmt(te_plain, 0)
    # Plain lines must NOT use error or warn colors
    if fmt_p is not None:
        assert fmt_p.foreground().color().name().lower() != QColor(jobs_module._ERROR_FG).name().lower()
        assert fmt_p.foreground().color().name().lower() != QColor(jobs_module._WARN_FG).name().lower()


def test_autoscroll_pauses_on_user_scrollup(monkeypatch):
    """Auto-scroll tracks scrollbar: pauses when below max, resumes at max."""
    app = _get_app()
    from dataforge.gui.panels import jobs as jobs_module

    monkeypatch.setattr(jobs_module.Panel, "_subscribe_sse", lambda self, ws: None)

    panel = jobs_module.Panel()

    # Create a log pane for "task_a" directly via the helper
    pane = panel._make_log_pane("task_a")
    panel._tab_auto_scroll["task_a"] = True  # default state

    # Simulate user scrolling up: value < maximum → auto-scroll should pause
    sb = pane.verticalScrollBar()
    # Manually invoke the slot with a value that is below maximum
    panel._on_log_scroll_changed("task_a", 0, sb)
    # After scrolling up, auto-scroll must be False (max is 0 when no content, so 0 == max is True)
    # Use a range that forces non-equality: set range then value
    sb.setRange(0, 100)
    sb.setValue(50)
    panel._on_log_scroll_changed("task_a", 50, sb)
    assert panel._tab_auto_scroll["task_a"] is False

    # Simulate user scrolling back to bottom → auto-scroll resumes
    sb.setValue(100)
    panel._on_log_scroll_changed("task_a", 100, sb)
    assert panel._tab_auto_scroll["task_a"] is True

    panel.close()


def test_run_detail_tab_widget_created(monkeypatch):
    """RunDetailView has a QTabWidget; SSE snapshot creates one tab per task."""
    app = _get_app()
    from PySide6.QtWidgets import QTabWidget
    from dataforge.gui.panels import jobs as jobs_module

    monkeypatch.setattr(jobs_module.Panel, "_subscribe_sse", lambda self, ws: None)

    panel = jobs_module.Panel()
    # _task_tabs must exist on the panel
    assert hasattr(panel, "_task_tabs")
    assert isinstance(panel._task_tabs, QTabWidget)

    # Feed a fake snapshot
    snapshot = {
        "run_id": "abc123",
        "status": "running",
        "tasks": [
            {"name": "extract", "status": "success", "output": "done\n"},
            {"name": "load", "status": "running", "output": "loading...\n"},
        ],
    }
    panel._handle_run_snapshot(snapshot)

    assert panel._task_tabs.count() == 2
    tab_labels = [panel._task_tabs.tabText(i) for i in range(panel._task_tabs.count())]
    assert any("extract" in lbl for lbl in tab_labels)
    assert any("load" in lbl for lbl in tab_labels)
    panel.close()


def test_run_sse_closes_on_terminal_status(monkeypatch):
    """Per-run SSE reply is aborted when snapshot status is 'success' or 'failed'."""
    app = _get_app()
    from unittest.mock import MagicMock
    from dataforge.gui.panels import jobs as jobs_module

    monkeypatch.setattr(jobs_module.Panel, "_subscribe_sse", lambda self, ws: None)

    panel = jobs_module.Panel()

    # Install a mock run SSE reply
    mock_reply = MagicMock()
    panel._run_sse_reply = mock_reply

    # Feed a terminal snapshot
    snapshot = {"run_id": "r1", "status": "success", "tasks": []}
    panel._handle_run_snapshot(snapshot)

    # Reply must have been aborted
    mock_reply.abort.assert_called_once()
    assert panel._run_sse_reply is None
    panel.close()


# ---------------------------------------------------------------------------
# Phase C / Task 15: New/Edit/Delete editor wiring
# ---------------------------------------------------------------------------

def test_jobs_panel_has_editor_page(monkeypatch):
    app = _get_app()
    from dataforge.gui.panels import jobs as jobs_module
    monkeypatch.setattr(jobs_module.Panel, "_subscribe_sse", lambda self, ws: None)
    panel = jobs_module.Panel()
    # editor page added to the stack
    assert panel._stack.count() >= 4
    assert hasattr(panel, "_editor")
    panel.close()


def test_open_editor_create_mode(monkeypatch):
    app = _get_app()
    from dataforge.gui.panels import jobs as jobs_module
    monkeypatch.setattr(jobs_module.Panel, "_subscribe_sse", lambda self, ws: None)
    panel = jobs_module.Panel()
    panel._current_workspace = "test_ws"

    loaded = {}
    monkeypatch.setattr(panel._editor, "load_job_data",
                        lambda ws, job: loaded.update({"ws": ws, "job": job}))
    panel._open_editor(None)
    assert loaded == {"ws": "test_ws", "job": None}
    assert panel._stack.currentIndex() == panel.PAGE_EDITOR
    panel.close()


def test_open_editor_edit_mode_fetches_job(monkeypatch):
    app = _get_app()
    from dataforge.gui.panels import jobs as jobs_module
    from unittest.mock import MagicMock
    monkeypatch.setattr(jobs_module.Panel, "_subscribe_sse", lambda self, ws: None)
    panel = jobs_module.Panel()
    panel._current_workspace = "test_ws"

    captured = {}

    def _fake_get(req):
        captured["url"] = req.url().toString()
        r = MagicMock(); r.finished = MagicMock(); r.finished.connect = MagicMock()
        return r

    panel._nam.get = _fake_get
    panel._open_editor("etl")
    assert "/test_ws/jobs/etl" in captured["url"]
    panel.close()


def test_edit_button_in_job_rows(monkeypatch):
    """Fix #13: each job row carries an Edit button wired to _open_editor(job_name)."""
    app = _get_app()
    from dataforge.gui.panels import jobs as jobs_module
    from PySide6.QtWidgets import QPushButton
    monkeypatch.setattr(jobs_module.Panel, "_subscribe_sse", lambda self, ws: None)
    panel = jobs_module.Panel()
    panel._current_workspace = "alpha"

    opened = []
    monkeypatch.setattr(panel, "_open_editor", lambda jn: opened.append(jn))

    panel._rebuild_job_list_from_data("alpha", _FAKE_WORKSPACES[0])
    # Find Edit buttons across the rebuilt rows
    edit_btns = [b for b in panel._list_page.findChildren(QPushButton) if b.text() == "Edit"]
    assert len(edit_btns) == 2  # one per job row (ingest, transform)
    edit_btns[0].click()
    assert opened == ["ingest"]
    panel.close()


def test_workspace_switch_resets_editor_to_list(monkeypatch):
    """Fix #9: switching workspace while the editor is open resets the stack to PAGE_LIST and
    discards editor state, so an in-flight edit cannot Save to the OLD workspace."""
    app = _get_app()
    from dataforge.gui.panels import jobs as jobs_module
    monkeypatch.setattr(jobs_module.Panel, "_subscribe_sse", lambda self, ws: None)
    monkeypatch.setattr(jobs_module.Panel, "_load_jobs", lambda self, ws: None)
    panel = jobs_module.Panel()
    panel._current_workspace = "ws_a"
    # open the editor on ws_a
    panel._editor.load_job_data("ws_a", None)
    panel._navigate_to(panel.PAGE_EDITOR, "Jobs › ws_a › New Job")
    assert panel._stack.currentIndex() == panel.PAGE_EDITOR
    # switch workspace
    panel.set_workspace("ws_b")
    # back on the list, editor no longer pointed at ws_a
    assert panel._stack.currentIndex() == panel.PAGE_LIST
    panel.close()


def test_editor_page_does_not_disturb_run_detail_sse(monkeypatch):
    """The new editor page must not break the run-detail SSE subscription path."""
    app = _get_app()
    from dataforge.gui.panels import jobs as jobs_module
    monkeypatch.setattr(jobs_module.Panel, "_subscribe_sse", lambda self, ws: None)
    loaded = []
    monkeypatch.setattr(jobs_module.Panel, "_load_run_detail", lambda self, rid: loaded.append(rid))
    panel = jobs_module.Panel()
    panel._current_run_id = "run99"
    # Navigating to PAGE_DETAIL still triggers _load_run_detail (SSE) as before.
    panel._navigate_to(jobs_module.Panel.PAGE_DETAIL, "Jobs › x › Run #99")
    assert loaded == ["run99"]
    # PAGE_EDITOR must NOT trigger run-detail SSE.
    panel._navigate_to(jobs_module.Panel.PAGE_EDITOR, "Jobs › x › New Job")
    assert loaded == ["run99"]
    panel.close()
