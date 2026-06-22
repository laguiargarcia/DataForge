"""DataForge main application window.

Shell layout: QVBoxLayout wrapping header (workspace combo) + QHBoxLayout with
QListWidget sidebar (180px) + QStackedWidget content area.
Tray integration: minimize-to-tray on close, restore on double-click, quit via menu.
"""
from __future__ import annotations

import json

from PySide6.QtCore import QSettings, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QAction, QColor, QGuiApplication, QIcon, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMenu,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from dataforge.gui.daemon import DaemonThread

SETTINGS_ORG = "DataForge"
SETTINGS_APP = "DataForge"
KEY_LAST_WS = "main/last_workspace"


class MainWindow(QMainWindow):
    """Main application window with sidebar navigation and system tray."""

    workspace_changed = Signal(str)

    SECTIONS: list[str] = ["Jobs", "Scheduler", "SQL", "Dashboards", "Deploy", "Settings"]

    def __init__(self, daemon: DaemonThread) -> None:
        super().__init__()
        self._daemon = daemon
        self._port = 8000
        self._nam = QNetworkAccessManager(self)
        self.setWindowTitle("DataForge")
        self.resize(1200, 800)
        self._build_ui()
        self._setup_tray()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

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
        import os
        self._port = int(os.environ.get("DATAFORGE_PORT", 8000))
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
    # System tray
    # ------------------------------------------------------------------

    def _setup_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self._tray = None
            return

        self._tray = QSystemTrayIcon(self)

        icon = QIcon.fromTheme("applications-engineering")
        if icon.isNull():
            icon = QIcon.fromTheme("application-x-executable")
        if icon.isNull():
            # No desktop theme available (e.g. WSL2) — create a minimal placeholder.
            pixmap = QPixmap(16, 16)
            pixmap.fill(QColor("#4A90D9"))
            icon = QIcon(pixmap)
        self._tray.setIcon(icon)
        self._tray.setToolTip("DataForge")

        menu = QMenu()
        restore_action = QAction("Restore", self)
        restore_action.triggered.connect(self._on_restore)
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(lambda: self._shutdown_and_quit())
        menu.addAction(restore_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self._tray.setContextMenu(menu)

        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _on_restore(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._on_restore()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802
        """Minimize to tray instead of quitting when tray is active."""
        if self._tray is not None and self._tray.isVisible():
            self.hide()
            event.ignore()  # MUST ignore — otherwise Qt destroys the window
        else:
            self._shutdown_and_quit(event=event)

    def _shutdown_and_quit(self, event=None) -> None:
        """Stop daemon and exit Qt application."""
        self._daemon.stop()
        if event is not None:
            event.accept()
        QApplication.quit()
