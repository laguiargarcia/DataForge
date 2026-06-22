"""Deploy panel — D4b: a thin async client of the D4a deploy HTTP API.

No business logic lives in the widget. The daemon (via dataforge/deploy/service.py) is the
single ledger writer; this panel only renders the plan diff and POSTs apply/rollback, exactly
like every other panel (mirrors dataforge/gui/panels/scheduler.py).
"""
from __future__ import annotations

import json
import os
from typing import Optional

from PySide6.QtCore import QByteArray, QUrl, Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dataforge.deploy.models import Action


# ---------------------------------------------------------------------------
# Module-level pure helpers (importable without constructing Qt widgets)
# ---------------------------------------------------------------------------

# Category order + writability are DERIVED from the engine's Action enum and writable()
# semantics — not a parallel hardcoded list — so the GUI can never drift from the engine.
_ACTION_ORDER = [a.value for a in Action]
_WRITABLE_ACTIONS = {Action.add.value, Action.modify.value, Action.delete.value}

# Presentation-only display label + tree color per action. This is intrinsic UI data (like
# scheduler._DAY_NAMES), not a configurable business lookup. "IGNORED" reads better than the
# wire value "SKIP_IGNORED".
_ACTION_STYLE = {
    Action.add.value:          ("ADD",       "#3fb950"),  # green
    Action.modify.value:       ("MODIFY",    "#d29922"),  # amber
    Action.delete.value:       ("DELETE",    "#f85149"),  # red
    Action.protected.value:    ("PROTECTED", "#a371f7"),  # purple — never written (laws #2/#3)
    Action.skip_ignored.value: ("IGNORED",   "#8b949e"),  # grey — .deployignore (law #3)
}


def is_writable_action(action: str) -> bool:
    """True for actions that touch disk on apply (ADD/MODIFY/DELETE) — the only items that may
    be selected for `only`. PROTECTED/IGNORED are never deployable, so they get no checkbox."""
    return action in _WRITABLE_ACTIONS


def action_label(action: str) -> str:
    """Display label for an action string (SKIP_IGNORED → 'IGNORED'); unknown → passthrough."""
    style = _ACTION_STYLE.get(action)
    return style[0] if style else action


def action_color(action: str) -> Optional[str]:
    """Tree color hex for an action string, or None if unknown."""
    style = _ACTION_STYLE.get(action)
    return style[1] if style else None


def group_items_by_action(items: list[dict]) -> dict[str, list[dict]]:
    """Group plan `items` (dicts from /plan) by `action`, preserving the engine's action order.
    Unknown actions sort last (stable). Empty categories are omitted."""
    grouped: dict[str, list[dict]] = {}
    for item in items:
        grouped.setdefault(item.get("action", ""), []).append(item)
    ordered = {a: grouped[a] for a in _ACTION_ORDER if a in grouped}
    for a, group in grouped.items():
        if a not in ordered:
            ordered[a] = group
    return ordered


def format_provenance(entry: dict) -> str:
    """One-line provenance for a ledger entry (D19): 'source → target [pipeline] id (ts)'.
    Shown in the rollback confirm dialog so the operator sees what an undo will touch."""
    base = (
        f"{entry.get('source', '?')} → {entry.get('target', '?')} "
        f"[{entry.get('pipeline', '?')}] {entry.get('id', '?')}"
    )
    ts = entry.get("ts", "")
    return f"{base} ({ts})" if ts else base


# ---------------------------------------------------------------------------
# Deploy Panel
# ---------------------------------------------------------------------------

