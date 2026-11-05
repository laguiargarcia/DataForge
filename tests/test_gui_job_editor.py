"""Offscreen Qt tests for the job editor panel — Phase C (Tasks 11-14)."""
import json
import pytest
from unittest.mock import MagicMock, patch

pytest.importorskip("PySide6")


def _get_app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["test", "-platform", "offscreen"])


# ---------------------------------------------------------------------------
# Task 11: skeleton — construct, create/edit mode, D-PRESERVE stash
# ---------------------------------------------------------------------------

def test_job_editor_constructs():
    _get_app()
    from dataforge.gui.panels.job_editor import JobEditorPanel
    panel = JobEditorPanel()
    assert panel is not None
    panel.close()


def test_create_mode_name_editable():
    _get_app()
    from dataforge.gui.panels.job_editor import JobEditorPanel
    panel = JobEditorPanel()
    panel.load_job_data("test_ws", None)
    assert panel._name_edit.text() == ""
    assert not panel._name_edit.isReadOnly()
    assert not panel._delete_btn.isVisible()
    panel.close()


def test_edit_mode_name_readonly():
    """D1: name is immutable when editing an existing job.
    Fix #1: this job HAS parameters AND schedule (the unshown fields) so the test exercises
    the D-PRESERVE stash path, not just parameter-less jobs."""
    _get_app()
    from dataforge.gui.panels.job_editor import JobEditorPanel
    panel = JobEditorPanel()
    panel.show()  # offscreen: isVisible() reflects setVisible only once the top-level is shown
    panel.load_job_data("test_ws", {
        "name": "etl",
        "env_file": None, "schedule": "0 2 * * *",
        "retry": {"attempts": 3, "delay_seconds": 30},
        "tasks": [{"name": "t1", "type": "python", "script": "a.py",
                   "job": None, "params": {}, "depends_on": [], "match": "all",
                   "retry": {"attempts": 3, "delay_seconds": 30}, "active": True}],
        "parameters": [{"name": "env", "type": "string", "default": "dev", "description": None}],
    })
    assert panel._name_edit.text() == "etl"
    assert panel._name_edit.isReadOnly()
    assert panel._delete_btn.isVisible()
    assert panel._task_list.count() == 1
    # D-SCHEDULE: no schedule edit field exists on the form
    assert not hasattr(panel, "_schedule_edit")
    panel.close()


def test_build_payload_preserves_unshown_fields():
    """Fix #1 / D-PRESERVE: load a job with parameters + schedule, edit only a task field,
    build the payload — the unshown parameters[] and schedule survive VERBATIM."""
    _get_app()
    from dataforge.gui.panels.job_editor import JobEditorPanel
    panel = JobEditorPanel()
    params = [{"name": "env", "type": "string", "default": "dev", "description": None}]
    panel.load_job_data("test_ws", {
        "name": "etl", "env_file": None, "schedule": "0 2 * * *",
        "retry": {"attempts": 3, "delay_seconds": 30},
        "tasks": [{"name": "t1", "type": "python", "script": "a.py", "job": None,
                   "params": {}, "depends_on": [], "match": "all",
                   "retry": {"attempts": 3, "delay_seconds": 30}, "active": True}],
        "parameters": params,
    })
    payload = panel._build_job_payload()
    assert payload["schedule"] == "0 2 * * *"      # preserved, never wiped
    assert payload["parameters"] == params          # preserved verbatim
    panel.close()


# ---------------------------------------------------------------------------
# Task 12: per-task detail form + add/remove/reorder + typed-params round-trip
# ---------------------------------------------------------------------------

