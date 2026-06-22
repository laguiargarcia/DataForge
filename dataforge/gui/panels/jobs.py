"""Jobs panel — Phase 1.4 live job monitoring."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from PySide6.QtCore import QSettings, QTimer, QUrl
from PySide6.QtGui import QColor, QTextCharFormat
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from dataforge.gui.panels.job_editor import JobEditorPanel

SETTINGS_ORG = "DataForge"
SETTINGS_APP = "DataForge"
KEY_LAST_WS = "jobs/last_workspace"

# Log line color pairs (ERROR / WARN)
_ERROR_FG = "#ff6b6b"
_ERROR_BG = "#5c1a1a"
_WARN_FG = "#ffd166"
_WARN_BG = "#4a3a00"


def _append_log_line(text_edit: QTextEdit, line: str) -> None:
    """Insert one log line into *text_edit* with ERROR/WARN coloring.

    Uses QTextCharFormat + QTextCursor.insertText — never HTML — to prevent
    XSS via log output (T-1.4-09 / Critical Note 5).
    """
    fmt = QTextCharFormat()
    lower = line.lower()
    if "error" in lower:
        fmt.setBackground(QColor(_ERROR_BG))
        fmt.setForeground(QColor(_ERROR_FG))
    elif "warn" in lower:
        fmt.setBackground(QColor(_WARN_BG))
        fmt.setForeground(QColor(_WARN_FG))
    cursor = text_edit.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    cursor.insertText(line + "\n", fmt)
    text_edit.setTextCursor(cursor)


class Panel(QWidget):
    PAGE_LIST = 0
    PAGE_DETAIL = 1
    PAGE_HISTORY = 2
    PAGE_EDITOR = 3

    def __init__(self) -> None:
        super().__init__()
        self._port = int(os.environ.get("DATAFORGE_PORT", 8000))
        self._nam = QNetworkAccessManager(self)
        self._sse_reply = None
        self._sse_buffer = b""
        self._run_sse_reply = None   # per-run SSE reply (distinct from workspace SSE)
        self._run_sse_buffer = b""
        self._current_workspace: str | None = None
        self._job_rows: dict[str, QLabel] = {}
        self._history_offset = 0
        self._history_limit = 20
        self._current_run_id: str | None = None

        # Per-tab auto-scroll state: tab_name -> bool
        self._tab_auto_scroll: dict[str, bool] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(8, 8, 8, 8)

        self._back_btn = QPushButton("← Back")
        self._back_btn.setVisible(False)
        self._back_btn.clicked.connect(self._go_back)
        header_layout.addWidget(self._back_btn)

        self._breadcrumb = QLabel("Jobs")
        header_layout.addWidget(self._breadcrumb)
        header_layout.addStretch()

        outer.addWidget(header)

        self._stack = QStackedWidget()

        # PAGE_LIST = 0
        self._list_page = QWidget()
        list_outer = QVBoxLayout(self._list_page)
        list_outer.setContentsMargins(0, 0, 0, 0)
        list_outer.setSpacing(0)
        list_header = QWidget()
        list_header_layout = QHBoxLayout(list_header)
        list_header_layout.setContentsMargins(8, 4, 8, 4)
        list_header_layout.addStretch()
        new_btn = QPushButton("New Job")
        new_btn.clicked.connect(lambda: self._open_editor(None))
        list_header_layout.addWidget(new_btn)
        history_btn = QPushButton("History")
        history_btn.clicked.connect(self._open_history)
        list_header_layout.addWidget(history_btn)
        list_outer.addWidget(list_header)
        self._list_layout = QVBoxLayout()
        list_outer.addLayout(self._list_layout)
        self._stack.addWidget(self._list_page)

        # PAGE_DETAIL = 1 — run detail with per-task QTabWidget + SSE consumer
        self._detail_page = self._build_detail_page()
        self._stack.addWidget(self._detail_page)

        # PAGE_HISTORY = 2 — run history table with pagination
        self._history_page = self._build_history_page()
        self._stack.addWidget(self._history_page)

        # PAGE_EDITOR = 3 — job & task editor form (own module)
        self._editor = JobEditorPanel()
        self._editor.set_on_saved(self._on_editor_done)
        self._stack.addWidget(self._editor)

        outer.addWidget(self._stack, stretch=1)

    # ------------------------------------------------------------------
    # Run detail page (PAGE_DETAIL = 1)
    # ------------------------------------------------------------------

    def _build_detail_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        # Header row: run id label + status badge
        header_row = QWidget()
        header_layout = QHBoxLayout(header_row)
        header_layout.setContentsMargins(0, 0, 0, 0)
        self._detail_run_label = QLabel("—")
        self._detail_status_badge = QLabel("")
        self._detail_status_badge.setStyleSheet(
            "background-color: #555555; color: white; padding: 2px 8px; border-radius: 4px;"
        )
        header_layout.addWidget(self._detail_run_label)
        header_layout.addStretch()
        header_layout.addWidget(self._detail_status_badge)
        layout.addWidget(header_row)

        # QTabWidget — one tab per task (filled dynamically on SSE snapshots)
        self._task_tabs = QTabWidget()
        layout.addWidget(self._task_tabs, stretch=1)

        return page

    def _load_run_detail(self, run_id: str) -> None:
        """Open per-run SSE stream and start populating the RunDetailView."""
        # Abort any existing per-run SSE connection (Pitfall 5 — prevent leaked connections)
        if self._run_sse_reply is not None:
            try:
                self._run_sse_reply.readyRead.disconnect(self._on_run_sse_data)
                self._run_sse_reply.errorOccurred.disconnect(self._on_run_sse_error)
                self._run_sse_reply.finished.disconnect(self._on_run_sse_finished)
            except RuntimeError:
                pass
            self._run_sse_reply.abort()
            self._run_sse_reply = None

        # Reset UI
        self._task_tabs.clear()
        self._tab_auto_scroll.clear()
        self._detail_run_label.setText(f"Run #{run_id[:8]}")
        self._detail_status_badge.setText("…")
        self._detail_status_badge.setStyleSheet(
            "background-color: #555555; color: white; padding: 2px 8px; border-radius: 4px;"
        )

        # Open SSE connection
        url = QUrl(f"http://127.0.0.1:{self._port}/api/executions/{run_id}/stream")
        req = QNetworkRequest(url)
        self._run_sse_buffer = b""
        self._run_sse_reply = self._nam.get(req)
        self._run_sse_reply.readyRead.connect(self._on_run_sse_data)
        self._run_sse_reply.errorOccurred.connect(self._on_run_sse_error)
        self._run_sse_reply.finished.connect(self._on_run_sse_finished)

    def _on_run_sse_data(self) -> None:
        """Accumulate bytes; parse complete \n\n-terminated frames (Pitfall 2)."""
        if self._run_sse_reply is None:
            return
        self._run_sse_buffer += bytes(self._run_sse_reply.readAll())
        while b"\n\n" in self._run_sse_buffer:
            frame, self._run_sse_buffer = self._run_sse_buffer.split(b"\n\n", 1)
            for line in frame.splitlines():
                if line.startswith(b"data:"):
                    payload = line[len(b"data:"):].strip()
                    try:
                        self._handle_run_snapshot(json.loads(payload))
                    except Exception:
                        pass

    def _on_run_sse_error(self, error) -> None:
        self._run_sse_reply = None

    def _on_run_sse_finished(self) -> None:
        self._run_sse_reply = None

    def _handle_run_snapshot(self, snapshot: dict) -> None:
        """Apply a full SSE snapshot: update overall status + per-task tabs/logs."""
        run_status = snapshot.get("status", "")
        tasks = snapshot.get("tasks", [])

        # Update header badge
        self._detail_status_badge.setText(run_status)
        self._detail_status_badge.setStyleSheet(
            f"background-color: {self._badge_color(run_status)};"
            " color: white; padding: 2px 8px; border-radius: 4px;"
        )

        # Update per-task tabs — create missing tabs; do not duplicate
        existing_tabs: dict[str, int] = {}
        for i in range(self._task_tabs.count()):
            existing_tabs[self._task_tabs.tabText(i)] = i

        for task in tasks:
            task_name = task.get("name") or task.get("task") or "(unnamed)"
            task_status = task.get("status") or ""
            task_output = task.get("output") or ""

            tab_label = f"{task_name} [{task_status}]" if task_status else task_name

            if task_name not in existing_tabs:
                # Create new tab with log QTextEdit
                log_pane = self._make_log_pane(task_name)
                self._task_tabs.addTab(log_pane, tab_label)
                existing_tabs[task_name] = self._task_tabs.count() - 1
                self._tab_auto_scroll[task_name] = True
            else:
                # Update tab label
                idx = existing_tabs[task_name]
                self._task_tabs.setTabText(idx, tab_label)

            # Replace log pane contents (full snapshot replace per D-07)
            idx = existing_tabs[task_name]
            log_pane = self._task_tabs.widget(idx)
            if isinstance(log_pane, QTextEdit) and task_output:
                log_pane.clear()
                for log_line in task_output.splitlines():
                    _append_log_line(log_pane, log_line)
                # Auto-scroll after appending
                if self._tab_auto_scroll.get(task_name, True):
                    sb = log_pane.verticalScrollBar()
                    sb.setValue(sb.maximum())

        # Close SSE on terminal status (T-1.4-10)
        if run_status in ("success", "failed"):
            if self._run_sse_reply is not None:
                try:
                    self._run_sse_reply.readyRead.disconnect(self._on_run_sse_data)
                    self._run_sse_reply.errorOccurred.disconnect(self._on_run_sse_error)
                    self._run_sse_reply.finished.disconnect(self._on_run_sse_finished)
                except RuntimeError:
                    pass
                self._run_sse_reply.abort()
                self._run_sse_reply = None

    def _make_log_pane(self, task_name: str) -> QTextEdit:
        """Create a dark monospace read-only log QTextEdit for one task."""
        pane = QTextEdit()
        pane.setReadOnly(True)
        pane.setAcceptRichText(False)  # XSS guard (T-1.4-09 / Critical Note 5)
        pane.setStyleSheet(
            "background-color: #1e1e1e; color: #d4d4d4;"
            " font-family: monospace; font-size: 12px;"
        )

        # Auto-scroll tracking — connect scrollbar value changes
        scrollbar = pane.verticalScrollBar()
        scrollbar.valueChanged.connect(
            lambda value, tn=task_name, sb=scrollbar: self._on_log_scroll_changed(tn, value, sb)
        )

        return pane

    def _on_log_scroll_changed(self, task_name: str, value: int, scrollbar) -> None:
        """Pause auto-scroll when user scrolls up; resume when back at bottom."""
        self._tab_auto_scroll[task_name] = (value == scrollbar.maximum())

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _navigate_to(self, page: int, breadcrumb: str = "Jobs") -> None:
        self._breadcrumb.setText(breadcrumb)
        self._back_btn.setVisible(page != self.PAGE_LIST)
        self._stack.setCurrentIndex(page)
        if page == self.PAGE_DETAIL and self._current_run_id:
            self._load_run_detail(self._current_run_id)

    def _go_back(self) -> None:
        self._navigate_to(self.PAGE_LIST, "Jobs")

    # ------------------------------------------------------------------
    # Run history
    # ------------------------------------------------------------------

    def _build_history_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)

        self._history_table = QTableWidget(0, 5)
        self._history_table.setHorizontalHeaderLabels(["Job", "Trigger", "Status", "Started", "Duration"])
        self._history_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._history_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._history_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._history_table.verticalHeader().setVisible(False)
        self._history_table.cellClicked.connect(self._on_history_cell_clicked)
        layout.addWidget(self._history_table)

        # Pagination controls
        pag = QWidget()
        pag_layout = QHBoxLayout(pag)
        pag_layout.setContentsMargins(0, 4, 0, 0)
        self._prev_btn = QPushButton("← Prev")
        self._prev_btn.setEnabled(False)
        self._prev_btn.clicked.connect(self._history_prev)
        self._next_btn = QPushButton("Next →")
        self._next_btn.clicked.connect(self._history_next)
        pag_layout.addStretch()
        pag_layout.addWidget(self._prev_btn)
        pag_layout.addWidget(self._next_btn)
        layout.addWidget(pag)

        return page

    def _load_run_history(self, workspace: str, offset: int = 0, limit: int = 20) -> None:
        self._history_offset = offset
        # Integer-bound the pagination params (T-1.4-07)
        offset = max(0, int(offset))
        limit = max(1, min(int(limit), 100))
        url = QUrl(
            f"http://127.0.0.1:{self._port}"
            f"/api/workspaces/{workspace}/executions"
            f"?offset={offset}&limit={limit}"
        )
        req = QNetworkRequest(url)
        reply = self._nam.get(req)
        reply.finished.connect(lambda: self._on_history_loaded(reply, workspace))

    def _on_history_loaded(self, reply, workspace: str) -> None:
        try:
            data = json.loads(bytes(reply.readAll()).decode())
        except Exception:
            data = []
        finally:
            reply.deleteLater()

        if isinstance(data, dict):
            records = data.get("executions", data.get("items", []))
        else:
            records = data if isinstance(data, list) else []

        # Sort newest-first by started_at (client-side in case API doesn't guarantee order)
        def _start_key(r: dict):
            ts = r.get("started_at") or r.get("start_time") or ""
            return ts

        records = sorted(records, key=_start_key, reverse=True)
        self._history_records = records

        self._history_table.setRowCount(0)
        for i, rec in enumerate(records):
            self._history_table.insertRow(i)

            job_name = rec.get("job_name") or rec.get("job") or ""
            status = rec.get("status") or ""
            started_at = rec.get("started_at") or rec.get("start_time") or ""
            finished_at = rec.get("finished_at") or rec.get("end_time") or ""
            trigger = rec.get("trigger") or "manual"
            trigger_label = "Manual" if trigger == "manual" else trigger

            job_item = QTableWidgetItem(job_name)
            trigger_item = QTableWidgetItem(trigger_label)
            status_item = QTableWidgetItem(status)
            status_item.setForeground(QColor(self._badge_color(status)))
            started_item = QTableWidgetItem(self._relative_time(started_at))
            duration_item = QTableWidgetItem(self._format_duration(started_at, finished_at, status))

            for col, item in enumerate([job_item, trigger_item, status_item, started_item, duration_item]):
                item.setFlags(item.flags() & ~item.flags().__class__.ItemIsEditable)
                self._history_table.setItem(i, col, item)

        # Pagination control state
        self._prev_btn.setEnabled(self._history_offset > 0)
        self._next_btn.setEnabled(len(records) >= self._history_limit)

    def _history_prev(self) -> None:
        if not self._current_workspace:
            return
        new_offset = max(0, self._history_offset - self._history_limit)
        self._load_run_history(self._current_workspace, offset=new_offset, limit=self._history_limit)

    def _history_next(self) -> None:
        if not self._current_workspace:
            return
        new_offset = self._history_offset + self._history_limit
        self._load_run_history(self._current_workspace, offset=new_offset, limit=self._history_limit)

    def _on_history_cell_clicked(self, row: int, col: int) -> None:
        if not hasattr(self, "_history_records") or row >= len(self._history_records):
            return
        self._open_run_detail(self._history_records[row])

    def _open_run_detail(self, run_record: dict) -> None:
        run_id = str(run_record.get("id") or run_record.get("run_id") or "")
        self._current_run_id = run_id
        short_id = run_id[:8] if run_id else "?"
        ws = self._current_workspace or ""
        job = run_record.get("job_name") or run_record.get("job") or ""
        breadcrumb = f"Jobs › {ws}/{job} › Run #{short_id}"
        self._navigate_to(self.PAGE_DETAIL, breadcrumb)

    @staticmethod
    def _relative_time(ts: str) -> str:
        if not ts:
            return "—"
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            diff = int((now - dt).total_seconds())
            if diff < 60:
                return f"{diff}s ago"
            if diff < 3600:
                return f"{diff // 60} min ago"
            if diff < 86400:
                return f"{diff // 3600} h ago"
            return f"{diff // 86400} d ago"
        except Exception:
            return ts

    @staticmethod
    def _format_duration(started_at: str, finished_at: str, status: str) -> str:
        if status == "running" or not finished_at:
            return "running" if status == "running" else "—"
        try:
            start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            end = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
            secs = int((end - start).total_seconds())
            if secs < 60:
                return f"{secs}s"
            return f"{secs // 60}m {secs % 60}s"
        except Exception:
            return "—"

    def _open_history(self) -> None:
        if not self._current_workspace:
            return
        ws = self._current_workspace
        self._navigate_to(self.PAGE_HISTORY, f"Jobs › {ws} › History")
        self._load_run_history(ws, offset=0, limit=self._history_limit)

    def set_workspace(self, name: str) -> None:
        """Called by MainWindow.workspace_changed signal."""
        if not name:
            return
        # Fix #9: discard any in-flight editor session and return to the list, so an
        # in-flight edit cannot Save to the old workspace. The editor is re-populated
        # fresh by _open_editor on the next edit.
        self._navigate_to(self.PAGE_LIST, "Jobs")
        self._current_workspace = name
        QSettings(SETTINGS_ORG, SETTINGS_APP).setValue(KEY_LAST_WS, name)
        self._load_jobs(name)
        self._subscribe_sse(name)

    # ------------------------------------------------------------------
    # Job editor (Phase C / Task 15)
    # ------------------------------------------------------------------

    def _open_editor(self, job_name) -> None:
        if not self._current_workspace:
            return
        ws = self._current_workspace
        if job_name is None:
            self._editor.load_job_data(ws, None)
            self._navigate_to(self.PAGE_EDITOR, f"Jobs › {ws} › New Job")
            return
        url = QUrl(f"http://127.0.0.1:{self._port}/api/workspaces/{ws}/jobs/{job_name}")
        reply = self._nam.get(QNetworkRequest(url))

        def _done():
            try:
                data = json.loads(bytes(reply.readAll()).decode())
            except Exception:
                data = None
            finally:
                reply.deleteLater()
            if isinstance(data, dict) and "name" in data:
                self._editor.load_job_data(ws, data)
                self._navigate_to(self.PAGE_EDITOR, f"Jobs › {ws} › {job_name}")

        reply.finished.connect(_done)

    def _on_editor_done(self) -> None:
        self._navigate_to(self.PAGE_LIST, "Jobs")
        if self._current_workspace:
            self._load_jobs(self._current_workspace)

    def _load_jobs(self, workspace: str) -> None:
        """Fetch workspace data (including job list) from the API."""
        url = QUrl(f"http://127.0.0.1:{self._port}/api/workspaces/")
        req = QNetworkRequest(url)
        reply = self._nam.get(req)
        reply.finished.connect(lambda: self._on_jobs_loaded(reply, workspace))

    def _on_jobs_loaded(self, reply, workspace: str) -> None:
        raw = bytes(reply.readAll())
        reply.deleteLater()
        try:
            data = json.loads(raw.decode())
        except Exception:
            # Server not ready yet — silently skip (workspace was already set)
            return
        if not isinstance(data, list):
            return
        ws_data = next((w for w in data if w["name"] == workspace), None)
        if ws_data:
            self._rebuild_job_list_from_data(workspace, ws_data)

    def _rebuild_job_list_from_data(self, workspace: str, ws_data: dict) -> None:
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._job_rows.clear()

        for job in ws_data.get("jobs", []):
            job_name = job["name"]
            status = job.get("last_run_status")

            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(8, 4, 8, 4)

            row_layout.addWidget(QLabel(job_name))
            row_layout.addStretch()

            badge = QLabel(status or "Never run")
            badge.setStyleSheet(
                f"background-color: {self._badge_color(status)};"
                " color: white; padding: 2px 8px; border-radius: 4px;"
            )
            self._job_rows[job_name] = badge
            row_layout.addWidget(badge)

            edit_btn = QPushButton("Edit")
            edit_btn.clicked.connect(lambda checked=False, jn=job_name: self._open_editor(jn))
            row_layout.addWidget(edit_btn)

            run_btn = QPushButton("Run")
            run_btn.clicked.connect(lambda checked=False, jn=job_name: self._trigger_run(jn))
            row_layout.addWidget(run_btn)

            self._list_layout.addWidget(row)

        self._list_layout.addStretch()

    def _badge_color(self, status: str | None) -> str:
        if status == "running":
            return "#1a6fc4"
        if status == "success":
            return "#2d7a3a"
        if status == "failed":
            return "#a32929"
        if status == "skipped":
            return "#7a7a7a"
        return "#555555"

    def _trigger_run(self, job_name: str) -> None:
        if not self._current_workspace:
            return
        url = QUrl(
            f"http://127.0.0.1:{self._port}"
            f"/api/workspaces/{self._current_workspace}/jobs/{job_name}/run"
        )
        req = QNetworkRequest(url)
        req.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        self._nam.post(req, b"{}")
        if job_name in self._job_rows:
            badge = self._job_rows[job_name]
            badge.setText("running")
            badge.setStyleSheet(
                f"background-color: {self._badge_color('running')};"
                " color: white; padding: 2px 8px; border-radius: 4px;"
            )

    def _subscribe_sse(self, workspace: str | None) -> None:
        if not workspace:
            return
        if self._sse_reply is not None:
            # Disconnect before aborting to prevent the finished signal from
            # triggering an unwanted reconnect loop when switching workspaces.
            try:
                self._sse_reply.readyRead.disconnect(self._on_sse_data)
                self._sse_reply.errorOccurred.disconnect(self._on_sse_error)
                self._sse_reply.finished.disconnect(self._on_sse_finished)
            except RuntimeError:
                pass
            self._sse_reply.abort()
            self._sse_reply = None
        url = QUrl(f"http://127.0.0.1:{self._port}/api/workspaces/{workspace}/stream")
        req = QNetworkRequest(url)
        self._sse_buffer = b""
        self._sse_reply = self._nam.get(req)
        self._sse_reply.readyRead.connect(self._on_sse_data)
        self._sse_reply.errorOccurred.connect(self._on_sse_error)
        self._sse_reply.finished.connect(self._on_sse_finished)

    def _on_sse_data(self) -> None:
        self._sse_buffer += bytes(self._sse_reply.readAll())
        while b"\n\n" in self._sse_buffer:
            frame, self._sse_buffer = self._sse_buffer.split(b"\n\n", 1)
            for line in frame.splitlines():
                if line.startswith(b"data:"):
                    payload = line[len(b"data:"):].strip()
                    try:
                        self._handle_workspace_event(json.loads(payload))
                    except Exception:
                        pass

    def _on_sse_error(self, error) -> None:
        self._sse_reply = None
        if self._current_workspace:
            QTimer.singleShot(2000, lambda: self._subscribe_sse(self._current_workspace))

    def _on_sse_finished(self) -> None:
        self._sse_reply = None
        if self._current_workspace:
            QTimer.singleShot(2000, lambda: self._subscribe_sse(self._current_workspace))

    def _handle_workspace_event(self, event: dict) -> None:
        job_name = event.get("job")
        status = event.get("status")
        if job_name and job_name in self._job_rows:
            badge = self._job_rows[job_name]
            badge.setText(status or "")
            badge.setStyleSheet(
                f"background-color: {self._badge_color(status)};"
                " color: white; padding: 2px 8px; border-radius: 4px;"
            )
