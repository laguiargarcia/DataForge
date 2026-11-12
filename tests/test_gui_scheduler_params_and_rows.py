"""Offscreen tests for the scheduler's JobParamsDialog (typed widgets, OK validation,
result_params), the trigger table rendering and the job-row wiring (df-a4cq.2, written before the
S3776 split of _make_value_widget, _on_ok, result_params, _render_trigger_rows, _append_job_row)."""
from datetime import datetime, timedelta

import pytest

pytest.importorskip("PySide6")


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["test", "-platform", "offscreen"])


@pytest.fixture
def sched(qapp):
    from dataforge.gui.panels import scheduler
    return scheduler


def _dialog(sched, declared=(), values=None):
    return sched.JobParamsDialog(list(declared), values or {}, "ws", "job")


# ---------------------------------------------------------------------------
# _make_value_widget / _read_value_widget
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(("type_str", "value", "expected"), [
    ("int", 7, 7),
    ("int", "12", 12),
    ("int", None, 0),
    ("int", "abc", 0),
    ("int", [1], 0),
    pytest.param("int", 3_000_000_000, 0, marks=[
        pytest.mark.xfail(strict=True, raises=OverflowError,
                          reason="df-dqg1: QSpinBox.setValue overflows past int32, not caught"),
        pytest.mark.filterwarnings("ignore:libshiboken"),
    ]),
    ("float", 2.5, 2.5),
    ("float", "1.25", 1.25),
    ("float", None, 0.0),
    ("float", "x", 0.0),
    ("bool", True, True),
    ("bool", 0, False),
    ("bool", "yes", True),
    ("bool", None, False),
    ("date", "2026-02-03", "2026-02-03"),
    ("string", "hi", "hi"),
    ("string", 5, "5"),
    ("string", None, ""),
    ("unknown-type", "x", "x"),
    ("json", "raw text", "raw text"),
    ("json", {"a": 1}, '{"a": 1}'),
    ("json", [1, 2], "[1, 2]"),
    ("json", None, ""),
])
def test_value_widget_roundtrip(sched, type_str, value, expected):
    d = _dialog(sched)
    w = d._make_value_widget(type_str, value)
    assert d._read_value_widget(w, type_str) == expected


@pytest.mark.parametrize("value", [None, "not-a-date", "2026-02", "2026-xx-01", 20260203])
def test_date_widget_ignores_values_it_cannot_parse(sched, value):
    d = _dialog(sched)
    default = d._read_value_widget(d._make_value_widget("date"), "date")
    assert d._read_value_widget(d._make_value_widget("date", value), "date") == default


def test_json_widget_falls_back_to_str_for_unserializable_values(sched):
    d = _dialog(sched)
    w = d._make_value_widget("json", {1, 2})
    assert d._read_value_widget(w, "json") == str({1, 2})


@pytest.mark.parametrize(("type_str", "cls_name"), [
    ("int", "QSpinBox"), ("float", "QDoubleSpinBox"), ("bool", "QCheckBox"),
    ("date", "QDateEdit"), ("json", "QPlainTextEdit"), ("string", "QLineEdit"),
    ("other", "QLineEdit"),
])
def test_value_widget_class_per_type(sched, type_str, cls_name):
    assert type(_dialog(sched)._make_value_widget(type_str)).__name__ == cls_name


def test_value_widget_settings(sched):
    d = _dialog(sched)
    i = d._make_value_widget("int")
    assert (i.minimum(), i.maximum()) == (-2_147_483_648, 2_147_483_647)
    f = d._make_value_widget("float")
    assert (f.minimum(), f.maximum(), f.decimals()) == (-1e15, 1e15, 6)
    assert d._make_value_widget("date").calendarPopup() is True
    j = d._make_value_widget("json")
    assert (j.minimumHeight(), j.maximumHeight()) == (60, 60)


# ---------------------------------------------------------------------------
# _on_ok
# ---------------------------------------------------------------------------