def test_build_payload_roundtrips_tasks():
    _get_app()
    from dataforge.gui.panels.job_editor import JobEditorPanel
    panel = JobEditorPanel()
    panel.load_job_data("test_ws", {
        "name": "etl", "env_file": None, "schedule": None,
        "retry": {"attempts": 2, "delay_seconds": 5},
        "tasks": [
            {"name": "a", "type": "python", "script": "a.py", "job": None,
             "params": {}, "depends_on": [], "match": "all",
             "retry": {"attempts": 3, "delay_seconds": 30}, "active": True},
            {"name": "b", "type": "python", "script": "b.py", "job": None,
             "params": {}, "depends_on": [{"task": "a", "when": ["succeeded"]}],
             "match": "all", "retry": {"attempts": 3, "delay_seconds": 30}, "active": True},
        ],
        "parameters": [],
    })
    payload = panel._build_job_payload()
    assert payload["name"] == "etl"
    assert payload["retry"]["attempts"] == 2
    assert [t["name"] for t in payload["tasks"]] == ["a", "b"]
    # depends_on preserved (b depends on a)
    b = next(t for t in payload["tasks"] if t["name"] == "b")
    assert b["depends_on"] and b["depends_on"][0]["task"] == "a"
    panel.close()


def test_params_typed_values_survive_roundtrip():
    """Fix #2: task params keep int/bool/list types through the grid — NOT stringified.
    Load a task with typed params, render into the grid, read back via the build payload."""
    _get_app()
    from dataforge.gui.panels.job_editor import JobEditorPanel
    panel = JobEditorPanel()
    panel.load_job_data("test_ws", {
        "name": "etl", "env_file": None, "schedule": None,
        "retry": {"attempts": 3, "delay_seconds": 30},
        "tasks": [{"name": "a", "type": "python", "script": "a.py", "job": None,
                   "params": {"retries": 5, "debug": True, "cols": ["a", "b"]},
                   "depends_on": [], "match": "all",
                   "retry": {"attempts": 3, "delay_seconds": 30}, "active": True}],
        "parameters": [],
    })
    # select the task so its detail grid is populated, then build (write-back must keep types)
    panel._task_list.setCurrentRow(0)
    payload = panel._build_job_payload()
    p = payload["tasks"][0]["params"]
    assert p["retries"] == 5 and isinstance(p["retries"], int)
    assert p["debug"] is True
    assert p["cols"] == ["a", "b"]
    panel.close()


def test_add_and_reorder_tasks():
    _get_app()
    from dataforge.gui.panels.job_editor import JobEditorPanel
    panel = JobEditorPanel()
    panel.load_job_data("test_ws", None)
    panel._add_task()
    panel._add_task()
    assert panel._task_list.count() == 2
    # rename via internal state, then move second up
    panel._tasks[0]["name"] = "first"
    panel._tasks[1]["name"] = "second"
    panel._refresh_task_list()
    panel._task_list.setCurrentRow(1)
    panel._move_task_up()
    assert [panel._task_list.item(i).text() for i in range(2)] == ["second", "first"]
    panel.close()


def test_empty_when_rejected_in_payload():
    """Fix #10: a depends_on edge with an EMPTY when must be rejected (it would silently
    revert to the model default [succeeded]). The payload builder must raise / surface error."""
    _get_app()
    from dataforge.gui.panels.job_editor import JobEditorPanel
    panel = JobEditorPanel()
    panel.load_job_data("test_ws", None)
    panel._add_task()
    panel._add_task()
    panel._tasks[0]["name"] = "a"
    panel._tasks[1]["name"] = "b"
    # detach the detail form so write-back can't clobber the crafted edge below
    panel._current_task_row = -1
    # craft an edge with an empty when — disallowed
    panel._tasks[1]["depends_on"] = [{"task": "a", "when": []}]
    with pytest.raises(ValueError):
        panel._build_job_payload()
    panel.close()


# ---------------------------------------------------------------------------
# Task 13: async Save (POST/PUT) + error/warning + busy guard
# ---------------------------------------------------------------------------