class Panel(QWidget):
    """Deploy panel: pipeline picker → source→target → diff tree → Deploy / Rollback (D4b)."""

    def __init__(self) -> None:
        super().__init__()
        self._port = int(os.environ.get("DATAFORGE_PORT", 8000))
        self._nam = QNetworkAccessManager(self)
        self._current_workspace = ""       # SOURCE workspace (from MainWindow)
        self._current_pipeline = ""
        self._target = ""                  # resolved from the last /plan response
        self._last_entry: Optional[dict] = None  # last ledger entry (from /status) for rollback

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        # ---- Toolbar: pipeline picker + source→target route ----
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Pipeline:"))
        self._pipeline_combo = QComboBox()
        self._pipeline_combo.currentIndexChanged.connect(self._on_pipeline_changed)
        toolbar.addWidget(self._pipeline_combo)
        self._route_label = QLabel("")
        toolbar.addWidget(self._route_label)
        toolbar.addStretch()
        outer.addLayout(toolbar)

        # ---- Error label (hidden until an API error) ----
        self._error_label = QLabel("")
        self._error_label.setStyleSheet("color: #ff6b6b;")
        self._error_label.setVisible(False)
        outer.addWidget(self._error_label)

        # ---- Diff tree ----
        self._tree = QTreeWidget()
        self._tree.setColumnCount(3)
        self._tree.setHeaderLabels(["Item", "Kind", "Reason"])
        self._tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        outer.addWidget(self._tree, stretch=1)

        # ---- Status + action buttons ----
        btn_row = QHBoxLayout()
        self._status_label = QLabel("")
        btn_row.addWidget(self._status_label)
        btn_row.addStretch()
        self._deploy_btn = QPushButton("Deploy")
        # Late-bound via lambda: _on_deploy/_on_rollback are added in Tasks 4/5. The lambda
        # defers the attribute lookup to click time, so __init__ constructs cleanly in this
        # task (a direct `.connect(self._on_deploy)` would AttributeError here). Lambda connects
        # are idiomatic in this codebase (see scheduler.py row buttons).
        self._deploy_btn.clicked.connect(lambda: self._on_deploy())
        self._rollback_btn = QPushButton("Rollback")
        self._rollback_btn.setEnabled(False)
        self._rollback_btn.clicked.connect(lambda: self._on_rollback())
        btn_row.addWidget(self._deploy_btn)
        btn_row.addWidget(self._rollback_btn)
        outer.addLayout(btn_row)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _base(self) -> str:
        return (
            f"http://127.0.0.1:{self._port}"
            f"/api/workspaces/{self._current_workspace}/deploy"
        )

    def _show_error(self, status, raw: str) -> None:
        try:
            detail = json.loads(raw).get("detail", raw)
        except Exception:
            detail = raw or f"HTTP {status}"
        self._error_label.setText(f"Error: {detail}")
        self._error_label.setVisible(True)

    # ------------------------------------------------------------------
    # Workspace slot — connected by MainWindow.workspace_changed
    # ------------------------------------------------------------------

    def set_workspace(self, name: str) -> None:
        """Called by MainWindow.workspace_changed: the SOURCE workspace."""
        if not name:
            return
        self._current_workspace = name
        self._load_pipelines()

    # ------------------------------------------------------------------
    # Pipelines
    # ------------------------------------------------------------------

    def _load_pipelines(self) -> None:
        reply = self._nam.get(QNetworkRequest(QUrl(f"{self._base()}/pipelines")))
        reply.finished.connect(lambda: self._on_pipelines_loaded(reply))

    def _on_pipelines_loaded(self, reply) -> None:
        try:
            data = json.loads(bytes(reply.readAll()).decode())
        except Exception:
            data = []
        finally:
            reply.deleteLater()
        if not isinstance(data, list):
            data = []

        self._pipeline_combo.blockSignals(True)
        self._pipeline_combo.clear()
        for name in data:
            self._pipeline_combo.addItem(str(name))
        if self._pipeline_combo.count() > 0:
            self._pipeline_combo.setCurrentIndex(0)
        self._pipeline_combo.blockSignals(False)

        if self._pipeline_combo.count() > 0:
            # Signals were blocked during repopulation, so trigger the load exactly once.
            self._on_pipeline_changed(0)
        else:
            self._current_pipeline = ""
            self._target = ""
            self._tree.clear()
            self._route_label.setText("")
            self._rollback_btn.setEnabled(False)
            self._status_label.setText("No deploy pipelines in this workspace.")

    def _on_pipeline_changed(self, _index: int) -> None:
        """Pipeline selection changed: load that pipeline's plan + status."""
        name = self._pipeline_combo.currentText()
        if not name:
            return
        self._current_pipeline = name
        self._error_label.setVisible(False)
        self._load_plan()
        self._load_status()

    # ------------------------------------------------------------------
    # Plan (diff/dry-run) — read-only, writes nothing
    # ------------------------------------------------------------------

    def _load_plan(self) -> None:
        url = QUrl(f"{self._base()}/{self._current_pipeline}/plan")
        reply = self._nam.get(QNetworkRequest(url))
        reply.finished.connect(lambda: self._on_plan_loaded(reply))

    def _on_plan_loaded(self, reply) -> None:
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        raw = bytes(reply.readAll()).decode(errors="replace")
        reply.deleteLater()
        if status is None or status >= 400:
            self._show_error(status, raw)
            self._tree.clear()
            return
        self._error_label.setVisible(False)   # a fresh successful plan clears any prior error
        try:
            plan = json.loads(raw)
        except Exception:
            plan = {}
        if not isinstance(plan, dict):
            plan = {}
        self._target = plan.get("target", "")
        source = plan.get("source", self._current_workspace)
        self._route_label.setText(f"{source} → {self._target}")
        items = plan.get("items", [])
        self._render_tree(items if isinstance(items, list) else [])

    def _render_tree(self, items: list[dict]) -> None:
        self._tree.clear()
        for action, group in group_items_by_action(items).items():
            color = action_color(action)
            parent = QTreeWidgetItem(self._tree, [f"{action_label(action)} ({len(group)})", "", ""])
            if color:
                parent.setForeground(0, QBrush(QColor(color)))
            writable = is_writable_action(action)
            for item in group:
                child = QTreeWidgetItem(parent, [
                    item.get("target_path", ""),
                    item.get("kind", ""),
                    item.get("reason", ""),
                ])
                child.setData(0, Qt.ItemDataRole.UserRole, item.get("target_path", ""))
                if color:
                    child.setForeground(0, QBrush(QColor(color)))
                if writable:
                    # Only ADD/MODIFY/DELETE can be selected for `only`; default checked = deploy all.
                    child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    child.setCheckState(0, Qt.CheckState.Checked)
                else:
                    # PROTECTED/IGNORED rows intentionally get NO checkbox (laws #2/#3).
                    child.setFlags(child.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            parent.setExpanded(True)

    def _collect_selected(self) -> list[str]:
        """Target paths of checked (writable) rows → apply `only`. Non-checkable rows are
        unreachable here, so a PROTECTED/IGNORED path can never enter the request."""
        selected: list[str] = []
        root = self._tree.invisibleRootItem()
        for i in range(root.childCount()):
            parent = root.child(i)
            for j in range(parent.childCount()):
                child = parent.child(j)
                if not (child.flags() & Qt.ItemFlag.ItemIsUserCheckable):
                    continue
                if child.checkState(0) == Qt.CheckState.Checked:
                    path = child.data(0, Qt.ItemDataRole.UserRole)
                    if path:
                        selected.append(path)
        return selected

    # ------------------------------------------------------------------
    # Deploy (apply) — daemon snapshots first; recoverable (D16/D2)
    # ------------------------------------------------------------------

    def _on_deploy(self) -> None:
        only = self._collect_selected()
        if not only:
            QMessageBox.information(
                self, "Nothing selected",
                "Check at least one ADD / MODIFY / DELETE item to deploy.",
            )
            return
        url = QUrl(f"{self._base()}/{self._current_pipeline}/apply")
        req = QNetworkRequest(url)
        req.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        body = json.dumps({"only": only}).encode()
        reply = self._nam.post(req, QByteArray(body))
        reply.finished.connect(lambda: self._on_apply_finished(reply))

    def _on_apply_finished(self, reply) -> None:
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        raw = bytes(reply.readAll()).decode(errors="replace")
        reply.deleteLater()
        if status is None or status >= 400:
            # Includes the D4a carry-forward: a mid-batch OSError surfaces as 500. The change is
            # recoverable (partial entry recorded); the GUI surfaces it instead of crashing.
            self._show_error(status, raw)
            return
        try:
            applied = json.loads(raw).get("applied", False)
        except Exception:
            applied = False
        if not applied:
            QMessageBox.information(
                self, "Nothing deployed",
                "No changes were applied (selected items may be protected or ignored).",
            )
        # Re-sync the diff + status with server truth after any apply attempt — even when nothing
        # was applied, the plan may have drifted, so always re-fetch.
        self._load_plan()
        self._load_status()

    # ------------------------------------------------------------------
    # Status — last ledger entry (provenance) + pending count
    # ------------------------------------------------------------------

    def _load_status(self) -> None:
        url = QUrl(f"{self._base()}/{self._current_pipeline}/status")
        reply = self._nam.get(QNetworkRequest(url))
        reply.finished.connect(lambda: self._on_status_loaded(reply))

    def _on_status_loaded(self, reply) -> None:
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        raw = bytes(reply.readAll()).decode(errors="replace")
        reply.deleteLater()
        if status is None or status >= 400:
            self._last_entry = None
            self._rollback_btn.setEnabled(False)
            return
        try:
            body = json.loads(raw)
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        last = body.get("last")
        self._last_entry = last if isinstance(last, dict) else None
        pending = body.get("pending", 0)
        if self._last_entry:
            self._status_label.setText(f"{pending} pending · last: {format_provenance(self._last_entry)}")
            self._rollback_btn.setEnabled(True)
        else:
            self._status_label.setText(f"{pending} pending · no deploy history")
            self._rollback_btn.setEnabled(False)

    # ------------------------------------------------------------------
    # Rollback — show provenance BEFORE confirm (D19), then LIFO undo
    # ------------------------------------------------------------------

    def _on_rollback(self) -> None:
        if not self._last_entry:
            QMessageBox.information(
                self, "Nothing to roll back",
                f"No deploy history for target '{self._target}'.",
            )
            return
        confirm = QMessageBox.question(
            self, "Confirm rollback",
            "Roll back the most recent deploy?\n\n" + format_provenance(self._last_entry),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        url = QUrl(f"{self._base()}/{self._current_pipeline}/rollback")
        reply = self._nam.post(QNetworkRequest(url), QByteArray(b""))
        reply.finished.connect(lambda: self._on_rollback_finished(reply))

    def _on_rollback_finished(self, reply) -> None:
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        raw = bytes(reply.readAll()).decode(errors="replace")
        reply.deleteLater()
        if status == 409:
            # Benign: nothing to undo (or a corrupt snapshot blob — D18); not an error state.
            QMessageBox.information(self, "Nothing to roll back",
                                    "No deploy to roll back.")
            self._load_status()
            return
        if status is None or status >= 400:
            self._show_error(status, raw)
            return
        self._load_plan()
        self._load_status()