@pytest.fixture
def warnings(monkeypatch, sched):
    seen = []
    monkeypatch.setattr(sched.QMessageBox, "warning", lambda *a: seen.append(a[2]))
    return seen


def _accepted(monkeypatch, d):
    calls = []
    monkeypatch.setattr(d, "accept", lambda: calls.append(True))
    d._on_ok()
    return bool(calls)


@pytest.mark.parametrize("text", ["", "   ", '{"a": 1}', "[1]", "3"])
def test_ok_accepts_empty_or_valid_json(monkeypatch, sched, warnings, text):
    d = _dialog(sched, [{"name": "cfg", "type": "json"}])
    d._table.cellWidget(0, 2).setPlainText(text)
    assert _accepted(monkeypatch, d)
    assert warnings == []


def test_ok_rejects_invalid_json_in_a_declared_row(monkeypatch, sched, warnings):
    d = _dialog(sched, [{"name": "a", "type": "string"}, {"name": "cfg", "type": "json"}])
    d._table.cellWidget(1, 2).setPlainText("{bad")
    assert not _accepted(monkeypatch, d)
    assert len(warnings) == 1
    assert warnings[0].startswith("Row 2: invalid JSON")


def test_ok_rejects_invalid_json_in_a_free_row(monkeypatch, sched, warnings):
    d = _dialog(sched)
    d._add_free_row()
    d._table.cellWidget(0, 1).setCurrentText("json")
    d._table.cellWidget(0, 2).setPlainText("nope")
    assert not _accepted(monkeypatch, d)
    assert warnings[0].startswith("Row 1: invalid JSON")


def test_ok_ignores_invalid_text_in_non_json_rows(monkeypatch, sched, warnings):
    d = _dialog(sched, [{"name": "s", "type": "string"}])
    d._table.cellWidget(0, 2).setText("{bad")
    d._add_free_row()
    d._table.cellWidget(1, 2).setText("{bad")
    assert _accepted(monkeypatch, d)
    assert warnings == []


def test_ok_skips_rows_without_a_type_widget_or_text_widget(monkeypatch, sched, warnings):
    from PySide6.QtWidgets import QLabel, QLineEdit
    d = _dialog(sched, [{"name": "cfg", "type": "json"}, {"name": "x", "type": "json"}])
    d._table.removeCellWidget(0, 1)                      # no type widget: row skipped
    d._table.cellWidget(0, 2).setPlainText("{bad")
    d._table.setCellWidget(1, 2, QLineEdit("{bad"))      # json type but not a text-edit
    assert isinstance(d._table.cellWidget(1, 1), QLabel)
    assert _accepted(monkeypatch, d)
    assert warnings == []


# ---------------------------------------------------------------------------
# result_params
# ---------------------------------------------------------------------------

def test_result_params_reads_declared_rows_by_type(sched):
    declared = [
        {"name": "n", "type": "int"}, {"name": "f", "type": "float"},
        {"name": "b", "type": "bool"}, {"name": "d", "type": "date"},
        {"name": "s", "type": "string"}, {"name": "u"},
        {"name": "j", "type": "json"}, {"name": "jbad", "type": "json"},
        {"name": "jempty", "type": "json", "default": None},
    ]
    values = {"n": 3, "f": 1.5, "b": True, "d": "2026-03-04", "s": "x", "u": "dflt",
              "j": {"k": [1]}, "jbad": "{oops"}
    assert _dialog(sched, declared, values).result_params() == {
        "n": 3, "f": 1.5, "b": True, "d": "2026-03-04", "s": "x", "u": "dflt",
        "j": {"k": [1]}, "jbad": "{oops", "jempty": {},
    }


def test_result_params_uses_declared_default_when_no_current_value(sched):
    d = _dialog(sched, [{"name": "n", "type": "int", "default": 9}])
    assert d.result_params() == {"n": 9}


