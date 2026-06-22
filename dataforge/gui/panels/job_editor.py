"""Job editor panel — form-based create/edit/delete for jobs + inline tasks.

Async-only CRUD via QNetworkAccessManager (no blocking HTTP in the Qt thread).
Hosted as a stack page by ``gui/panels/jobs.py``; the jobs panel feeds it the
workspace and navigates to/from it.

Locked decisions honored here:
- D1: job name is immutable in edit mode (read-only name field).
- D-SCHEDULE: NO schedule field on the form (managed by the Scheduler/CLI).
- D-PRESERVE: unshown job-level keys (parameters, schedule) are stashed verbatim
  on load and re-emitted verbatim on save so the full-overwrite POST/PUT never
  wipes them.
- Typed task params survive load->save (JSON-parse-per-cell, string fallback).
- Empty depends_on `when` is rejected (would silently revert to [succeeded]).
"""
from __future__ import annotations

import json
import os
from typing import Optional

from PySide6.QtCore import QByteArray, Qt, QUrl
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QCompleter,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

# Conditions a depends_on edge may wait on.
_WHEN_CONDITIONS = ("succeeded", "failed", "skipped")


def _attach_contains_completer(combo: QComboBox) -> None:
    """Make an editable combo a searchable picker: type any substring (case-insensitive)
    to filter to matching items. NoInsert keeps free-text from polluting the item list,
    while still SHOWING a not-in-list value (editable combos always render currentText)."""
    combo.setEditable(True)
    combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
    completer = QCompleter(combo.model(), combo)
    completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
    completer.setFilterMode(Qt.MatchFlag.MatchContains)
    combo.setCompleter(completer)


def _repopulate_preserving_text(combo: QComboBox, items: list[str]) -> None:
    """Replace a combo's item list WITHOUT losing the current free-text value.

    Save currentText, block signals (so the clear()/addItems churn does not fire
    writeback or completer activations), swap the items, restore the text. The combo
    is editable so a value absent from `items` still shows (free-text always allowed —
    a not-yet-created script/job is valid; the server's soft warning covers it)."""
    saved = combo.currentText()
    combo.blockSignals(True)
    combo.clear()
    combo.addItems(items)
    combo.setCurrentText(saved)
    combo.blockSignals(False)