def test_create_mode_posts(monkeypatch):
    _get_app()
    from dataforge.gui.panels.job_editor import JobEditorPanel
    panel = JobEditorPanel()
    panel.load_job_data("test_ws", None)
    panel._name_edit.setText("newjob")

    captured = {}

    def _fake_post(req, body):
        captured["url"] = req.url().toString()
        captured["verb"] = "POST"
        captured["body"] = bytes(body).decode()
        r = MagicMock(); r.finished = MagicMock(); r.finished.connect = MagicMock()
        return r

    panel._nam.post = _fake_post
    panel._on_save()
    assert captured["verb"] == "POST"
    assert captured["url"].rstrip("/").endswith("/test_ws/jobs")
    assert json.loads(captured["body"])["name"] == "newjob"
    panel.close()


def test_edit_mode_puts(monkeypatch):
    _get_app()
    from dataforge.gui.panels.job_editor import JobEditorPanel
    panel = JobEditorPanel()
    panel.load_job_data("test_ws", {"name": "etl", "env_file": None, "schedule": None,
                                    "retry": {"attempts": 3, "delay_seconds": 30},
                                    "tasks": [], "parameters": []})
    captured = {}

    def _fake_put(req, body):
        captured["url"] = req.url().toString(); captured["verb"] = "PUT"
        r = MagicMock(); r.finished = MagicMock(); r.finished.connect = MagicMock()
        return r

    panel._nam.put = _fake_put
    panel._on_save()
    assert captured["verb"] == "PUT"
    assert "/test_ws/jobs/etl" in captured["url"]
    panel.close()


def test_save_finished_surfaces_error():
    _get_app()
    from dataforge.gui.panels.job_editor import JobEditorPanel
    panel = JobEditorPanel()
    panel.show()  # offscreen: isVisible() needs the top-level shown
    panel.load_job_data("test_ws", None)
    reply = MagicMock()
    reply.attribute.return_value = 400
    reply.readAll.return_value = json.dumps({"detail": "cycle detected"}).encode()
    panel._on_save_finished(reply)
    assert panel._error_label.isVisible()
    assert "cycle detected" in panel._error_label.text()
    panel.close()


def test_save_finished_shows_warning():
    _get_app()
    from dataforge.gui.panels.job_editor import JobEditorPanel
    from dataforge.gui.panels import job_editor as je
    panel = JobEditorPanel()
    panel.load_job_data("test_ws", None)
    panel.set_on_saved(lambda: None)
    reply = MagicMock()
    reply.attribute.return_value = 201
    reply.readAll.return_value = json.dumps(
        {"name": "j", "warnings": ["script missing"]}).encode()
    with patch.object(je, "QMessageBox") as mb:
        panel._on_save_finished(reply)
    shown = " ".join(str(c) for c in mb.mock_calls)
    assert "script missing" in shown
    panel.close()


def test_save_disables_buttons_while_in_flight():
    """Fix #11: a second _on_save while the first is pending is a no-op (double-click guard)."""
    _get_app()
    from dataforge.gui.panels.job_editor import JobEditorPanel
    panel = JobEditorPanel()
    panel.load_job_data("test_ws", None)
    panel._name_edit.setText("newjob")

    calls = []

    def _fake_post(req, body):
        calls.append(1)
        r = MagicMock(); r.finished = MagicMock(); r.finished.connect = MagicMock()
        return r

    panel._nam.post = _fake_post
    panel._on_save()
    assert not panel._save_btn.isEnabled()   # busy: disabled
    panel._on_save()                          # second click ignored
    assert len(calls) == 1
    panel.close()


# ---------------------------------------------------------------------------
# Task 14: delete-with-references confirm (D2)
# ---------------------------------------------------------------------------