def test_result_params_reads_free_rows(sched):
    d = _dialog(sched)
    for name, type_str, fill in [
        ("  a  ", "string", lambda w: w.setText("v")),
        ("j", "json", lambda w: w.setPlainText('{"x": 1}')),
        ("", "string", lambda w: w.setText("dropped")),
        ("   ", "int", lambda w: w.setValue(4)),
        ("n", "int", lambda w: w.setValue(4)),
    ]:
        d._add_free_row()
        row = d._table.rowCount() - 1
        d._table.cellWidget(row, 0).setText(name)
        d._table.cellWidget(row, 1).setCurrentText(type_str)
        fill(d._table.cellWidget(row, 2))
    assert d.result_params() == {"a": "v", "j": {"x": 1}, "n": 4}


def test_result_params_free_row_type_change_replaces_the_value_widget(sched):
    d = _dialog(sched)
    d._add_free_row()
    d._table.cellWidget(0, 0).setText("flag")
    d._table.cellWidget(0, 1).setCurrentText("bool")
    assert type(d._table.cellWidget(0, 2)).__name__ == "QCheckBox"
    d._table.cellWidget(0, 2).setChecked(True)
    assert d.result_params() == {"flag": True}


def test_result_params_json_widget_that_is_not_a_text_edit_reads_as_empty_dict(sched):
    from PySide6.QtWidgets import QLineEdit
    d = _dialog(sched, [{"name": "j", "type": "json"}])
    d._table.setCellWidget(0, 2, QLineEdit("ignored"))
    assert d.result_params() == {"j": {}}


def test_result_params_skips_rows_missing_name_or_value_widget(sched):
    from PySide6.QtWidgets import QPushButton
    d = _dialog(sched, [{"name": "a", "type": "string"}, {"name": "b", "type": "string"},
                        {"name": "c", "type": "string"}])
    d._table.removeCellWidget(0, 0)                         # no name widget
    d._table.removeCellWidget(1, 2)                         # no value widget
    d._table.setCellWidget(2, 0, QPushButton("c"))          # name widget of an unknown kind
    d._add_free_row()
    d._table.cellWidget(3, 0).setText("free")
    d._table.removeCellWidget(3, 1)                         # free row without type combo: string
    d._table.cellWidget(3, 2).setText("v")
    assert d.result_params() == {"free": "v"}


def test_result_params_declared_row_without_type_property_reads_as_string(sched):
    d = _dialog(sched, [{"name": "a", "type": "int"}])
    d._table.cellWidget(0, 0).setProperty("param_type", None)
    from PySide6.QtWidgets import QLineEdit
    d._table.setCellWidget(0, 2, QLineEdit("7"))
    assert d.result_params() == {"a": "7"}


# ---------------------------------------------------------------------------
# _render_trigger_rows
# ---------------------------------------------------------------------------

@pytest.fixture
def panel(monkeypatch, sched):
    monkeypatch.setattr(sched.Panel, "_load_triggers", lambda self: None)
    monkeypatch.setattr(sched, "_local_offset", lambda: timedelta(hours=-3))
    p = sched.Panel()
    p._current_workspace = "ws-a"
    yield p
    p.close()


def _trigger_rows(panel):
    t = panel._trigger_table
    return [[t.item(r, c).text() for c in range(t.columnCount())] for r in range(t.rowCount())]


_NEXT = datetime(2026, 5, 1, 9, 30)


@pytest.mark.parametrize(("mode", "shown"), [   # shown: (cron, next run) in that mode, UTC-3
    ("local", ("0 3 * * *", "2026-05-01 06:30 local")),
    ("utc", ("0 6 * * *", "2026-05-01 09:30 UTC")),
])
def test_trigger_rows_follow_display_mode(monkeypatch, sched, panel, mode, shown):
    monkeypatch.setattr(sched, "next_run", lambda expr: _NEXT)
    panel._display_mode = mode
    panel._trigger_data = [{"name": "t", "cron": "0 6 * * *", "enabled": True, "last_run": "yday"}]
    panel._render_trigger_rows()
    assert _trigger_rows(panel) == [["t", shown[0], "Yes", "yday", shown[1]]]


