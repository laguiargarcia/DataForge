"""Offscreen tests for the jobs panel's run snapshot and run history rendering: what each tab,
log, badge and history cell shows for a given payload (df-a4cq.2, written before the S3776 split
of _handle_run_snapshot and _on_history_loaded)."""
import json
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PySide6")


@pytest.fixture
def panel(monkeypatch):
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(["test", "-platform", "offscreen"])
    from dataforge.gui.panels import jobs as jobs_module
    monkeypatch.setattr(jobs_module.Panel, "_subscribe_sse", lambda self, ws: None)
    p = jobs_module.Panel()
    yield p
    p.close()
    del app


def _tabs(panel):
    return [panel._task_tabs.tabText(i) for i in range(panel._task_tabs.count())]


def _log(panel, i):
    """The log pane's lines (each _append_log_line ends its line with a newline)."""
    return panel._task_tabs.widget(i).toPlainText().splitlines()


# ---------------------------------------------------------------------------
# _handle_run_snapshot
# ---------------------------------------------------------------------------

def test_snapshot_sets_status_badge(panel):
    panel._handle_run_snapshot({"status": "running", "tasks": []})
    assert panel._detail_status_badge.text() == "running"
    assert panel._badge_color("running") in panel._detail_status_badge.styleSheet()


def test_snapshot_without_status_or_tasks_clears_badge(panel):
    panel._handle_run_snapshot({})
    assert panel._detail_status_badge.text() == ""
    assert _tabs(panel) == []


def test_snapshot_tab_label_and_name_fallbacks(panel):
    panel._handle_run_snapshot({"status": "running", "tasks": [
        {"name": "a", "status": "success"},
        {"task": "b", "status": "running"},
        {"status": "failed"},
        {"name": "d"},
    ]})
    assert _tabs(panel) == ["a [success]", "b [running]", "(unnamed) [failed]", "d"]
    assert panel._tab_auto_scroll == {"a": True, "b": True, "(unnamed)": True, "d": True}


def test_snapshot_writes_one_log_line_per_output_line(panel):
    panel._handle_run_snapshot({"status": "running", "tasks": [
        {"name": "a", "status": "running", "output": "one\ntwo\n"},
    ]})
    assert _log(panel, 0) == ["one", "two"]


def test_second_snapshot_updates_label_and_replaces_log_without_new_tab(panel):
    """df-0kcj: a status change relabels the task's tab instead of adding a second one."""
    panel._handle_run_snapshot({"status": "running", "tasks": [
        {"name": "a", "status": "running", "output": "one\n"},
    ]})
    panel._handle_run_snapshot({"status": "running", "tasks": [
        {"name": "a", "status": "success", "output": "one\ntwo\n"},
    ]})
    assert _tabs(panel) == ["a [success]"]
    assert _log(panel, 0) == ["one", "two"]


def test_second_snapshot_with_same_status_reuses_the_tab(panel):
    panel._handle_run_snapshot({"status": "running", "tasks": [
        {"name": "a", "status": "running", "output": "one\n"},
    ]})
    panel._handle_run_snapshot({"status": "running", "tasks": [
        {"name": "a", "status": "running", "output": "one\ntwo\n"},
    ]})
    assert _tabs(panel) == ["a [running]"]
    assert _log(panel, 0) == ["one", "two"]


def test_a_whole_run_keeps_one_tab_per_task(panel):
    for statuses in [("queued", "queued"), ("running", "queued"), ("success", "running"),
                     ("success", "failed")]:
        panel._handle_run_snapshot({"status": "running", "tasks": [
            {"name": "extract", "status": statuses[0], "output": f"extract {statuses[0]}\n"},
            {"name": "load", "status": statuses[1], "output": f"load {statuses[1]}\n"},
        ]})
    assert _tabs(panel) == ["extract [success]", "load [failed]"]
    assert _log(panel, 0) == ["extract success"]
    assert _log(panel, 1) == ["load failed"]


def test_task_named_like_another_tabs_label_gets_its_own_tab(panel):
    """Tabs are found by task name, not by their text: a task literally called 'a [running]'
    is not task 'a' while running."""
    panel._handle_run_snapshot({"status": "running", "tasks": [
        {"name": "a", "status": "running", "output": "from a\n"},
    ]})
    panel._handle_run_snapshot({"status": "running", "tasks": [
        {"name": "a [running]", "output": "from the other\n"},
    ]})
    assert _tabs(panel) == ["a [running]", "a [running]"]
    assert _log(panel, 0) == ["from a"]
    assert _log(panel, 1) == ["from the other"]


