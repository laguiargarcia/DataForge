"""Offscreen Qt tests for the Deploy panel — D4b."""
import json
import pytest
from unittest.mock import MagicMock

pytest.importorskip("PySide6")


def _get_app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["test", "-platform", "offscreen"])


def _fake_reply(payload, status=200):
    """A MagicMock QNetworkReply: readAll() → JSON bytes, attribute() → status code."""
    reply = MagicMock()
    reply.readAll.return_value = json.dumps(payload).encode()
    reply.attribute.return_value = status
    return reply


def _make_panel(monkeypatch):
    """Construct the Deploy Panel offscreen with network-on-init suppressed."""
    from dataforge.gui.panels import deploy as deploy_module
    _get_app()
    monkeypatch.setattr(deploy_module.Panel, "_load_pipelines", lambda self: None)
    panel = deploy_module.Panel()
    panel._current_workspace = "Dev"
    panel._current_pipeline = "to-prod"
    return panel


_FAKE_PLAN = {
    "source": "Dev",
    "target": "Prod",
    "pipeline": "to-prod",
    "items": [
        {"source_path": "scripts/a.py", "target_path": "scripts/a.py",
         "kind": "script", "action": "ADD", "reason": "new"},
        {"source_path": "scripts/b.py", "target_path": "scripts/b.py",
         "kind": "script", "action": "MODIFY", "reason": "changed"},
        {"source_path": None, "target_path": "scripts/old.py",
         "kind": "script", "action": "DELETE", "reason": "absent from source"},
        {"source_path": "triggers/n.yaml", "target_path": "triggers/n.yaml",
         "kind": "trigger", "action": "PROTECTED", "reason": "active in target"},
        {"source_path": "data/x.duckdb", "target_path": "data/x.duckdb",
         "kind": "other", "action": "SKIP_IGNORED", "reason": ".deployignore"},
    ],
}

_FAKE_ENTRY = {
    "id": "a1b2c3", "ts": "2026-06-03T10:00:00Z",
    "source": "Dev", "target": "Prod", "pipeline": "to-prod",
    "parent": None, "items": [],
}


def test_is_writable_action():
    from dataforge.gui.panels.deploy import is_writable_action
    assert is_writable_action("ADD")
    assert is_writable_action("MODIFY")
    assert is_writable_action("DELETE")
    assert not is_writable_action("PROTECTED")
    assert not is_writable_action("SKIP_IGNORED")
    assert not is_writable_action("nonsense")


def test_action_label_renames_skip_ignored():
    from dataforge.gui.panels.deploy import action_label
    assert action_label("SKIP_IGNORED") == "IGNORED"
    assert action_label("ADD") == "ADD"
    assert action_label("PROTECTED") == "PROTECTED"
    assert action_label("weird") == "weird"  # unknown → passthrough, never crash


def test_group_items_by_action_orders_and_groups():
    from dataforge.gui.panels.deploy import group_items_by_action
    grouped = group_items_by_action(_FAKE_PLAN["items"])
    # Engine order: ADD, MODIFY, DELETE, SKIP_IGNORED, PROTECTED
    assert list(grouped.keys()) == ["ADD", "MODIFY", "DELETE", "SKIP_IGNORED", "PROTECTED"]
    assert [i["target_path"] for i in grouped["ADD"]] == ["scripts/a.py"]
    assert len(grouped["PROTECTED"]) == 1


def test_group_items_empty():
    from dataforge.gui.panels.deploy import group_items_by_action
    assert group_items_by_action([]) == {}


def test_format_provenance():
    from dataforge.gui.panels.deploy import format_provenance
    assert format_provenance(_FAKE_ENTRY) == "Dev → Prod [to-prod] a1b2c3 (2026-06-03T10:00:00Z)"
    # Missing ts → no parens
    assert format_provenance({"source": "A", "target": "B", "pipeline": "p", "id": "x"}) \
        == "A → B [p] x"


def test_deploy_panel_init(monkeypatch):
    panel = _make_panel(monkeypatch)
    assert panel is not None
    assert panel._pipeline_combo.count() == 0
    assert not panel._error_label.isVisible()     # no error on a fresh panel
    assert not panel._rollback_btn.isEnabled()    # nothing to roll back yet
    panel.close()


def test_set_workspace_empty_is_noop(monkeypatch):
    from dataforge.gui.panels import deploy as deploy_module
    _get_app()
    calls = []
    monkeypatch.setattr(deploy_module.Panel, "_load_pipelines", lambda self: calls.append(True))
    panel = deploy_module.Panel()
    panel.set_workspace("")
    assert calls == []          # blank workspace must not fetch
    panel.set_workspace("Dev")
    assert calls == [True]      # real workspace triggers a load
    panel.close()