@pytest.mark.parametrize("mode", ["local", "utc"])
def test_trigger_rows_defaults_and_no_next_run(monkeypatch, sched, panel, mode):
    monkeypatch.setattr(sched, "next_run", lambda expr: None)
    panel._display_mode = mode
    panel._trigger_data = [{}, {"name": "off", "cron": "bad", "enabled": False, "last_run": None}]
    panel._render_trigger_rows()
    assert _trigger_rows(panel) == [
        ["", "", "Yes", "—", "—"],
        ["off", "bad", "No", "—", "—"],
    ]


def test_trigger_rows_next_run_is_asked_with_the_utc_cron(monkeypatch, sched, panel):
    asked = []
    monkeypatch.setattr(sched, "next_run", lambda expr: asked.append(expr))
    panel._display_mode = "local"
    panel._trigger_data = [{"name": "t", "cron": "0 6 * * *"}]
    panel._render_trigger_rows()
    assert asked == ["0 6 * * *"]


def test_trigger_rows_replace_previous_rows(monkeypatch, sched, panel):
    monkeypatch.setattr(sched, "next_run", lambda expr: None)
    panel._trigger_data = [{"name": "a"}, {"name": "b"}]
    panel._render_trigger_rows()
    panel._trigger_data = [{"name": "c"}]
    panel._render_trigger_rows()
    assert [r[0] for r in _trigger_rows(panel)] == ["c"]


# ---------------------------------------------------------------------------
# _append_job_row
# ---------------------------------------------------------------------------

_WS = [{"name": "ws-a"}, {"name": "ws-b"}]
_JOBS = [{"name": "job1", "parameters": []},
         {"name": "job2", "parameters": [{"name": "d", "type": "date"}]}]


@pytest.fixture
def rows(monkeypatch, panel):
    monkeypatch.setattr(panel, "_fetch_workspaces", lambda cb: cb(_WS))
    fetched = []
    monkeypatch.setattr(panel, "_fetch_jobs_for_ws", lambda ws, cb: (fetched.append(ws), cb(_JOBS)))
    return fetched


@pytest.mark.parametrize(("workspace", "selected"), [("ws-b", "ws-b"), ("", "ws-a"), ("gone", "ws-a")])
def test_job_row_preselects_the_workspace_when_listed(panel, rows, workspace, selected):
    panel._append_job_row(workspace, "", {})
    assert panel._jobs_table.cellWidget(0, 0).currentText() == selected
    assert rows == [selected]       # one job fetch, for the selected workspace


def test_job_row_shows_param_count_and_keeps_params(panel, rows):
    panel._append_job_row("ws-a", "job1", {"a": 1, "b": 2})
    btn = panel._jobs_table.cellWidget(0, 2)
    assert btn.text() == "Configure… (2 params)"
    assert btn.property("params") == {"a": 1, "b": 2}


def test_job_row_workspace_change_refetches_jobs(panel, rows):
    panel._append_job_row("ws-a", "", {})
    panel._jobs_table.cellWidget(0, 0).setCurrentIndex(1)
    assert rows == ["ws-a", "ws-b"]


def _capture_dialog(monkeypatch, sched, accepted, result=None):
    from PySide6.QtWidgets import QDialog
    opened = []

    class _Dlg:
        def __init__(self, declared, current, ws, job, parent=None):
            opened.append((declared, current, ws, job))

        def exec(self):
            return QDialog.DialogCode.Accepted if accepted else QDialog.DialogCode.Rejected

        def result_params(self):
            return result

    monkeypatch.setattr(sched, "JobParamsDialog", _Dlg)
    return opened


