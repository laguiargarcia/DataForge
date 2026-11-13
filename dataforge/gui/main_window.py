"""DataForge main application window.

Shell layout: QVBoxLayout wrapping header (workspace combo) + QHBoxLayout with
QListWidget sidebar (180px) + QStackedWidget content area.
The window is a plain client of the local server; the background process owns the tray.
"""
from __future__ import annotations

import json

from PySide6.QtCore import QSettings, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from dataforge.compat import DEFAULT_API_PORT, api_port
from dataforge.gui.server_lost_banner import ServerLostBanner

SETTINGS_ORG = "DataForge"
SETTINGS_APP = "DataForge"
KEY_LAST_WS = "main/last_workspace"


class MainWindow(QMainWindow):
    """Main application window with sidebar navigation."""

    workspace_changed = Signal(str)

    SECTIONS: list[str] = ["Jobs", "Scheduler", "SQL", "Dashboards", "Deploy", "Settings"]

    def __init__(self) -> None:
        super().__init__()
        self._port = DEFAULT_API_PORT
        self._nam = QNetworkAccessManager(self)
        self.setWindowTitle("DataForge")
        self.resize(1200, 800)
        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Notice shown while the local server does not answer (hidden otherwise)
        self._lost_banner = ServerLostBanner()
        self._lost_label = self._lost_banner.label
        self._restart_btn = self._lost_banner.restart_btn
        self._lost_banner.restarted.connect(lambda: self._load_workspaces())
        root_layout.addWidget(self._lost_banner)

        # Header bar with workspace selector
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(8, 6, 8, 6)
        header_layout.addWidget(QLabel("Workspace:"))
        self._ws_combo = QComboBox()
        self._ws_combo.currentTextChanged.connect(self.workspace_changed)
        self.workspace_changed.connect(self._persist_workspace)
        header_layout.addWidget(self._ws_combo)
        header_layout.addStretch()
        root_layout.addWidget(header)

        # Sidebar + content row
        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        self._sidebar = QListWidget()
        self._sidebar.setFixedWidth(180)
        for section in self.SECTIONS:
            self._sidebar.addItem(section)
        self._sidebar.setCurrentRow(0)
        self._sidebar.currentRowChanged.connect(self._on_nav)

        self._stack = QStackedWidget()
        # Panels added in _load_panels (after QApplication exists).
        # Pitfall 4: _load_workspaces called AFTER _load_panels connects all slots.
        self._load_panels()
        self._load_workspaces()

        body_layout.addWidget(self._sidebar)
        body_layout.addWidget(self._stack, stretch=1)
        root_layout.addWidget(body, stretch=1)

    def _load_panels(self) -> None:
        """Import and add panel widgets to the QStackedWidget.

        Panels are imported here (not at module level) because they import
        PySide6 widgets — safe only after QApplication has been created.
        workspace_changed is connected to each panel's set_workspace slot here,
        before _load_workspaces emits the first workspace name (Pitfall 4).
        """
        try:
            from dataforge.gui.panels import dashboards, deploy, jobs, scheduler, settings, sql

            for panel_mod in (jobs, scheduler, sql, dashboards, deploy, settings):
                panel = panel_mod.Panel()
                self._stack.addWidget(panel)
                if hasattr(panel, "set_workspace"):
                    self.workspace_changed.connect(panel.set_workspace)
        except ImportError:
            # Panels not yet implemented — add one placeholder per section.
            for _ in self.SECTIONS:
                self._stack.addWidget(QWidget())

    def _load_workspaces(self) -> None:
        """Fetch workspace list from local daemon API (async, never blocking)."""
        self._port = api_port()
        url = QUrl(f"http://127.0.0.1:{self._port}/api/workspaces/")
        req = QNetworkRequest(url)
        reply = self._nam.get(req)
        reply.finished.connect(lambda: self._on_workspaces_loaded(reply))

    def _on_workspaces_loaded(self, reply) -> None:
        had_error = reply.error() != QNetworkReply.NetworkError.NoError
        raw = bytes(reply.readAll())
        reply.deleteLater()

        if had_error:
            # Daemon not ready yet — retry after 1 s (startup race condition)
            QTimer.singleShot(1000, self._load_workspaces)
            return

        try:
            data = json.loads(raw.decode())
        except Exception:
            return
        if not isinstance(data, list):
            return
        names = sorted(ws["name"] for ws in data if isinstance(ws, dict) and "name" in ws)

        self._ws_combo.blockSignals(True)
        self._ws_combo.clear()
        for name in names:
            self._ws_combo.addItem(name)

        settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
        last_ws = settings.value(KEY_LAST_WS, defaultValue=None)
        if last_ws and last_ws in names:
            self._ws_combo.setCurrentText(last_ws)
        elif names:
            self._ws_combo.setCurrentIndex(0)
        self._ws_combo.blockSignals(False)

        # Emit the selected workspace once — panels are already connected (Pitfall 4)
        current = self._ws_combo.currentText()
        if current:
            self.workspace_changed.emit(current)

    def _persist_workspace(self, name: str) -> None:
        QSettings(SETTINGS_ORG, SETTINGS_APP).setValue(KEY_LAST_WS, name)

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _on_nav(self, index: int) -> None:
        self._stack.setCurrentIndex(index)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def show_server_lost(self) -> None:
        """Non-blocking notice, with a button, that the local server stopped answering."""
        self._lost_banner.show()

    def hide_server_lost(self) -> None:
        self._lost_banner.hide()

    def closeEvent(self, event) -> None:
        """The admin window is a plain client: closing it really closes it."""
        event.accept()
