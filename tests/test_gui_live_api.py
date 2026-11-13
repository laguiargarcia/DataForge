"""The GUI panels against the real API, through their own signals (df-ahua.16.4).

The other GUI tests replace the network calls with monkeypatch or feed handlers payloads written
by hand; the run detail ones carried a task `output` the server never sent, so they passed while
the run detail stayed empty (df-ahua.16.1). Here each panel talks over a real socket to the API
of tests/live_api.py, on a temporary DATAFORGE_HOME, and every test also checks the contract of
tests/gui_contract.py: each field the panel read from a response is one the server sent.

One test per panel family: jobs (run detail live and from History, the job list and the editor),
main window, scheduler and deploy. The server runs in a background thread; tests/conftest.py keeps
the automatic garbage collector off, so it never destroys a widget off the GUI thread (df-3yh).
"""
import time
import types
from typing import ClassVar

import httpx
import pytest
import yaml

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QPushButton

from tests.gui_contract import FieldReads
from tests.live_api import (
    GATED_JOB, GATED_OUTPUT, GATED_TASK, add_gated_job, release_gated_job, serve_api,
)

_WAIT = 20.0
_WS = "test_ws"


class _MemorySettings:
    """QSettings stand-in: the tests must not write the developer's own GUI settings."""
    _values: ClassVar[dict] = {}

    def __init__(self, *_args) -> None:
        pass

    def value(self, key, defaultValue=None):
        return self._values.get(key, defaultValue)

    def setValue(self, key, value) -> None:
        self._values[key] = value


@pytest.fixture
def app():
    return QApplication.instance() or QApplication(["test", "-platform", "offscreen"])


@pytest.fixture
def gui(app):
    """The GUI modules, imported once a QApplication exists (docs/subsystems/gui.md)."""
    from dataforge.gui import main_window
    from dataforge.gui.panels import deploy, job_editor, jobs, scheduler
    return types.SimpleNamespace(main_window=main_window, deploy=deploy, job_editor=job_editor,
                                 jobs=jobs, scheduler=scheduler)


@pytest.fixture
def live(workspace_dir, monkeypatch):
    """The real API on a free port, with the panels pointed at it (they read the port when built)."""
    add_gated_job(workspace_dir)
    with serve_api() as base:
        monkeypatch.setenv("DATAFORGE_PORT", base.rsplit(":", 1)[1])
        yield base


@pytest.fixture
def reads(gui, monkeypatch):
    recorder = FieldReads()
    recorder.install(monkeypatch, gui.jobs, gui.job_editor, gui.scheduler, gui.deploy,
                     gui.main_window)
    monkeypatch.setattr(gui.jobs, "QSettings", _MemorySettings)
    monkeypatch.setattr(gui.main_window, "QSettings", _MemorySettings)
    _MemorySettings._values.clear()
    return recorder


def _pump() -> None:
    """Deliver the pending Qt events, then let the server thread run. The sleep matters:
    QTest.qWait keeps the GIL while it waits, so the server would answer only between waits."""
    QApplication.processEvents()
    time.sleep(0.005)


def _wait_for(condition, what: str, timeout: float = _WAIT) -> None:
    """Run the Qt event loop until `condition()` holds."""
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out after {timeout}s waiting for {what}")
        _pump()


def _settle() -> None:
    """A few rounds of events: aborted replies and deleteLater take effect."""
    for _ in range(5):
        _pump()


def _drop_streams(panel) -> None:
    """Abort the panel's open SSE connections, so the server can stop."""
    for reply in (getattr(panel, "_run_sse_reply", None), getattr(panel, "_sse_reply", None)):
        if reply is not None:
            panel._current_workspace = None   # no reconnect when the workspace stream ends
            reply.abort()


def _close(panel) -> None:
    _drop_streams(panel)
    _settle()
    panel.close()
    panel.deleteLater()
    _settle()


def _button(widget, text: str) -> QPushButton:
    found = [b for b in widget.findChildren(QPushButton) if b.text() == text]
    assert found, f"no '{text}' button"
    return found[0]