def test_configure_opens_dialog_with_row_state(monkeypatch, sched, panel, rows):
    opened = _capture_dialog(monkeypatch, sched, accepted=True, result={"d": "2026-01-01"})
    panel._append_job_row("ws-a", "job2", {"old": 1})
    btn = panel._jobs_table.cellWidget(0, 2)
    btn.click()
    assert opened == [(_JOBS[1]["parameters"], {"old": 1}, "ws-a", "job2")]
    assert btn.property("params") == {"d": "2026-01-01"}
    assert btn.text() == "Configure… (1 params)"


def test_configure_cancelled_keeps_params(monkeypatch, sched, panel, rows):
    _capture_dialog(monkeypatch, sched, accepted=False)
    panel._append_job_row("ws-a", "job1", {"old": 1})
    btn = panel._jobs_table.cellWidget(0, 2)
    btn.click()
    assert btn.property("params") == {"old": 1}
    assert btn.text() == "Configure… (1 params)"


def test_configure_with_no_job_selected_passes_no_declared_params(monkeypatch, sched, panel):
    monkeypatch.setattr(panel, "_fetch_workspaces", lambda cb: cb(_WS))
    monkeypatch.setattr(panel, "_fetch_jobs_for_ws", lambda ws, cb: cb([]))
    opened = _capture_dialog(monkeypatch, sched, accepted=False)
    panel._append_job_row("ws-a", "", {})
    panel._jobs_table.cellWidget(0, 2).click()
    assert opened == [([], {}, "ws-a", "")]


def test_configure_with_job_without_parameters_data_passes_empty_list(monkeypatch, sched, panel):
    monkeypatch.setattr(panel, "_fetch_workspaces", lambda cb: cb(_WS))
    monkeypatch.setattr(panel, "_fetch_jobs_for_ws", lambda ws, cb: cb([{"name": "j", "parameters": None}]))
    opened = _capture_dialog(monkeypatch, sched, accepted=False)
    panel._append_job_row("ws-a", "j", {})
    panel._jobs_table.cellWidget(0, 2).click()
    assert opened == [([], {}, "ws-a", "j")]


# ---------------------------------------------------------------------------
# Row buttons after an earlier row is removed (df-r3x8): each button acts on its own row, found
# when it fires, not on the index the row had when it was built.
# ---------------------------------------------------------------------------

def _free_rows(d, *names):
    for name in names:
        d._add_free_row()
        d._table.cellWidget(d._table.rowCount() - 1, 0).setText(name)


def _names(d):
    return [d._table.cellWidget(r, 0).text() for r in range(d._table.rowCount())]


def test_free_row_remove_buttons_follow_their_rows(sched):
    d = _dialog(sched)
    _free_rows(d, "a", "b", "c")
    buttons = [d._table.cellWidget(r, 3) for r in range(3)]
    buttons[0].click()
    buttons[1].click()
    assert _names(d) == ["c"]


def test_last_free_row_remove_button_works_after_an_earlier_removal(sched):
    d = _dialog(sched)
    _free_rows(d, "a", "b", "c")
    buttons = [d._table.cellWidget(r, 3) for r in range(3)]
    buttons[0].click()
    buttons[2].click()
    assert _names(d) == ["b"]


def test_declared_row_remove_buttons_follow_their_rows(sched):
    d = _dialog(sched, [{"name": n, "type": "string"} for n in ("a", "b", "c")])
    buttons = [d._table.cellWidget(r, 3) for r in range(3)]
    buttons[0].click()
    buttons[2].click()
    assert d.result_params() == {"b": ""}


def test_free_row_type_change_after_an_earlier_removal_replaces_its_own_value(sched):
    d = _dialog(sched)
    _free_rows(d, "a", "b")
    type_combo = d._table.cellWidget(1, 1)
    d._table.cellWidget(0, 3).click()
    type_combo.setCurrentText("bool")
    assert type(d._table.cellWidget(0, 2)).__name__ == "QCheckBox"
    d._table.cellWidget(0, 2).setChecked(True)
    assert d.result_params() == {"b": True}