def test_pipelines_populate_combo(monkeypatch):
    panel = _make_panel(monkeypatch)
    # Selecting a pipeline kicks off plan/status loads — count them to guard against double-fire.
    calls = []
    monkeypatch.setattr(panel, "_load_plan", lambda: calls.append("plan"), raising=False)
    monkeypatch.setattr(panel, "_load_status", lambda: calls.append("status"), raising=False)
    panel._on_pipelines_loaded(_fake_reply(["to-prod", "to-stage"]))
    texts = [panel._pipeline_combo.itemText(i) for i in range(panel._pipeline_combo.count())]
    assert texts == ["to-prod", "to-stage"]
    assert panel._current_pipeline == "to-prod"   # first auto-selected
    assert calls == ["plan", "status"]            # fired exactly once each (no double-fire)
    panel.close()


def test_pipelines_empty_clears(monkeypatch):
    panel = _make_panel(monkeypatch)
    panel._on_pipelines_loaded(_fake_reply([]))
    assert panel._pipeline_combo.count() == 0
    assert "No deploy pipelines" in panel._status_label.text()
    panel.close()


def test_plan_renders_grouped_color_tree(monkeypatch):
    panel = _make_panel(monkeypatch)
    panel._on_plan_loaded(_fake_reply(_FAKE_PLAN))
    assert panel._route_label.text() == "Dev → Prod"
    assert panel._target == "Prod"
    root = panel._tree.invisibleRootItem()
    # One top-level node per non-empty action group, in engine order, IGNORED relabeled.
    headers = [root.child(i).text(0) for i in range(root.childCount())]
    assert headers == ["ADD (1)", "MODIFY (1)", "DELETE (1)", "IGNORED (1)", "PROTECTED (1)"]
    panel.close()


def test_writable_rows_checkable_and_checked(monkeypatch):
    from PySide6.QtCore import Qt
    panel = _make_panel(monkeypatch)
    panel._on_plan_loaded(_fake_reply(_FAKE_PLAN))
    root = panel._tree.invisibleRootItem()
    for i in range(root.childCount()):
        parent = root.child(i)
        action = parent.text(0).split(" ")[0]
        child = parent.child(0)
        checkable = bool(child.flags() & Qt.ItemFlag.ItemIsUserCheckable)
        if action in ("ADD", "MODIFY", "DELETE"):
            assert checkable, f"{action} row must be checkable"
            assert child.checkState(0) == Qt.CheckState.Checked
        else:  # PROTECTED / IGNORED — never selectable (laws #2/#3)
            assert not checkable, f"{action} row must NOT be checkable"
    panel.close()


def test_collect_selected_returns_checked_writable_paths(monkeypatch):
    from PySide6.QtCore import Qt
    panel = _make_panel(monkeypatch)
    panel._on_plan_loaded(_fake_reply(_FAKE_PLAN))
    assert sorted(panel._collect_selected()) == ["scripts/a.py", "scripts/b.py", "scripts/old.py"]
    # Uncheck the ADD row → it drops out
    add_parent = panel._tree.invisibleRootItem().child(0)
    add_parent.child(0).setCheckState(0, Qt.CheckState.Unchecked)
    assert sorted(panel._collect_selected()) == ["scripts/b.py", "scripts/old.py"]
    panel.close()


def test_plan_error_surfaces_and_clears_tree(monkeypatch):
    panel = _make_panel(monkeypatch)
    panel._on_plan_loaded(_fake_reply(_FAKE_PLAN))      # populate first
    assert panel._tree.invisibleRootItem().childCount() == 5
    panel._on_plan_loaded(_fake_reply({"detail": "Pipeline 'bad' is invalid"}, status=400))
    # isVisible() requires the full parent chain to be shown; isHidden() reflects setVisible() state.
    assert not panel._error_label.isHidden()
    assert "invalid" in panel._error_label.text()
    assert panel._tree.invisibleRootItem().childCount() == 0
    panel.close()


def _capture_post(panel):
    """Replace panel._nam.post with a capturing fake; returns the captured dict."""
    captured = {}

    def _fake_post(req, body):
        captured["url"] = req.url().toString()
        captured["body"] = bytes(body).decode()
        reply = MagicMock()
        reply.finished = MagicMock()
        reply.finished.connect = MagicMock()
        return reply

    panel._nam.post = _fake_post
    return captured