def test_new_run_starts_with_no_tabs(panel, monkeypatch):
    """Opening another run clears the previous run's tabs, so the lookup starts empty."""
    panel._handle_run_snapshot({"status": "success", "tasks": [{"name": "a", "status": "success"}]})
    monkeypatch.setattr(panel, "_nam", MagicMock())   # no real SSE connection
    panel._load_run_detail("run-2")
    panel._handle_run_snapshot({"status": "running", "tasks": [{"name": "a", "status": "running"}]})
    assert _tabs(panel) == ["a [running]"]


def test_snapshot_with_empty_output_keeps_the_previous_log(panel):
    panel._handle_run_snapshot({"status": "running", "tasks": [{"name": "a", "output": "kept\n"}]})
    panel._handle_run_snapshot({"status": "running", "tasks": [{"name": "a", "output": ""}]})
    assert _log(panel, 0) == ["kept"]


def test_snapshot_existing_task_keeps_its_tab_position(panel):
    panel._handle_run_snapshot({"status": "running", "tasks": [{"name": "a"}, {"name": "b"}]})
    panel._handle_run_snapshot({"status": "running", "tasks": [
        {"name": "b", "status": "success", "output": "B\n"},
        {"name": "c", "status": "running"},
    ]})
    assert _tabs(panel) == ["a", "b [success]", "c [running]"]
    assert _log(panel, 1) == ["B"]


_MANY_LINES = "".join(f"line {n}\n" for n in range(300))


def _shown_log_scrollbar(panel):
    """Show the detail page with one laid-out tab 'a', so its scrollbar gets a real range."""
    from PySide6.QtWidgets import QApplication
    panel.resize(600, 400)
    panel.show()
    panel._stack.setCurrentIndex(panel.PAGE_DETAIL)
    panel._handle_run_snapshot({"status": "running", "tasks": [{"name": "a", "output": "x\n"}]})
    QApplication.processEvents()
    return panel._task_tabs.widget(0).verticalScrollBar()


def test_snapshot_scrolls_the_log_to_the_bottom(panel):
    sb = _shown_log_scrollbar(panel)
    panel._handle_run_snapshot({"status": "running", "tasks": [{"name": "a", "output": _MANY_LINES}]})
    # Scrolled to the bottom it knew of; the document layout is lazy and can still grow the
    # maximum by a few pixels afterwards, so "scrolled" is the observable, not value == maximum.
    assert sb.value() > sb.maximum() // 2


def _paused_at(panel, sb, position):
    """Fill tab 'a' with a long log, then scroll it like the user would."""
    from PySide6.QtWidgets import QApplication
    panel._handle_run_snapshot({"status": "running", "tasks": [{"name": "a", "output": _MANY_LINES}]})
    QApplication.processEvents()
    sb.setValue(position(sb))
    assert panel._tab_auto_scroll["a"] is False