def _job_rows(panel):
    return [(panel._jobs_table.cellWidget(r, 0).currentText(),
             panel._jobs_table.cellWidget(r, 1).currentText())
            for r in range(panel._jobs_table.rowCount())]


def test_job_row_remove_buttons_follow_their_rows(panel, rows):
    for job in ("job1", "job2", "job1"):
        panel._append_job_row("ws-a", job, {})
    buttons = [panel._jobs_table.cellWidget(r, 3) for r in range(3)]
    buttons[0].click()
    buttons[2].click()
    assert _job_rows(panel) == [("ws-a", "job2")]


def test_job_row_configure_after_an_earlier_removal_opens_its_own_row(monkeypatch, sched, panel,
                                                                       rows):
    opened = _capture_dialog(monkeypatch, sched, accepted=True, result={"d": "2026-01-01"})
    panel._append_job_row("ws-a", "job1", {"first": 1})
    panel._append_job_row("ws-b", "job2", {"second": 2})
    btn = panel._jobs_table.cellWidget(1, 2)
    panel._jobs_table.cellWidget(0, 3).click()
    btn.click()
    assert opened == [(_JOBS[1]["parameters"], {"second": 2}, "ws-b", "job2")]
    assert btn.property("params") == {"d": "2026-01-01"}


def test_job_row_workspace_change_after_an_earlier_removal_refetches_its_jobs(panel, rows):
    panel._append_job_row("ws-a", "", {})
    panel._append_job_row("ws-a", "", {})
    combo_ws = panel._jobs_table.cellWidget(1, 0)
    panel._jobs_table.cellWidget(0, 3).click()
    rows.clear()
    combo_ws.setCurrentIndex(1)
    assert rows == ["ws-b"]


@pytest.fixture
def pending(monkeypatch, panel):
    """Hold workspace and job fetches until the test answers them, like the real async API."""
    held = {"ws": [], "jobs": []}
    monkeypatch.setattr(panel, "_fetch_workspaces", held["ws"].append)
    monkeypatch.setattr(panel, "_fetch_jobs_for_ws", lambda ws, cb: held["jobs"].append(cb))
    return held


def _remove_job_row_and_delete_its_widgets(panel, row):
    """Click the row's ✕ and run the deleteLater that removeRow posts for its cell widgets, as
    the event loop would before a late API answer arrives. Only those widgets: flushing every
    pending deletion in the app also frees leftovers of earlier tests and crashes at exit."""
    from PySide6.QtCore import QCoreApplication, QEvent
    widgets = [panel._jobs_table.cellWidget(row, c) for c in range(4)]
    widgets[3].click()
    for w in widgets:
        QCoreApplication.sendPostedEvents(w, QEvent.Type.DeferredDelete)


def test_late_workspace_answer_fills_the_row_that_asked(panel, pending):
    panel._append_job_row("ws-a", "job1", {})
    panel._append_job_row("ws-b", "job2", {})
    _remove_job_row_and_delete_its_widgets(panel, 0)
    pending["ws"][1](_WS)
    pending["jobs"][-1](_JOBS)
    assert _job_rows(panel) == [("ws-b", "job2")]


def test_late_answers_for_a_removed_row_are_ignored(panel, pending):
    panel._append_job_row("ws-a", "job1", {})
    _remove_job_row_and_delete_its_widgets(panel, 0)
    pending["ws"][0](_WS)
    assert pending["jobs"] == []
    assert panel._jobs_table.rowCount() == 0


def test_late_jobs_answer_for_a_removed_row_is_ignored(panel, pending):
    panel._append_job_row("ws-a", "job1", {})
    pending["ws"][0](_WS)
    _remove_job_row_and_delete_its_widgets(panel, 0)
    pending["jobs"][0](_JOBS)
    assert panel._jobs_table.rowCount() == 0