def test_deploy_posts_only_checked_writable(monkeypatch):
    panel = _make_panel(monkeypatch)
    panel._on_plan_loaded(_fake_reply(_FAKE_PLAN))
    captured = _capture_post(panel)
    panel._on_deploy()
    assert captured["url"].endswith("/api/workspaces/Dev/deploy/to-prod/apply")
    only = json.loads(captured["body"])["only"]
    assert sorted(only) == ["scripts/a.py", "scripts/b.py", "scripts/old.py"]
    # Law #2/#3: protected/ignored target paths are never in the request
    assert "triggers/n.yaml" not in only
    assert "data/x.duckdb" not in only
    panel.close()


def test_deploy_nothing_selected_no_post(monkeypatch):
    from PySide6.QtCore import Qt
    from dataforge.gui.panels import deploy as deploy_module
    panel = _make_panel(monkeypatch)
    panel._on_plan_loaded(_fake_reply(_FAKE_PLAN))
    # Uncheck every writable row
    root = panel._tree.invisibleRootItem()
    for i in range(root.childCount()):
        parent = root.child(i)
        for j in range(parent.childCount()):
            child = parent.child(j)
            if child.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                child.setCheckState(0, Qt.CheckState.Unchecked)
    posted = []
    panel._nam.post = lambda req, body: posted.append(1)
    monkeypatch.setattr(deploy_module, "QMessageBox", MagicMock())
    panel._on_deploy()
    assert posted == []     # empty selection → no request
    panel.close()


def test_apply_error_surfaces(monkeypatch):
    panel = _make_panel(monkeypatch)
    monkeypatch.setattr(panel, "_load_plan", lambda: None)
    monkeypatch.setattr(panel, "_load_status", lambda: None, raising=False)
    panel._on_apply_finished(_fake_reply({"detail": "boom"}, status=500))
    assert panel._error_label.isVisible() or not panel._error_label.isHidden()
    assert "boom" in panel._error_label.text()
    panel.close()


def test_apply_refreshes_on_success(monkeypatch):
    panel = _make_panel(monkeypatch)
    calls = []
    monkeypatch.setattr(panel, "_load_plan", lambda: calls.append("plan"))
    monkeypatch.setattr(panel, "_load_status", lambda: calls.append("status"), raising=False)
    panel._on_apply_finished(_fake_reply({"applied": True, "entry": _FAKE_ENTRY}))
    assert "plan" in calls   # refresh after deploy
    assert "status" in calls
    panel.close()


def test_apply_not_applied_shows_info_and_still_refreshes(monkeypatch):
    from dataforge.gui.panels import deploy as deploy_module
    panel = _make_panel(monkeypatch)
    calls = []
    monkeypatch.setattr(panel, "_load_plan", lambda: calls.append("plan"))
    monkeypatch.setattr(panel, "_load_status", lambda: calls.append("status"), raising=False)
    mock_box = MagicMock()
    monkeypatch.setattr(deploy_module, "QMessageBox", mock_box)
    panel._on_apply_finished(_fake_reply({"applied": False, "entry": None}))
    mock_box.information.assert_called_once()      # user told nothing was deployed
    assert calls == ["plan", "status"]             # still re-syncs with server truth
    panel.close()


def test_status_sets_last_entry_and_enables_rollback(monkeypatch):
    panel = _make_panel(monkeypatch)
    panel._on_status_loaded(_fake_reply({"last": _FAKE_ENTRY, "pending": 2}))
    assert panel._last_entry == _FAKE_ENTRY
    assert panel._rollback_btn.isEnabled()
    assert "2 pending" in panel._status_label.text()
    assert "Dev → Prod [to-prod] a1b2c3" in panel._status_label.text()
    panel.close()


def test_status_no_history_disables_rollback(monkeypatch):
    panel = _make_panel(monkeypatch)
    panel._rollback_btn.setEnabled(True)
    panel._on_status_loaded(_fake_reply({"last": None, "pending": 1}))
    assert panel._last_entry is None
    assert not panel._rollback_btn.isEnabled()
    assert "no deploy history" in panel._status_label.text()
    panel.close()


def test_rollback_shows_provenance_then_posts_on_confirm(monkeypatch):
    from dataforge.gui.panels import deploy as deploy_module
    from PySide6.QtWidgets import QMessageBox
    panel = _make_panel(monkeypatch)
    panel._last_entry = _FAKE_ENTRY
    captured = {}

    def _fake_question(parent, title, text, *args, **kwargs):
        captured["text"] = text
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(deploy_module.QMessageBox, "question", staticmethod(_fake_question))

    posted = {}

    def _fake_post(req, body):
        posted["url"] = req.url().toString()
        reply = MagicMock()
        reply.finished = MagicMock()
        reply.finished.connect = MagicMock()
        return reply

    panel._nam.post = _fake_post
    panel._on_rollback()
    # D19: provenance shown BEFORE the request
    assert "Dev → Prod [to-prod] a1b2c3" in captured["text"]
    assert posted["url"].endswith("/api/workspaces/Dev/deploy/to-prod/rollback")
    panel.close()


