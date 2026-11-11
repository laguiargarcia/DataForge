"""Offscreen Qt tests for the scheduler panel — Phase 1.5."""
import json
import pytest
from unittest.mock import MagicMock, patch

pytest.importorskip("PySide6")


_FAKE_TRIGGERS = [
    {
        "name": "daily-at-6am",
        "cron": "0 6 * * *",
        "enabled": True,
        "jobs": [{"workspace": "test_ws", "job": "finance_daily", "params": {}}],
    },
    {
        "name": "weekly-monday",
        "cron": "30 8 * * 1",
        "enabled": False,
        "jobs": [],
    },
]


def _get_app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["test", "-platform", "offscreen"])


# ---------------------------------------------------------------------------
# Scaffold tests (from Plan 04 — must still pass)
# ---------------------------------------------------------------------------

def test_scheduler_panel_init(monkeypatch):
    """Scheduler Panel constructs offscreen without error."""
    app = _get_app()
    from dataforge.gui.panels import scheduler as scheduler_module
    monkeypatch.setattr(scheduler_module.Panel, "_load_triggers", lambda self: None)

    panel = scheduler_module.Panel()
    assert panel is not None
    panel.close()


def test_scheduler_set_workspace_empty(monkeypatch):
    """set_workspace('') returns without raising and without triggering a load."""
    app = _get_app()
    from dataforge.gui.panels import scheduler as scheduler_module
    monkeypatch.setattr(scheduler_module.Panel, "_load_triggers", lambda self: None)

    panel = scheduler_module.Panel()
    load_called = []

    def _track_load(self):
        load_called.append(True)

    monkeypatch.setattr(scheduler_module.Panel, "_load_triggers", _track_load)

    # Should return without calling _load_triggers
    panel.set_workspace("")
    assert load_called == []
    panel.close()


def test_jobs_panel_set_workspace_exists():
    """jobs.Panel has a callable set_workspace attribute."""
    from dataforge.gui.panels import jobs as jobs_module
    assert hasattr(jobs_module.Panel, "set_workspace")
    assert callable(jobs_module.Panel.set_workspace)


# ---------------------------------------------------------------------------
# Task 2: CRUD flow smoke tests
# ---------------------------------------------------------------------------

def _make_panel_with_triggers(monkeypatch, triggers=None):
    """Construct Panel with _load_triggers replaced by feeding canned data."""
    from dataforge.gui.panels import scheduler as scheduler_module
    app = _get_app()
    panel = scheduler_module.Panel.__new__(scheduler_module.Panel)
    monkeypatch.setattr(scheduler_module.Panel, "_load_triggers", lambda self: None)
    scheduler_module.Panel.__init__(panel)
    panel._current_workspace = "test_ws"

    # Directly call _on_triggers_loaded logic by feeding data
    if triggers is not None:
        panel._trigger_data = triggers
        panel._trigger_table.setRowCount(0)
        for i, trigger in enumerate(triggers):
            panel._trigger_table.insertRow(i)
            from dataforge.gui.panels.scheduler import next_run
            cron_expr = trigger.get("cron", "")
            nxt = next_run(cron_expr)
            nxt_str = nxt.strftime("%Y-%m-%d %H:%M UTC") if nxt else "—"
            from PySide6.QtWidgets import QTableWidgetItem
            items = [
                QTableWidgetItem(trigger.get("name", "")),
                QTableWidgetItem(cron_expr),
                QTableWidgetItem("Yes" if trigger.get("enabled", True) else "No"),
                QTableWidgetItem(trigger.get("last_run", "—") or "—"),
                QTableWidgetItem(nxt_str),
            ]
            for col, item in enumerate(items):
                panel._trigger_table.setItem(i, col, item)

    return panel