def test_delete_with_references_confirms_then_deletes(monkeypatch):
    _get_app()
    from dataforge.gui.panels.job_editor import JobEditorPanel
    from dataforge.gui.panels import job_editor as je
    from PySide6.QtWidgets import QMessageBox

    panel = JobEditorPanel()
    panel.load_job_data("test_ws", {"name": "target", "env_file": None, "schedule": None,
                                    "retry": {"attempts": 3, "delay_seconds": 30},
                                    "tasks": [], "parameters": []})

    refs = {"triggers": ["nightly"], "tasks": ["caller.c1"], "schedule": True}
    # User confirms the dialog
    monkeypatch.setattr(je.QMessageBox, "question",
                        lambda *a, **k: QMessageBox.StandardButton.Yes)

    captured = {}

    def _fake_delete(req):
        captured["url"] = req.url().toString(); captured["verb"] = "DELETE"
        r = MagicMock(); r.finished = MagicMock(); r.finished.connect = MagicMock()
        return r

    panel._nam.deleteResource = _fake_delete
    panel._confirm_and_delete(refs)

    assert captured["verb"] == "DELETE"
    assert "/test_ws/jobs/target" in captured["url"]
    panel.close()


def test_delete_cancelled_does_not_call_api(monkeypatch):
    _get_app()
    from dataforge.gui.panels.job_editor import JobEditorPanel
    from dataforge.gui.panels import job_editor as je
    from PySide6.QtWidgets import QMessageBox

    panel = JobEditorPanel()
    panel.load_job_data("test_ws", {"name": "target", "env_file": None, "schedule": None,
                                    "retry": {"attempts": 3, "delay_seconds": 30},
                                    "tasks": [], "parameters": []})
    refs = {"triggers": ["nightly"], "tasks": [], "schedule": False}
    monkeypatch.setattr(je.QMessageBox, "question",
                        lambda *a, **k: QMessageBox.StandardButton.No)

    called = []
    panel._nam.deleteResource = lambda req: called.append(req)
    panel._confirm_and_delete(refs)
    assert called == []
    panel.close()


# ---------------------------------------------------------------------------
# UX enhancement: searchable type-aware Script/Job pickers + sectioned layout
# ---------------------------------------------------------------------------

def _make_reply(status, body_bytes):
    """A MagicMock QNetworkReply whose finished.connect captures the slot for manual firing."""
    reply = MagicMock()
    reply.attribute.return_value = status
    reply.readAll.return_value = body_bytes
    slots = []
    reply.finished.connect = lambda fn: slots.append(fn)
    reply._slots = slots
    return reply


def _intercept_gets(panel):
    """Replace panel._nam.get with a capturer that returns _make_reply-style mocks.

    Returns a dict mapping URL-substring -> the created reply, so tests can fire
    the finished slot with a chosen payload.
    """
    created = []

    def _fake_get(req):
        url = req.url().toString()
        reply = MagicMock()
        reply.attribute.return_value = 200
        reply.readAll.return_value = b"{}"
        slots = []
        reply.finished.connect = lambda fn: slots.append(fn)
        reply._slots = slots
        reply._url = url
        created.append(reply)
        return reply

    panel._nam.get = _fake_get
    panel._created_gets = created
    return created


def _fire(reply, status, body_bytes):
    reply.attribute.return_value = status
    reply.readAll.return_value = body_bytes
    for fn in reply._slots:
        fn()


def test_script_combo_is_editable_with_contains_completer():
    """Script field is an editable QComboBox with a CaseInsensitive + MatchContains completer."""
    _get_app()
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QComboBox
    from dataforge.gui.panels.job_editor import JobEditorPanel
    panel = JobEditorPanel()
    assert isinstance(panel._t_script_combo, QComboBox)
    assert panel._t_script_combo.isEditable()
    assert panel._t_script_combo.insertPolicy() == QComboBox.InsertPolicy.NoInsert
    comp = panel._t_script_combo.completer()
    assert comp is not None
    assert comp.caseSensitivity() == Qt.CaseSensitivity.CaseInsensitive
    assert comp.filterMode() == Qt.MatchFlag.MatchContains
    panel.close()