@pytest.mark.parametrize("position", [lambda sb: 0, lambda sb: sb.maximum() // 2],
                         ids=["top", "middle"])
def test_snapshot_keeps_position_when_auto_scroll_is_paused(panel, position):
    from PySide6.QtWidgets import QApplication
    sb = _shown_log_scrollbar(panel)
    _paused_at(panel, sb, position)
    kept = sb.value()
    panel._handle_run_snapshot({"status": "running", "tasks": [{"name": "a", "output": _MANY_LINES}]})
    QApplication.processEvents()
    assert sb.value() == kept
    assert panel._tab_auto_scroll["a"] is False


def test_snapshot_while_paused_still_replaces_the_log(panel):
    sb = _shown_log_scrollbar(panel)
    _paused_at(panel, sb, lambda sb: 0)
    panel._handle_run_snapshot({"status": "running", "tasks": [{"name": "a", "output": "new\n"}]})
    assert _log(panel, 0) == ["new"]


def test_scrolling_back_to_the_bottom_resumes_auto_scroll(panel):
    from PySide6.QtWidgets import QApplication
    sb = _shown_log_scrollbar(panel)
    _paused_at(panel, sb, lambda sb: 0)
    sb.setValue(sb.maximum())
    assert panel._tab_auto_scroll["a"] is True
    more = _MANY_LINES + "".join(f"more {n}\n" for n in range(300))
    panel._handle_run_snapshot({"status": "running", "tasks": [{"name": "a", "output": more}]})
    QApplication.processEvents()
    assert sb.value() > sb.maximum() * 3 // 4


@pytest.mark.parametrize("status", ["success", "failed"])
def test_terminal_snapshot_disconnects_and_aborts_the_run_stream(panel, status):
    reply = MagicMock()
    panel._run_sse_reply = reply
    panel._handle_run_snapshot({"status": status, "tasks": []})
    reply.readyRead.disconnect.assert_called_once_with(panel._on_run_sse_data)
    reply.errorOccurred.disconnect.assert_called_once_with(panel._on_run_sse_error)
    reply.finished.disconnect.assert_called_once_with(panel._on_run_sse_finished)
    reply.abort.assert_called_once()
    assert panel._run_sse_reply is None


def test_terminal_snapshot_aborts_even_when_disconnect_raises(panel):
    reply = MagicMock()
    reply.readyRead.disconnect.side_effect = RuntimeError("not connected")
    panel._run_sse_reply = reply
    panel._handle_run_snapshot({"status": "failed", "tasks": []})
    reply.abort.assert_called_once()
    assert panel._run_sse_reply is None


def test_terminal_snapshot_without_a_stream_is_fine(panel):
    panel._run_sse_reply = None
    panel._handle_run_snapshot({"status": "success", "tasks": []})
    assert panel._run_sse_reply is None


@pytest.mark.parametrize("status", ["running", "queued", ""])
def test_non_terminal_snapshot_keeps_the_run_stream(panel, status):
    reply = MagicMock()
    panel._run_sse_reply = reply
    panel._handle_run_snapshot({"status": status, "tasks": []})
    reply.abort.assert_not_called()
    assert panel._run_sse_reply is reply


# ---------------------------------------------------------------------------
# _on_history_loaded
# ---------------------------------------------------------------------------

def _reply(payload):
    reply = MagicMock()
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    reply.readAll.return_value = raw
    return reply


def _history_rows(panel):
    t = panel._history_table
    return [[t.item(r, c).text() for c in range(t.columnCount())] for r in range(t.rowCount())]


_RECS = [
    {"job_name": "old", "status": "success", "started_at": "2026-01-01T10:00:00",
     "finished_at": "2026-01-01T10:00:05", "trigger": "manual"},
    {"job": "new", "status": "failed", "start_time": "2026-01-02T10:00:00",
     "end_time": "2026-01-02T10:01:00", "trigger": "daily"},
]


@pytest.mark.parametrize("payload", [
    _RECS,
    {"executions": _RECS},
    {"items": _RECS},
])
def test_history_accepts_list_or_wrapped_payload(panel, payload):
    reply = _reply(payload)
    panel._on_history_loaded(reply, "ws")
    reply.deleteLater.assert_called_once()
    assert [r[0] for r in _history_rows(panel)] == ["new", "old"]   # newest first


@pytest.mark.parametrize("payload", [b"not json", b'"a string"', b"42", b"{}"])
def test_history_with_unusable_payload_is_empty(panel, payload):
    reply = _reply(payload)
    panel._on_history_loaded(reply, "ws")
    reply.deleteLater.assert_called_once()
    assert panel._history_table.rowCount() == 0
    assert panel._history_records == []


def test_history_cells(panel):
    panel._on_history_loaded(_reply(_RECS), "ws")
    new, old = _history_rows(panel)
    assert new[:3] == ["new", "daily", "failed"]
    assert new[3] == panel._relative_time("2026-01-02T10:00:00")
    assert new[4] == panel._format_duration("2026-01-02T10:00:00", "2026-01-02T10:01:00", "failed")
    assert old[:3] == ["old", "Manual", "success"]
    assert old[4] == panel._format_duration("2026-01-01T10:00:00", "2026-01-01T10:00:05", "success")


def test_history_defaults_for_missing_fields(panel):
    panel._on_history_loaded(_reply([{}]), "ws")
    [row] = _history_rows(panel)
    assert row[0] == ""
    assert row[1] == "Manual"
    assert row[2] == ""
    assert row[3] == panel._relative_time("")
    assert row[4] == panel._format_duration("", "", "")


def test_history_status_color_and_read_only_cells(panel):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor
    panel._on_history_loaded(_reply(_RECS), "ws")
    t = panel._history_table
    assert t.item(0, 2).foreground().color() == QColor(panel._badge_color("failed"))
    for c in range(t.columnCount()):
        assert not t.item(0, c).flags() & Qt.ItemFlag.ItemIsEditable


def test_history_replaces_previous_rows_and_records(panel):
    panel._on_history_loaded(_reply(_RECS), "ws")
    panel._on_history_loaded(_reply(_RECS[:1]), "ws")
    assert [r[0] for r in _history_rows(panel)] == ["old"]
    assert panel._history_records == _RECS[:1]


@pytest.mark.parametrize(("offset", "n", "prev", "nxt"), [
    (0, 0, False, False),
    (0, 20, False, True),
    (20, 5, True, False),
    (40, 21, True, True),
])
def test_history_pagination_buttons(panel, offset, n, prev, nxt):
    panel._history_offset = offset
    recs = [{"job_name": f"j{i}", "started_at": f"2026-01-01T00:00:{i:02d}"} for i in range(n)]
    panel._on_history_loaded(_reply(recs), "ws")
    assert panel._prev_btn.isEnabled() is prev
    assert panel._next_btn.isEnabled() is nxt