class JobEditorPanel(QWidget):
    """Form editor for one job. Async-only CRUD via QNetworkAccessManager."""

    # Fix #1 / D-PRESERVE: the job-level keys the form does NOT expose for editing.
    _UNSHOWN_KEYS = ("parameters", "schedule")

    def __init__(self) -> None:
        super().__init__()
        self._port = int(os.environ.get("DATAFORGE_PORT", 8000))
        self._nam = QNetworkAccessManager(self)
        self._current_workspace = ""
        self._editing_job_name: Optional[str] = None  # None = create mode
        self._on_saved_cb = None  # set by jobs panel to navigate back
        self._preserved: dict = {}  # Fix #1 / D-PRESERVE: raw unshown keys (parameters, schedule)
        self._tasks: list[dict] = []  # internal task state (file order)
        self._current_task_row = -1  # which task the detail form is bound to
        # Async-fetched picker choices (populated by load_job_data's /scripts + /jobs GETs).
        self._script_choices: list[str] = []
        self._job_choices: list[str] = []

        outer = QVBoxLayout(self)

        self._error_label = QLabel("")
        self._error_label.setStyleSheet("color: #ff6b6b;")
        self._error_label.setVisible(False)
        outer.addWidget(self._error_label)

        # ---- Header form ----
        form = QFormLayout()
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("job-name (alphanumeric, hyphens, underscores)")
        form.addRow("Name:", self._name_edit)
        self._env_file_edit = QLineEdit()
        form.addRow("Env file:", self._env_file_edit)
        # D-SCHEDULE: no Schedule field. `schedule` is managed by the Scheduler/triggers
        # panel + CLI; it is preserved on save via self._preserved (D-PRESERVE), never edited here.
        self._retry_attempts_spin = QSpinBox()
        self._retry_attempts_spin.setRange(1, 100)
        self._retry_attempts_spin.setValue(3)
        form.addRow("Retry attempts:", self._retry_attempts_spin)
        self._retry_delay_spin = QSpinBox()
        self._retry_delay_spin.setRange(0, 86400)
        self._retry_delay_spin.setValue(30)
        form.addRow("Retry delay (s):", self._retry_delay_spin)
        outer.addLayout(form)

        # ---- Task list + reorder buttons ----
        outer.addWidget(QLabel("Tasks:"))
        list_row = QHBoxLayout()
        self._task_list = QListWidget()
        self._task_list.currentRowChanged.connect(self._bind_task_detail)
        list_row.addWidget(self._task_list, stretch=1)

        list_btns = QVBoxLayout()
        self._add_task_btn = QPushButton("Add")
        self._add_task_btn.clicked.connect(self._add_task)
        self._remove_task_btn = QPushButton("Remove")
        self._remove_task_btn.clicked.connect(self._remove_task)
        self._up_task_btn = QPushButton("Up")
        self._up_task_btn.clicked.connect(self._move_task_up)
        self._down_task_btn = QPushButton("Down")
        self._down_task_btn.clicked.connect(self._move_task_down)
        for b in (self._add_task_btn, self._remove_task_btn, self._up_task_btn, self._down_task_btn):
            list_btns.addWidget(b)
        list_btns.addStretch()
        list_row.addLayout(list_btns)
        outer.addLayout(list_row, stretch=1)

        # ---- Per-task detail form ----
        self._task_detail = self._build_task_detail()
        outer.addWidget(self._task_detail)

        # ---- Action buttons ----
        btn_row = QHBoxLayout()
        self._save_btn = QPushButton("Save")
        self._save_btn.clicked.connect(self._on_save)
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self._on_cancel)
        self._delete_btn = QPushButton("Delete")
        self._delete_btn.setStyleSheet("color: #ff6b6b;")
        self._delete_btn.clicked.connect(self._on_delete)
        btn_row.addStretch()
        btn_row.addWidget(self._save_btn)
        btn_row.addWidget(self._cancel_btn)
        btn_row.addWidget(self._delete_btn)
        outer.addLayout(btn_row)

    # ------------------------------------------------------------------
    # Per-task detail form construction
    # ------------------------------------------------------------------

    def _build_task_detail(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

        # ---- Identity (Name + Type, paired on one row) ----
        identity_gb = QGroupBox("Identity")
        identity_form = QFormLayout(identity_gb)
        self._t_name_edit = QLineEdit()
        self._t_name_edit.editingFinished.connect(self._writeback_task)
        self._t_type_combo = QComboBox()
        self._t_type_combo.addItems(["python", "job"])
        self._t_type_combo.currentTextChanged.connect(self._on_type_changed)
        id_row = QHBoxLayout()
        id_row.addWidget(self._t_name_edit, stretch=1)
        id_row.addWidget(QLabel("Type:"))
        id_row.addWidget(self._t_type_combo)
        identity_form.addRow("Task name:", id_row)
        layout.addWidget(identity_gb)

        # ---- Action (type-aware: Script combo OR Job combo) ----
        action_gb = QGroupBox("Action")
        # store the form so _update_type_visibility can toggle whole rows (label + field).
        self._action_form = QFormLayout(action_gb)
        # Script: editable, searchable combo populated async from GET /{ws}/scripts.
        self._t_script_combo = QComboBox()
        self._t_script_combo.setEditable(True)
        self._t_script_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._t_script_combo.lineEdit().setPlaceholderText("e.g. landing/landing2raw.py")
        _attach_contains_completer(self._t_script_combo)
        self._t_script_combo.currentTextChanged.connect(lambda _t: self._writeback_task())
        self._t_script_combo.editTextChanged.connect(lambda _t: self._writeback_task())
        self._action_form.addRow("Script:", self._t_script_combo)
        # Job: editable, searchable combo populated async from GET /{ws}/jobs.
        self._t_job_combo = QComboBox()
        _attach_contains_completer(self._t_job_combo)
        self._t_job_combo.currentTextChanged.connect(lambda _t: self._writeback_task())
        self._t_job_combo.editTextChanged.connect(lambda _t: self._writeback_task())
        self._action_form.addRow("Job:", self._t_job_combo)
        layout.addWidget(action_gb)

        # ---- Dependencies (match selector at top, then the depends_on rows) ----
        deps_gb = QGroupBox("Dependencies")
        deps_layout = QVBoxLayout(deps_gb)
        # `match` governs how the depends_on conditions combine → it belongs WITH them.
        match_form = QFormLayout()
        self._t_match_combo = QComboBox()
        self._t_match_combo.addItems(["all", "any"])
        self._t_match_combo.currentTextChanged.connect(lambda _t: self._writeback_task())
        match_form.addRow("Require", self._t_match_combo)
        deps_layout.addLayout(match_form)

        self._t_depends_table = QTableWidget(0, 5)
        self._t_depends_table.setHorizontalHeaderLabels(
            ["Upstream", "succeeded", "failed", "skipped", ""]
        )
        self._t_depends_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._t_depends_table.verticalHeader().setVisible(False)
        self._t_depends_table.setMaximumHeight(120)
        deps_layout.addWidget(self._t_depends_table)
        dep_btns = QHBoxLayout()
        add_dep_btn = QPushButton("+ Dependency")
        add_dep_btn.clicked.connect(self._add_depends_row)
        dep_btns.addStretch()
        dep_btns.addWidget(add_dep_btn)
        deps_layout.addLayout(dep_btns)
        layout.addWidget(deps_gb)

        # ---- Params (key / value JSON table) ----
        params_gb = QGroupBox("Params")
        params_layout = QVBoxLayout(params_gb)
        params_layout.addWidget(
            QLabel("Params (value parsed as JSON; e.g. 5, true, [\"a\",\"b\"]):")
        )
        self._t_params_table = QTableWidget(0, 3)
        self._t_params_table.setHorizontalHeaderLabels(["Key", "Value (JSON)", ""])
        self._t_params_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._t_params_table.verticalHeader().setVisible(False)
        self._t_params_table.setMaximumHeight(120)
        self._t_params_table.cellChanged.connect(lambda *_a: self._writeback_task())
        params_layout.addWidget(self._t_params_table)
        params_btns = QHBoxLayout()
        add_param_btn = QPushButton("+ Param")
        add_param_btn.clicked.connect(self._add_param_row)
        params_btns.addStretch()
        params_btns.addWidget(add_param_btn)
        params_layout.addLayout(params_btns)
        layout.addWidget(params_gb)

        # ---- Advanced (Active + per-task retry) ----
        advanced_gb = QGroupBox("Advanced")
        advanced_form = QFormLayout(advanced_gb)
        self._t_active_check = QCheckBox("Active")
        self._t_active_check.setChecked(True)
        self._t_active_check.stateChanged.connect(lambda _s: self._writeback_task())
        advanced_form.addRow("", self._t_active_check)
        self._t_retry_attempts_spin = QSpinBox()
        self._t_retry_attempts_spin.setRange(1, 100)
        self._t_retry_attempts_spin.setValue(3)
        self._t_retry_attempts_spin.valueChanged.connect(lambda _v: self._writeback_task())
        advanced_form.addRow("Retry attempts:", self._t_retry_attempts_spin)
        self._t_retry_delay_spin = QSpinBox()
        self._t_retry_delay_spin.setRange(0, 86400)
        self._t_retry_delay_spin.setValue(30)
        self._t_retry_delay_spin.valueChanged.connect(lambda _v: self._writeback_task())
        advanced_form.addRow("Retry delay (s):", self._t_retry_delay_spin)
        layout.addWidget(advanced_gb)

        return page

    # ------------------------------------------------------------------
    # Load / payload (D-PRESERVE)
    # ------------------------------------------------------------------

    def load_job_data(self, ws: str, job: Optional[dict]) -> None:
        self._current_workspace = ws
        self._error_label.setVisible(False)
        self._current_task_row = -1
        # Stash unshown keys verbatim so _build_job_payload can re-emit them (D-PRESERVE).
        self._preserved = {k: job[k] for k in self._UNSHOWN_KEYS if job and k in job}
        if job is None:
            self._editing_job_name = None
            self._name_edit.clear()
            self._name_edit.setReadOnly(False)
            self._env_file_edit.clear()
            self._retry_attempts_spin.setValue(3)
            self._retry_delay_spin.setValue(30)
            self._tasks = []
            self._delete_btn.setVisible(False)
        else:
            self._editing_job_name = job.get("name", "")
            self._name_edit.setText(self._editing_job_name)
            self._name_edit.setReadOnly(True)  # D1
            self._env_file_edit.setText(job.get("env_file") or "")
            retry = job.get("retry") or {}
            self._retry_attempts_spin.setValue(int(retry.get("attempts", 3)))
            self._retry_delay_spin.setValue(int(retry.get("delay_seconds", 30)))
            self._tasks = [dict(t) for t in job.get("tasks", [])]
            self._delete_btn.setVisible(True)
        self._refresh_task_list()
        self._set_busy(False)
        self._fetch_picker_choices(ws)

    # ------------------------------------------------------------------
    # Async picker population (GET /{ws}/scripts + GET /{ws}/jobs)
    # ------------------------------------------------------------------

    def _fetch_picker_choices(self, ws: str) -> None:
        """Fire async GETs for the script + job picker item lists. Free-text is always
        preserved on each reply (_repopulate_preserving_text), so a reply that lands while
        the user has typed a not-yet-created script/job never wipes their input."""
        base = f"http://127.0.0.1:{self._port}/api/workspaces/{ws}"
        scripts_reply = self._nam.get(QNetworkRequest(QUrl(f"{base}/scripts")))
        scripts_reply.finished.connect(lambda: self._on_scripts_fetched(scripts_reply))
        jobs_reply = self._nam.get(QNetworkRequest(QUrl(f"{base}/jobs")))
        jobs_reply.finished.connect(lambda: self._on_jobs_fetched(jobs_reply))

    def _on_scripts_fetched(self, reply) -> None:
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        raw = bytes(reply.readAll()).decode(errors="replace")
        reply.deleteLater()
        if status is None or status >= 400:
            return  # picker stays free-text only; not fatal
        try:
            self._script_choices = list(json.loads(raw).get("scripts", []))
        except Exception:
            return
        _repopulate_preserving_text(self._t_script_combo, self._script_choices)

    def _on_jobs_fetched(self, reply) -> None:
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        raw = bytes(reply.readAll()).decode(errors="replace")
        reply.deleteLater()
        if status is None or status >= 400:
            return
        try:
            data = json.loads(raw)
        except Exception:
            return
        # The jobs list endpoint returns [{name, parameters}] — use the names, and exclude
        # the job currently being edited (a job may not depend on itself via type=job).
        names = [j.get("name", "") for j in data if isinstance(j, dict)]
        self._job_choices = [n for n in names if n and n != self._editing_job_name]
        _repopulate_preserving_text(self._t_job_combo, self._job_choices)

    def set_on_saved(self, callback) -> None:
        self._on_saved_cb = callback

    def _build_job_payload(self) -> dict:
        # Flush the currently-bound detail form into self._tasks first.
        self._writeback_task()
        tasks_out = []
        for t in self._tasks:
            tasks_out.append(self._normalize_task_for_payload(t))
        payload = {
            "name": self._name_edit.text().strip(),
            "tasks": tasks_out,
        }
        env = self._env_file_edit.text().strip()
        if env:
            payload["env_file"] = env
        payload["retry"] = {
            "attempts": self._retry_attempts_spin.value(),
            "delay_seconds": self._retry_delay_spin.value(),
        }
        # Fix #1 / D-PRESERVE: re-emit unshown job-level keys (parameters, schedule) verbatim.
        # `if k not in payload` guards against ever shadowing a form-set key.
        payload.update({k: v for k, v in self._preserved.items() if k not in payload})
        return payload

    def _normalize_task_for_payload(self, task: dict) -> dict:
        """Copy a task dict for the wire payload, rejecting an empty depends_on `when` (Fix #10)."""
        out = dict(task)
        deps = []
        for edge in out.get("depends_on", []) or []:
            when = list(edge.get("when", []) or [])
            if not when:
                # Empty when would silently revert to the model default [succeeded].
                raise ValueError(
                    f"Task '{out.get('name', '')}' depends on '{edge.get('task', '')}' "
                    "with no 'when' condition selected — choose at least one."
                )
            deps.append({"task": edge.get("task", ""), "when": when})
        out["depends_on"] = deps
        return out

    # ------------------------------------------------------------------
    # Task list mutation
    # ------------------------------------------------------------------

    def _blank_task(self) -> dict:
        return {"name": "", "type": "python", "script": "", "job": None,
                "params": {}, "depends_on": [], "match": "all",
                "retry": {"attempts": 3, "delay_seconds": 30}, "active": True}

    def _refresh_task_list(self) -> None:
        self._task_list.blockSignals(True)
        self._task_list.clear()
        for t in self._tasks:
            self._task_list.addItem(t.get("name") or "(unnamed)")
        self._task_list.blockSignals(False)

    def _add_task(self) -> None:
        self._tasks.append(self._blank_task())
        self._refresh_task_list()
        self._task_list.setCurrentRow(len(self._tasks) - 1)

    def _remove_task(self) -> None:
        row = self._task_list.currentRow()
        if 0 <= row < len(self._tasks):
            self._tasks.pop(row)
            self._current_task_row = -1
            self._refresh_task_list()

    def _move_task_up(self) -> None:
        row = self._task_list.currentRow()
        if row > 0:
            self._tasks[row - 1], self._tasks[row] = self._tasks[row], self._tasks[row - 1]
            self._refresh_task_list()
            self._task_list.setCurrentRow(row - 1)

    def _move_task_down(self) -> None:
        row = self._task_list.currentRow()
        if 0 <= row < len(self._tasks) - 1:
            self._tasks[row + 1], self._tasks[row] = self._tasks[row], self._tasks[row + 1]
            self._refresh_task_list()
            self._task_list.setCurrentRow(row + 1)

    # ------------------------------------------------------------------
    # Detail form <-> self._tasks[row] binding
    # ------------------------------------------------------------------

    def _bind_task_detail(self, row: int) -> None:
        """Populate the detail form widgets from self._tasks[row]."""
        self._current_task_row = -1  # suppress write-back while populating
        if not (0 <= row < len(self._tasks)):
            self._task_detail.setEnabled(False)
            return
        self._task_detail.setEnabled(True)
        task = self._tasks[row]

        self._t_name_edit.setText(task.get("name") or "")
        ttype = task.get("type") or ("job" if task.get("job") else "python")
        self._t_type_combo.setCurrentText(ttype if ttype in ("python", "job") else "python")
        self._t_script_combo.setCurrentText(task.get("script") or "")
        self._t_job_combo.setCurrentText(task.get("job") or "")
        self._t_match_combo.setCurrentText(task.get("match") or "all")
        self._t_active_check.setChecked(bool(task.get("active", True)))
        retry = task.get("retry") or {}
        self._t_retry_attempts_spin.setValue(int(retry.get("attempts", 3)))
        self._t_retry_delay_spin.setValue(int(retry.get("delay_seconds", 30)))
        self._update_type_visibility(self._t_type_combo.currentText())

        # Params grid
        self._t_params_table.blockSignals(True)
        self._t_params_table.setRowCount(0)
        for key, value in (task.get("params") or {}).items():
            self._insert_param_row(key, value)
        self._t_params_table.blockSignals(False)

        # depends_on grid
        self._t_depends_table.setRowCount(0)
        for edge in task.get("depends_on") or []:
            self._insert_depends_row(edge.get("task", ""), edge.get("when") or ["succeeded"])

        self._current_task_row = row

    def _on_type_changed(self, ttype: str) -> None:
        self._update_type_visibility(ttype)
        self._writeback_task()

    def _update_type_visibility(self, ttype: str) -> None:
        """Type-aware: SHOW/HIDE the relevant Action row (label + field), not just enable/disable.
        type=python -> Script row visible, Job row hidden; type=job -> the reverse."""
        is_job = ttype == "job"
        # setRowVisible toggles the whole form row (label + field widget) together.
        self._action_form.setRowVisible(self._t_script_combo, not is_job)
        self._action_form.setRowVisible(self._t_job_combo, is_job)

    def _writeback_task(self) -> None:
        """Sync the detail form back into self._tasks[self._current_task_row]."""
        row = self._current_task_row
        if not (0 <= row < len(self._tasks)):
            return
        task = self._tasks[row]
        task["name"] = self._t_name_edit.text().strip()
        ttype = self._t_type_combo.currentText()
        task["type"] = ttype
        if ttype == "job":
            task["job"] = self._t_job_combo.currentText().strip() or None
            task["script"] = None
        else:
            task["script"] = self._t_script_combo.currentText().strip() or None
            task["job"] = None
        task["match"] = self._t_match_combo.currentText()
        task["active"] = self._t_active_check.isChecked()
        task["retry"] = {
            "attempts": self._t_retry_attempts_spin.value(),
            "delay_seconds": self._t_retry_delay_spin.value(),
        }
        task["params"] = self._read_params_table()
        task["depends_on"] = self._read_depends_table()
        # Reflect a name edit in the list label.
        self._task_list.blockSignals(True)
        item = self._task_list.item(row)
        if item is not None:
            item.setText(task["name"] or "(unnamed)")
        self._task_list.blockSignals(False)

    # ------------------------------------------------------------------
    # Params grid (Fix #2 — typed value preservation)
    # ------------------------------------------------------------------

    def _insert_param_row(self, key: str, value) -> None:
        row = self._t_params_table.rowCount()
        self._t_params_table.insertRow(row)
        self._t_params_table.setItem(row, 0, QTableWidgetItem(str(key)))
        # Render non-string values as JSON so they survive the round-trip; strings as-is.
        if isinstance(value, str):
            shown = value
        else:
            try:
                shown = json.dumps(value)
            except (TypeError, ValueError):
                shown = str(value)
        self._t_params_table.setItem(row, 1, QTableWidgetItem(shown))
        rm = QPushButton("✕")
        rm.setFixedWidth(28)
        rm.clicked.connect(lambda _checked=False: self._remove_param_row(rm))
        self._t_params_table.setCellWidget(row, 2, rm)

    def _add_param_row(self) -> None:
        self._t_params_table.blockSignals(True)
        self._insert_param_row("", "")
        self._t_params_table.blockSignals(False)

    def _remove_param_row(self, button) -> None:
        for row in range(self._t_params_table.rowCount()):
            if self._t_params_table.cellWidget(row, 2) is button:
                self._t_params_table.removeRow(row)
                self._writeback_task()
                return

    def _read_params_table(self) -> dict:
        """Read the params grid, JSON-parsing each value cell with string fallback (Fix #2)."""
        out: dict = {}
        for row in range(self._t_params_table.rowCount()):
            key_item = self._t_params_table.item(row, 0)
            val_item = self._t_params_table.item(row, 1)
            key = key_item.text().strip() if key_item else ""
            if not key:
                continue
            raw = val_item.text() if val_item else ""
            try:
                out[key] = json.loads(raw)
            except (ValueError, TypeError):
                out[key] = raw
        return out

    # ------------------------------------------------------------------
    # depends_on grid
    # ------------------------------------------------------------------

    def _insert_depends_row(self, upstream: str, when: list) -> None:
        row = self._t_depends_table.rowCount()
        self._t_depends_table.insertRow(row)
        combo = QComboBox()
        # Offer the other task names as choices.
        for t in self._tasks:
            name = t.get("name")
            if name:
                combo.addItem(name)
        # Same searchable CONTAINS/case-insensitive completer as the script/job pickers.
        _attach_contains_completer(combo)
        combo.setCurrentText(upstream)
        combo.currentTextChanged.connect(lambda _t: self._writeback_task())
        self._t_depends_table.setCellWidget(row, 0, combo)
        for col, cond in enumerate(_WHEN_CONDITIONS, start=1):
            chk = QCheckBox()
            chk.setChecked(cond in when)
            chk.stateChanged.connect(lambda _s: self._writeback_task())
            self._t_depends_table.setCellWidget(row, col, chk)
        rm = QPushButton("✕")
        rm.setFixedWidth(28)
        rm.clicked.connect(lambda _checked=False: self._remove_depends_row(rm))
        self._t_depends_table.setCellWidget(row, 4, rm)

    def _add_depends_row(self) -> None:
        # Default a fresh edge to ["succeeded"] (Fix #10 — never start with an empty when).
        self._insert_depends_row("", ["succeeded"])
        self._writeback_task()

    def _remove_depends_row(self, button) -> None:
        for row in range(self._t_depends_table.rowCount()):
            if self._t_depends_table.cellWidget(row, 4) is button:
                self._t_depends_table.removeRow(row)
                self._writeback_task()
                return

    def _read_depends_table(self) -> list:
        edges = []
        for row in range(self._t_depends_table.rowCount()):
            combo = self._t_depends_table.cellWidget(row, 0)
            upstream = combo.currentText().strip() if combo else ""
            when = []
            for col, cond in enumerate(_WHEN_CONDITIONS, start=1):
                chk = self._t_depends_table.cellWidget(row, col)
                if chk is not None and chk.isChecked():
                    when.append(cond)
            if not upstream:
                continue
            edges.append({"task": upstream, "when": when})
        return edges

    # ------------------------------------------------------------------
    # Busy guard (Fix #11)
    # ------------------------------------------------------------------

    def _set_busy(self, busy: bool) -> None:
        """Disable Save/Delete while an HTTP request is in flight (double-click guard)."""
        self._save_btn.setEnabled(not busy)
        self._delete_btn.setEnabled(not busy)

    # ------------------------------------------------------------------
    # Save (POST/PUT)
    # ------------------------------------------------------------------

    def _on_save(self) -> None:
        if not self._save_btn.isEnabled():
            return  # request already in flight
        try:
            payload = self._build_job_payload()
        except ValueError as exc:
            self._error_label.setText(f"Error: {exc}")
            self._error_label.setVisible(True)
            return
        body = json.dumps(payload).encode()
        self._set_busy(True)
        if self._editing_job_name is None:
            url = QUrl(f"http://127.0.0.1:{self._port}/api/workspaces/{self._current_workspace}/jobs")
            req = QNetworkRequest(url)
            req.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
            reply = self._nam.post(req, QByteArray(body))
        else:
            name = self._editing_job_name
            url = QUrl(f"http://127.0.0.1:{self._port}/api/workspaces/{self._current_workspace}/jobs/{name}")
            req = QNetworkRequest(url)
            req.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
            reply = self._nam.put(req, QByteArray(body))
        reply.finished.connect(lambda: self._on_save_finished(reply))

    def _on_save_finished(self, reply) -> None:
        self._set_busy(False)  # re-enable buttons (Fix #11)
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        raw = bytes(reply.readAll()).decode(errors="replace")
        reply.deleteLater()
        if status is None or status >= 400:
            try:
                detail = json.loads(raw).get("detail", raw)
            except Exception:
                detail = raw or f"HTTP {status}"
            self._error_label.setText(f"Error: {detail}")
            self._error_label.setVisible(True)
            return
        try:
            warnings = json.loads(raw).get("warnings", [])
        except Exception:
            warnings = []
        if warnings:
            QMessageBox.warning(self, "Job saved — action needed", "\n\n".join(warnings))
        if self._on_saved_cb:
            self._on_saved_cb()

    def _on_cancel(self) -> None:
        if self._on_saved_cb:
            self._on_saved_cb()

    # ------------------------------------------------------------------
    # Delete with reference confirmation (D2)
    # ------------------------------------------------------------------

    def _on_delete(self) -> None:
        if not self._editing_job_name or not self._delete_btn.isEnabled():
            return
        self._set_busy(True)  # Fix #11: guard during the references fetch + delete
        name = self._editing_job_name
        url = QUrl(
            f"http://127.0.0.1:{self._port}/api/workspaces/{self._current_workspace}/jobs/{name}/references"
        )
        reply = self._nam.get(QNetworkRequest(url))

        def _done():
            try:
                refs = json.loads(bytes(reply.readAll()).decode())
            except Exception:
                refs = {"triggers": [], "tasks": [], "schedule": False}
            finally:
                reply.deleteLater()
            self._confirm_and_delete(refs)

        reply.finished.connect(_done)

    def _confirm_and_delete(self, refs: dict) -> None:
        lines = []
        if refs.get("triggers"):
            lines.append("Triggers: " + ", ".join(refs["triggers"]))
        if refs.get("tasks"):
            lines.append("Tasks in other jobs: " + ", ".join(refs["tasks"]))
        if refs.get("schedule"):
            lines.append("A schedule row exists for this job.")
        if lines:
            msg = ("This job is referenced by:\n\n" + "\n".join(lines)
                   + "\n\nDelete anyway? References will NOT be cascaded.")
            answer = QMessageBox.question(self, "Confirm delete", msg)
            if answer != QMessageBox.StandardButton.Yes:
                self._set_busy(False)  # Fix #11: cancelled — re-enable buttons
                return
        name = self._editing_job_name
        url = QUrl(f"http://127.0.0.1:{self._port}/api/workspaces/{self._current_workspace}/jobs/{name}")
        reply = self._nam.deleteResource(QNetworkRequest(url))
        reply.finished.connect(lambda: self._on_delete_finished(reply))

    def _on_delete_finished(self, reply) -> None:
        self._set_busy(False)  # Fix #11
        reply.deleteLater()
        if self._on_saved_cb:
            self._on_saved_cb()