def test_trigger_list_populated(monkeypatch):
    """2-trigger JSON populates 2 table rows with name/cron/enabled/next-run cells."""
    panel = _make_panel_with_triggers(monkeypatch, triggers=_FAKE_TRIGGERS)

    assert panel._trigger_table.rowCount() == 2

    # Row 0: daily-at-6am
    assert panel._trigger_table.item(0, 0).text() == "daily-at-6am"
    assert panel._trigger_table.item(0, 1).text() == "0 6 * * *"
    assert panel._trigger_table.item(0, 2).text() == "Yes"
    # Next Run should be a real datetime string (not "—") for valid cron
    next_run_text = panel._trigger_table.item(0, 4).text()
    assert next_run_text != "—", f"Expected a next-run datetime, got: {next_run_text!r}"

    # Row 1: weekly-monday (disabled)
    assert panel._trigger_table.item(1, 0).text() == "weekly-monday"
    assert panel._trigger_table.item(1, 2).text() == "No"

    panel.close()


def test_row_click_opens_detail(monkeypatch):
    """cellClicked(row) switches stack to PAGE_DETAIL showing that trigger's data."""
    panel = _make_panel_with_triggers(monkeypatch, triggers=_FAKE_TRIGGERS)

    assert panel._stack.currentIndex() == panel.PAGE_LIST

    # Simulate clicking row 0
    panel._on_trigger_clicked(0, 0)

    assert panel._stack.currentIndex() == panel.PAGE_DETAIL
    # Name should be populated (read-only in edit mode)
    assert panel._name_edit.text() == "daily-at-6am"
    assert panel._name_edit.isReadOnly()
    # Enabled checkbox
    assert panel._enabled_check.isChecked() is True

    panel.close()


def test_new_trigger_button_opens_form(monkeypatch):
    """Clicking New Trigger switches to PAGE_DETAIL with editable name field."""
    panel = _make_panel_with_triggers(monkeypatch, triggers=[])

    assert panel._stack.currentIndex() == panel.PAGE_LIST

    panel._open_create_form()

    assert panel._stack.currentIndex() == panel.PAGE_DETAIL
    assert panel._name_edit.text() == ""
    assert not panel._name_edit.isReadOnly(), "Name field must be editable in create mode"
    # Delete button hidden in create mode
    assert not panel._delete_btn.isVisible()

    panel.close()


def test_create_trigger_posts(monkeypatch):
    """Save in create mode issues POST to /api/workspaces/{ws}/triggers."""
    from unittest.mock import MagicMock

    panel = _make_panel_with_triggers(monkeypatch, triggers=[])
    panel._open_create_form()

    # Fill in form
    panel._name_edit.setText("new-trigger")
    panel._enabled_check.setChecked(True)

    captured = {}

    def _fake_post(req, body):
        captured["url"] = req.url().toString()
        captured["verb"] = "POST"
        captured["body"] = bytes(body).decode()
        mock_reply = MagicMock()
        mock_reply.finished = MagicMock()
        mock_reply.finished.connect = MagicMock()
        return mock_reply

    panel._nam.post = _fake_post

    panel._on_save()

    assert captured.get("verb") == "POST"
    assert "/api/workspaces/test_ws/triggers" in captured.get("url", "")
    # Body must not use /triggers/{name} path (that is PUT)
    assert captured["url"].rstrip("/").endswith("/triggers")
    # Body contains the trigger name
    body_data = json.loads(captured["body"])
    assert body_data["name"] == "new-trigger"

    panel.close()


# ---------------------------------------------------------------------------
# Plan 01.5-06: cascading dropdowns + dialog + row-delete tests
# ---------------------------------------------------------------------------

_FAKE_WORKSPACES = [{"name": "ws-a", "jobs": []}, {"name": "ws-b", "jobs": []}]
_FAKE_JOBS_WS_A = [
    {"name": "job1", "parameters": []},
    {"name": "job2", "parameters": [{"name": "start_date", "type": "date", "default": "2026-01-01", "description": None}]},
]


def _make_bare_panel(monkeypatch):
    """Construct Panel with _load_triggers suppressed."""
    from dataforge.gui.panels import scheduler as scheduler_module
    _get_app()
    monkeypatch.setattr(scheduler_module.Panel, "_load_triggers", lambda self: None)
    panel = scheduler_module.Panel()
    panel._current_workspace = "ws-a"
    return panel