def _assert_contract(reads: FieldReads, known_gaps: tuple[str, ...] = ()) -> None:
    """Every field the panel read was sent by the server, apart from `known_gaps` (key paths of
    drifts that have a bead and a strict xfail of their own)."""
    assert reads.read(), "the panel read no field from the server: the test drove nothing"
    gaps = [line for line in reads.unsatisfied() if not any(gap in line for gap in known_gaps)]
    assert gaps == [], "the GUI reads fields the server does not send"


def _history_rows(base: str) -> list[dict]:
    return httpx.get(f"{base}/api/workspaces/{_WS}/executions", timeout=10).json()["items"]


def _log_lines(panel) -> list[str]:
    return panel._task_tabs.widget(0).toPlainText().splitlines()


@pytest.fixture
def jobs_panel(gui, live, reads):
    panel = gui.jobs.Panel()
    panel.set_workspace(_WS)
    _wait_for(lambda: GATED_JOB in panel._job_rows, "the job list")
    yield panel
    _close(panel)


def _open_newest_run_from_history(panel) -> None:
    _button(panel, "History").click()
    _wait_for(lambda: panel._history_table.rowCount() == 1, "the run in History")
    panel._history_table.cellClicked.emit(0, 0)
    assert panel._stack.currentIndex() == panel.PAGE_DETAIL


def test_run_detail_follows_a_run_started_with_the_run_button_and_shows_its_log(
        jobs_panel, live, reads, workspace_dir):
    """df-ahua.16.1: Run, History, the run's row; the detail shows the task's log when it ends."""
    panel = jobs_panel
    _button(panel, "Run").click()
    _wait_for(lambda: _history_rows(live), "the run to be created")
    _open_newest_run_from_history(panel)
    _wait_for(lambda: panel._task_tabs.count() == 1
              and panel._task_tabs.tabText(0) == f"{GATED_TASK} [running]", "the running task")
    assert panel._detail_status_badge.text() == "running"
    assert _log_lines(panel) == []   # the output is stored when the task ends

    release_gated_job(workspace_dir)
    _wait_for(lambda: panel._detail_status_badge.text() == "success", "the run to end")
    assert panel._task_tabs.tabText(0) == f"{GATED_TASK} [success]"
    assert _log_lines(panel) == GATED_OUTPUT.splitlines()
    assert panel._run_sse_reply is None   # the panel closed the stream on the terminal status
    _assert_contract(reads)


def _open_a_failed_run(panel, live: str, workspace_dir) -> None:
    """Run the job to its failure through the API, then open it from History."""
    release_gated_job(workspace_dir, 1)
    httpx.post(f"{live}/api/workspaces/{_WS}/jobs/{GATED_JOB}/run", timeout=10)
    _wait_for(lambda: [r["status"] for r in _history_rows(live)] == ["failed"], "the run to end")
    _open_newest_run_from_history(panel)
    _wait_for(lambda: panel._detail_status_badge.text() == "failed", "the run's snapshot")


def test_run_detail_of_a_finished_run_opened_from_history_shows_its_log(
        jobs_panel, live, reads, workspace_dir):
    panel = jobs_panel
    _open_a_failed_run(panel, live, workspace_dir)
    assert panel._history_table.item(0, 2).text() == "failed"
    assert panel._task_tabs.tabText(0) == f"{GATED_TASK} [failed]"
    assert _log_lines(panel) == GATED_OUTPUT.splitlines()
    _assert_contract(reads)


@pytest.mark.xfail(strict=True, reason="df-ahua.16.13: the jobs panel reads aliases never sent")
def test_jobs_panel_reads_no_alias_the_server_never_sends(jobs_panel, live, reads, workspace_dir):
    _open_a_failed_run(jobs_panel, live, workspace_dir)
    assert reads.never_sent() == []


def test_edit_opens_the_job_in_the_editor_with_its_tasks_and_pickers(jobs_panel, reads):
    panel = jobs_panel
    _button(panel, "Edit").click()
    _wait_for(lambda: panel._stack.currentIndex() == panel.PAGE_EDITOR, "the editor")
    editor = panel._editor
    assert editor._name_edit.text() == GATED_JOB
    assert [t["name"] for t in editor._tasks] == [GATED_TASK]
    _wait_for(lambda: "gated.py" in editor._script_choices, "the script picker")
    _assert_contract(reads)