def test_job_and_upstream_combos_have_contains_completer():
    """Job picker AND the depends_on upstream combo get the same CONTAINS/case-insensitive completer."""
    _get_app()
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QComboBox
    from dataforge.gui.panels.job_editor import JobEditorPanel
    panel = JobEditorPanel()
    jc = panel._t_job_combo.completer()
    assert jc is not None
    assert jc.caseSensitivity() == Qt.CaseSensitivity.CaseInsensitive
    assert jc.filterMode() == Qt.MatchFlag.MatchContains
    assert panel._t_job_combo.insertPolicy() == QComboBox.InsertPolicy.NoInsert
    # upstream depends combo
    panel.load_job_data("ws", None)
    panel._add_task()
    panel._task_list.setCurrentRow(0)
    panel._add_depends_row()
    combo = panel._t_depends_table.cellWidget(0, 0)
    uc = combo.completer()
    assert uc is not None
    assert uc.caseSensitivity() == Qt.CaseSensitivity.CaseInsensitive
    assert uc.filterMode() == Qt.MatchFlag.MatchContains
    panel.close()


def test_load_fires_scripts_and_jobs_fetch():
    """load_job_data fires async GET /{ws}/scripts and GET /{ws}/jobs."""
    _get_app()
    from dataforge.gui.panels.job_editor import JobEditorPanel
    panel = JobEditorPanel()
    created = _intercept_gets(panel)
    panel.load_job_data("test_ws", None)
    urls = [r._url for r in created]
    assert any(u.endswith("/test_ws/scripts") for u in urls)
    assert any(u.endswith("/test_ws/jobs") for u in urls)
    panel.close()


def test_scripts_reply_populates_script_combo():
    """The /scripts reply populates the Script combo's item list."""
    _get_app()
    from dataforge.gui.panels.job_editor import JobEditorPanel
    panel = JobEditorPanel()
    created = _intercept_gets(panel)
    panel.load_job_data("test_ws", None)
    scripts_reply = next(r for r in created if r._url.endswith("/scripts"))
    # /scripts returns BARE paths (relative to scripts/); the combo deals in bare end-to-end.
    _fire(scripts_reply, 200, json.dumps(
        {"scripts": ["a.py", "cleansed2curated/fact_fatura_mes.py"]}).encode())
    items = [panel._t_script_combo.itemText(i) for i in range(panel._t_script_combo.count())]
    assert "cleansed2curated/fact_fatura_mes.py" in items
    assert "a.py" in items
    panel.close()


def test_jobs_reply_populates_job_combo_excluding_self():
    """The /jobs reply populates the Job combo's item list, excluding the job being edited."""
    _get_app()
    from dataforge.gui.panels.job_editor import JobEditorPanel
    panel = JobEditorPanel()
    created = _intercept_gets(panel)
    panel.load_job_data("test_ws", {"name": "etl", "env_file": None, "schedule": None,
                                    "retry": {"attempts": 3, "delay_seconds": 30},
                                    "tasks": [], "parameters": []})
    jobs_reply = next(r for r in created if r._url.endswith("/jobs"))
    _fire(jobs_reply, 200, json.dumps(
        [{"name": "etl", "parameters": []},
         {"name": "other", "parameters": []},
         {"name": "third", "parameters": []}]).encode())
    items = [panel._t_job_combo.itemText(i) for i in range(panel._t_job_combo.count())]
    assert "other" in items
    assert "third" in items
    assert "etl" not in items  # self excluded
    panel.close()


def test_repopulation_preserves_freetext_script():
    """A script value NOT in the fetched list survives repopulation AND ends up in the payload."""
    _get_app()
    from dataforge.gui.panels.job_editor import JobEditorPanel
    panel = JobEditorPanel()
    created = _intercept_gets(panel)
    panel.load_job_data("test_ws", {
        "name": "etl", "env_file": None, "schedule": None,
        "retry": {"attempts": 3, "delay_seconds": 30},
        "tasks": [{"name": "a", "type": "python", "script": "not_yet.py", "job": None,
                   "params": {}, "depends_on": [], "match": "all",
                   "retry": {"attempts": 3, "delay_seconds": 30}, "active": True}],
        "parameters": [],
    })
    panel._task_list.setCurrentRow(0)
    # current free-text value (a not-yet-created, bare script path) is shown
    assert panel._t_script_combo.currentText() == "not_yet.py"
    # now the /scripts reply arrives WITHOUT that path (bare paths)
    scripts_reply = next(r for r in created if r._url.endswith("/scripts"))
    _fire(scripts_reply, 200, json.dumps({"scripts": ["other.py"]}).encode())
    # free-text preserved (repopulation did not wipe it)
    assert panel._t_script_combo.currentText() == "not_yet.py"
    payload = panel._build_job_payload()
    assert payload["tasks"][0]["script"] == "not_yet.py"
    panel.close()