def test_workspace_combo_populates_from_api(monkeypatch):
    """col-0 workspace combo is populated from _fetch_workspaces callback."""
    panel = _make_bare_panel(monkeypatch)

    # Patch _fetch_workspaces to call callback immediately with fake data
    def _fake_fetch_ws(callback):
        callback(_FAKE_WORKSPACES)

    monkeypatch.setattr(panel, "_fetch_workspaces", _fake_fetch_ws)
    # Also patch _fetch_jobs_for_ws to avoid real HTTP
    monkeypatch.setattr(panel, "_fetch_jobs_for_ws", lambda ws, cb: cb([]))

    panel._add_job_row()

    combo_ws = panel._jobs_table.cellWidget(0, 0)
    assert combo_ws is not None
    assert combo_ws.count() == 2
    texts = [combo_ws.itemText(i) for i in range(combo_ws.count())]
    assert "ws-a" in texts
    assert "ws-b" in texts
    panel.close()


def test_workspace_selection_cascades_to_job_combo(monkeypatch):
    """Changing col-0 workspace triggers repopulation of col-1 job combo."""
    panel = _make_bare_panel(monkeypatch)

    def _fake_fetch_ws(callback):
        callback(_FAKE_WORKSPACES)

    def _fake_fetch_jobs(ws, callback):
        if ws == "ws-a":
            callback(_FAKE_JOBS_WS_A)
        else:
            callback([])

    monkeypatch.setattr(panel, "_fetch_workspaces", _fake_fetch_ws)
    monkeypatch.setattr(panel, "_fetch_jobs_for_ws", _fake_fetch_jobs)

    panel._append_job_row("ws-a", "job1", {})

    combo_job = panel._jobs_table.cellWidget(0, 1)
    assert combo_job is not None
    assert combo_job.count() == 2

    # itemData stores declared parameters list
    assert combo_job.itemData(0) == []  # job1 has no parameters
    assert combo_job.itemData(1) == _FAKE_JOBS_WS_A[1]["parameters"]  # job2 has one

    panel.close()


def test_configure_button_opens_dialog_with_declared_params(monkeypatch):
    """Clicking Configure button opens JobParamsDialog; OK updates button label and property."""
    from dataforge.gui.panels import scheduler as scheduler_module
    from PySide6.QtWidgets import QDialog

    panel = _make_bare_panel(monkeypatch)

    def _fake_fetch_ws(callback):
        callback(_FAKE_WORKSPACES)

    def _fake_fetch_jobs(ws, callback):
        callback(_FAKE_JOBS_WS_A)

    monkeypatch.setattr(panel, "_fetch_workspaces", _fake_fetch_ws)
    monkeypatch.setattr(panel, "_fetch_jobs_for_ws", _fake_fetch_jobs)

    panel._append_job_row("ws-a", "job2", {})

    # Select job2 (index 1, which has parameters)
    combo_job = panel._jobs_table.cellWidget(0, 1)
    combo_job.setCurrentIndex(1)

    # Mock JobParamsDialog.exec to return Accepted and result_params to return a value
    monkeypatch.setattr(
        scheduler_module.JobParamsDialog, "exec",
        lambda self: QDialog.DialogCode.Accepted,
    )
    monkeypatch.setattr(
        scheduler_module.JobParamsDialog, "result_params",
        lambda self: {"start_date": "2026-02-01"},
    )

    btn_cfg = panel._jobs_table.cellWidget(0, 2)
    btn_cfg.click()

    assert btn_cfg.property("params") == {"start_date": "2026-02-01"}
    assert "1 params" in btn_cfg.text()
    panel.close()


def test_dialog_values_roundtrip_into_payload(monkeypatch):
    """Configured params round-trip into _build_trigger_payload jobs list."""

    panel = _make_bare_panel(monkeypatch)
    panel._name_edit.setText("test-trigger")
    panel._enabled_check.setChecked(True)

    def _fake_fetch_ws(callback):
        callback(_FAKE_WORKSPACES)

    def _fake_fetch_jobs(ws, callback):
        callback(_FAKE_JOBS_WS_A)

    monkeypatch.setattr(panel, "_fetch_workspaces", _fake_fetch_ws)
    monkeypatch.setattr(panel, "_fetch_jobs_for_ws", _fake_fetch_jobs)

    panel._append_job_row("ws-a", "job1", {"start_date": "2026-02-01"})

    payload = panel._build_trigger_payload()
    assert len(payload["jobs"]) == 1
    job_entry = payload["jobs"][0]
    assert job_entry["workspace"] == "ws-a"
    assert job_entry["job"] == "job1"
    assert job_entry["params"] == {"start_date": "2026-02-01"}
    panel.close()