def test_main_window_lists_the_workspaces_and_hands_the_first_to_the_panels(gui, live, reads):
    window = gui.main_window.MainWindow()
    jobs = window._stack.widget(0)
    try:
        _wait_for(lambda: window._ws_combo.count() == 1, "the workspace list")
        assert window._ws_combo.currentText() == _WS
        _wait_for(lambda: GATED_JOB in jobs._job_rows, "the jobs panel's list")
        assert jobs._job_rows[GATED_JOB].text() == "Never run"
        _assert_contract(reads)
    finally:
        for i in range(window._stack.count()):
            _drop_streams(window._stack.widget(i))
        _settle()
        window.close()   # the admin window is a plain client of the server: it closes for real
        window.deleteLater()
        _settle()


_LAST_RUN = "$[].last_run"   # df-ahua.16.12: the Last Run column reads a field never sent


@pytest.fixture
def scheduler_panel(gui, live, reads, workspace_dir):
    """The Scheduler on a workspace with one trigger, its list loaded and the trigger opened."""
    (workspace_dir / "triggers").mkdir()
    (workspace_dir / "triggers" / "nightly.yaml").write_bytes(yaml.safe_dump({
        "name": "nightly", "cron": "0 3 * * *", "enabled": True,
        "jobs": [{"workspace": _WS, "job": GATED_JOB, "params": {}}],
    }).encode("utf-8"))
    panel = gui.scheduler.Panel()
    panel.set_workspace(_WS)
    _wait_for(lambda: panel._trigger_table.rowCount() == 1, "the trigger list")
    panel._trigger_table.cellClicked.emit(0, 0)
    _wait_for(lambda: panel._jobs_table.rowCount() == 1
              and panel._jobs_table.cellWidget(0, 1).currentText() == GATED_JOB,
              "the trigger's job row")
    yield panel
    _close(panel)


def test_scheduler_lists_a_trigger_and_opens_it_with_its_job_row(gui, scheduler_panel, reads):
    panel = scheduler_panel
    assert panel._trigger_table.item(0, 0).text() == "nightly"
    shown = "0 3 * * *"   # stored in UTC, shown in the panel's display mode
    if panel._display_mode == "local":
        shown = gui.scheduler.utc_to_local_cron(shown)
    assert panel._trigger_table.item(0, 1).text() == shown
    assert panel._name_edit.text() == "nightly"
    assert panel._jobs_table.cellWidget(0, 0).currentText() == _WS
    _assert_contract(reads, known_gaps=(_LAST_RUN,))


@pytest.mark.xfail(strict=True, reason="df-ahua.16.12: GET /{ws}/triggers sends no last_run")
def test_scheduler_reads_only_fields_the_server_sends(scheduler_panel, reads):
    _assert_contract(reads)


def test_deploy_shows_the_plan_and_the_last_deploy(gui, live, reads, dataforge_home, workspace_dir):
    prod = dataforge_home / "workspaces" / "prod_ws"
    prod.mkdir()
    (prod / "workspace.yaml").write_bytes(b"name: prod_ws\n")
    (workspace_dir / "deploy").mkdir()
    (workspace_dir / "deploy" / "to-prod.yaml").write_bytes(
        b"target: prod_ws\npairs:\n  - {source: 'scripts/**', target: 'scripts/'}\n")
    applied = httpx.post(f"{live}/api/workspaces/{_WS}/deploy/to-prod/apply", json={}, timeout=10)
    assert applied.status_code == 200
    (workspace_dir / "scripts" / "new.py").write_bytes(b"print('new')\n")

    panel = gui.deploy.Panel()
    try:
        panel.set_workspace(_WS)
        _wait_for(lambda: "last:" in panel._status_label.text(), "the deploy status")
        _wait_for(lambda: panel._tree.topLevelItemCount() > 0, "the plan")
        assert panel._route_label.text() == f"{_WS} → prod_ws"
        assert panel._status_label.text().startswith("1 pending · last: test_ws → prod_ws [to-prod]")
        assert panel._rollback_btn.isEnabled()
        planned = [panel._tree.topLevelItem(i).child(j).text(0)
                   for i in range(panel._tree.topLevelItemCount())
                   for j in range(panel._tree.topLevelItem(i).childCount())]
        assert "scripts/new.py" in planned
        _assert_contract(reads)
    finally:
        _close(panel)