def test_type_toggle_shows_hides_script_vs_job_rows():
    """type=python shows Script row, hides Job; type=job shows Job, hides Script."""
    _get_app()
    from dataforge.gui.panels.job_editor import JobEditorPanel
    panel = JobEditorPanel()
    panel.show()  # offscreen: isVisible reflects setVisible only once top-level shown
    panel.load_job_data("test_ws", None)
    panel._add_task()
    panel._task_list.setCurrentRow(0)
    # python (default)
    panel._t_type_combo.setCurrentText("python")
    assert panel._t_script_combo.isVisible()
    assert not panel._t_job_combo.isVisible()
    # job
    panel._t_type_combo.setCurrentText("job")
    assert panel._t_job_combo.isVisible()
    assert not panel._t_script_combo.isVisible()
    panel.close()


def test_layout_has_groupbox_sections():
    """Detail form is organised into labeled QGroupBox sections."""
    _get_app()
    from PySide6.QtWidgets import QGroupBox
    from dataforge.gui.panels.job_editor import JobEditorPanel
    panel = JobEditorPanel()
    titles = {gb.title() for gb in panel._task_detail.findChildren(QGroupBox)}
    for expected in ("Identity", "Action", "Dependencies", "Params", "Advanced"):
        assert expected in titles, f"missing section {expected!r}; got {titles}"
    panel.close()


def test_match_lives_in_dependencies_and_roundtrips():
    """The `match` selector (relocated into Dependencies) still round-trips into the payload."""
    _get_app()
    from PySide6.QtWidgets import QGroupBox
    from dataforge.gui.panels.job_editor import JobEditorPanel
    panel = JobEditorPanel()
    # the match combo is a descendant of the Dependencies group box
    deps_gb = next(gb for gb in panel._task_detail.findChildren(QGroupBox)
                   if gb.title() == "Dependencies")
    assert panel._t_match_combo in deps_gb.findChildren(type(panel._t_match_combo))
    # round-trip: set match=any, build payload
    panel.load_job_data("test_ws", None)
    panel._add_task()
    panel._task_list.setCurrentRow(0)
    panel._tasks[0]["name"] = "a"
    panel._t_match_combo.setCurrentText("any")
    payload = panel._build_job_payload()
    assert payload["tasks"][0]["match"] == "any"
    panel.close()


def test_delete_confirm_lists_referrers(monkeypatch):
    """The confirmation dialog message LISTS the referring triggers/tasks/schedule."""
    _get_app()
    from dataforge.gui.panels.job_editor import JobEditorPanel
    from dataforge.gui.panels import job_editor as je
    from PySide6.QtWidgets import QMessageBox

    panel = JobEditorPanel()
    panel.load_job_data("test_ws", {"name": "target", "env_file": None, "schedule": None,
                                    "retry": {"attempts": 3, "delay_seconds": 30},
                                    "tasks": [], "parameters": []})
    refs = {"triggers": ["nightly"], "tasks": ["caller.c1"], "schedule": True}

    captured = {}

    def _fake_question(parent, title, text, *a, **k):
        captured["text"] = text
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(je.QMessageBox, "question", _fake_question)
    panel._nam.deleteResource = lambda req: None
    panel._confirm_and_delete(refs)

    assert "nightly" in captured["text"]
    assert "caller.c1" in captured["text"]
    assert "schedule" in captured["text"].lower()
    panel.close()