def test_remove_row_button_deletes_row(monkeypatch):
    """Clicking col-3 ✕ on first row removes it; second row remains with correct values."""
    panel = _make_bare_panel(monkeypatch)

    def _fake_fetch_ws(callback):
        callback(_FAKE_WORKSPACES)

    def _fake_fetch_jobs(ws, callback):
        callback(_FAKE_JOBS_WS_A)

    monkeypatch.setattr(panel, "_fetch_workspaces", _fake_fetch_ws)
    monkeypatch.setattr(panel, "_fetch_jobs_for_ws", _fake_fetch_jobs)

    # Add two rows with different workspaces
    panel._append_job_row("ws-a", "job1", {})
    panel._append_job_row("ws-b", "job2", {})

    assert panel._jobs_table.rowCount() == 2

    # Click ✕ on first row
    btn_rm = panel._jobs_table.cellWidget(0, 3)
    btn_rm.click()

    assert panel._jobs_table.rowCount() == 1

    # Remaining row should be row that was originally at index 1 (ws-b/job2)
    remaining_ws_combo = panel._jobs_table.cellWidget(0, 0)
    assert remaining_ws_combo is not None
    # ws-b should be selected in remaining row
    assert remaining_ws_combo.currentText() == "ws-b"
    panel.close()


def test_delete_trigger_calls_api(monkeypatch):
    """Delete action issues DELETE to /api/workspaces/{ws}/triggers/{name} then reloads."""
    from unittest.mock import MagicMock

    panel = _make_panel_with_triggers(monkeypatch, triggers=_FAKE_TRIGGERS)

    # Open detail for first trigger
    panel._on_trigger_clicked(0, 0)
    assert panel._detail_trigger_name == "daily-at-6am"

    captured = {}

    def _fake_delete(req):
        captured["url"] = req.url().toString()
        captured["verb"] = "DELETE"
        mock_reply = MagicMock()
        mock_reply.finished = MagicMock()
        mock_reply.finished.connect = MagicMock()
        return mock_reply

    panel._nam.deleteResource = _fake_delete

    panel._on_delete()

    assert captured.get("verb") == "DELETE"
    assert "/api/workspaces/test_ws/triggers/daily-at-6am" in captured.get("url", ""), (
        f"Expected DELETE URL with trigger name, got: {captured.get('url')}"
    )

    panel.close()


# ---------------------------------------------------------------------------
# df-413: save response 'warnings' are surfaced to the user
# ---------------------------------------------------------------------------

def _fake_save_reply(status, body):
    reply = MagicMock()
    reply.attribute.return_value = status
    reply.readAll.return_value = json.dumps(body).encode()
    return reply


def test_save_finished_shows_history_warning(monkeypatch):
    """A 'warnings' entry in a successful save response is shown via QMessageBox."""
    from dataforge.gui.panels import scheduler as scheduler_module

    panel = _make_bare_panel(monkeypatch)
    monkeypatch.setattr(scheduler_module.Panel, "_go_back", lambda self: None)

    reply = _fake_save_reply(201, {"name": "t", "cron": "0 6 * * *",
                                    "warnings": ["ENABLE-HISTORY-CMD"]})

    with patch.object(scheduler_module, "QMessageBox") as mock_msgbox:
        panel._on_save_finished(reply)

    shown = " ".join(str(c) for c in mock_msgbox.mock_calls)
    assert "ENABLE-HISTORY-CMD" in shown
    panel.close()


def test_save_finished_no_warning_no_dialog(monkeypatch):
    """A clean save response shows no warning dialog."""
    from dataforge.gui.panels import scheduler as scheduler_module

    panel = _make_bare_panel(monkeypatch)
    monkeypatch.setattr(scheduler_module.Panel, "_go_back", lambda self: None)

    reply = _fake_save_reply(201, {"name": "t", "cron": "0 6 * * *"})

    with patch.object(scheduler_module, "QMessageBox") as mock_msgbox:
        panel._on_save_finished(reply)

    mock_msgbox.warning.assert_not_called()
    mock_msgbox.information.assert_not_called()
    panel.close()