def test_rollback_declined_no_post(monkeypatch):
    from dataforge.gui.panels import deploy as deploy_module
    from PySide6.QtWidgets import QMessageBox
    panel = _make_panel(monkeypatch)
    panel._last_entry = _FAKE_ENTRY
    monkeypatch.setattr(
        deploy_module.QMessageBox, "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No),
    )
    posted = []
    panel._nam.post = lambda req, body: posted.append(1)
    panel._on_rollback()
    assert posted == []        # declined → nothing sent
    panel.close()


def test_rollback_no_history_no_post(monkeypatch):
    from dataforge.gui.panels import deploy as deploy_module
    panel = _make_panel(monkeypatch)
    panel._last_entry = None
    monkeypatch.setattr(deploy_module, "QMessageBox", MagicMock())
    posted = []
    panel._nam.post = lambda req, body: posted.append(1)
    panel._on_rollback()
    assert posted == []
    panel.close()


def test_rollback_409_is_informational_not_error(monkeypatch):
    from dataforge.gui.panels import deploy as deploy_module
    panel = _make_panel(monkeypatch)
    monkeypatch.setattr(panel, "_load_status", lambda: None)
    monkeypatch.setattr(deploy_module, "QMessageBox", MagicMock())
    panel._on_rollback_finished(_fake_reply({"detail": "No deploy to roll back"}, status=409))
    assert not panel._error_label.isVisible()   # 409 is a benign "nothing to undo"
    panel.close()


def test_successful_plan_load_clears_stale_error(monkeypatch):
    panel = _make_panel(monkeypatch)
    # Simulate a prior error (e.g. from a failed apply)
    panel._show_error(500, '{"detail": "boom"}')
    assert not panel._error_label.isHidden()
    # A subsequent successful plan load must clear it
    panel._on_plan_loaded(_fake_reply(_FAKE_PLAN))
    assert panel._error_label.isHidden()
    panel.close()


def test_status_non_dict_last_does_not_crash(monkeypatch):
    panel = _make_panel(monkeypatch)
    # A non-conforming daemon sends `last` as a string instead of an object.
    panel._on_status_loaded(_fake_reply({"last": "oops", "pending": 1}))
    assert panel._last_entry is None          # coerced, not poisoned
    assert not panel._rollback_btn.isEnabled()
    panel.close()


def test_status_list_body_does_not_crash(monkeypatch):
    panel = _make_panel(monkeypatch)
    panel._on_status_loaded(_fake_reply(["unexpected", "list"]))
    assert panel._last_entry is None
    assert not panel._rollback_btn.isEnabled()
    panel.close()


def test_plan_list_body_does_not_crash(monkeypatch):
    panel = _make_panel(monkeypatch)
    panel._on_plan_loaded(_fake_reply(["unexpected", "list"]))   # top-level non-dict
    assert panel._tree.invisibleRootItem().childCount() == 0     # nothing rendered, no crash
    panel.close()


def test_rollback_safe_after_poisoned_status(monkeypatch):
    from dataforge.gui.panels import deploy as deploy_module
    panel = _make_panel(monkeypatch)
    panel._on_status_loaded(_fake_reply({"last": "oops", "pending": 1}))
    monkeypatch.setattr(deploy_module, "QMessageBox", MagicMock())
    posted = []
    panel._nam.post = lambda req, body: posted.append(1)
    panel._on_rollback()            # must not raise; _last_entry is None → info + no post
    assert posted == []
    panel.close()


def test_dropped_panel_is_freed(monkeypatch):
    # A panel kept alive by its own signal connections survives to interpreter exit, where
    # PySide6 6.12 destroys leftover widgets and crashes.
    import gc
    import weakref
    panel = _make_panel(monkeypatch)
    ref = weakref.ref(panel)
    panel.close()
    del panel
    gc.collect()
    assert ref() is None


def test_buttons_call_deploy_and_rollback(monkeypatch):
    from dataforge.gui.panels import deploy as deploy_module
    calls = []
    monkeypatch.setattr(deploy_module.Panel, "_on_deploy", lambda self: calls.append("deploy"))
    monkeypatch.setattr(deploy_module.Panel, "_on_rollback", lambda self: calls.append("rollback"))
    panel = _make_panel(monkeypatch)
    panel._rollback_btn.setEnabled(True)
    panel._deploy_btn.click()
    panel._rollback_btn.click()
    assert calls == ["deploy", "rollback"]
    panel.close()
