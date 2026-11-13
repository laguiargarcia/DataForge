"""Standalone window for a workspace app: the app served by the local server, no browser."""
from __future__ import annotations

import posixpath
import signal
import sys
from urllib.parse import unquote, urlsplit

from PySide6.QtCore import QCoreApplication, QSettings, QStandardPaths, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWebEngineCore import QWebEngineDownloadRequest, QWebEnginePage, QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox, QVBoxLayout, QWidget

from dataforge import instance
from dataforge.apps import AppInfo, load_app
from dataforge.compat import resolve_workspace, webengine_profile_dir
from dataforge.gui import gc_guard
from dataforge.gui.icons import default_icon
from dataforge.gui.presence_client import PresenceHeartbeat
from dataforge.gui.server_lost_banner import ServerLostBanner


def _origin(parts) -> tuple[str, str | None, int | None]:
    return parts.scheme, parts.hostname, parts.port


def is_inside_app(url: str, base: str) -> bool:
    """True when ``url`` has the scheme, host and port of ``base`` and its path lies under
    ``base``'s path (``..`` segments are resolved first, so they cannot escape it)."""
    target, root = urlsplit(url), urlsplit(base)
    if _origin(target) != _origin(root):
        return False
    path = posixpath.normpath(unquote(target.path)) if target.path else "/"
    return (path.rstrip("/") + "/").startswith(root.path.rstrip("/") + "/")


_WEB_SCHEMES = ("http", "https")
_profile: QWebEngineProfile | None = None


def app_profile() -> QWebEngineProfile:
    """The one persistent web profile of the app windows (localStorage, IndexedDB and cookies
    survive a restart). Created once per process, parented to the QApplication so it outlives
    every page that uses it."""
    global _profile  # noqa: PLW0603 -- one profile per process, created lazily after QApplication
    if _profile is None:
        _profile = QWebEngineProfile("dataforge-apps", QApplication.instance())
        _profile.setPersistentStoragePath(str(webengine_profile_dir()))
        _profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.AllowPersistentCookies)
    return _profile


class _ExternalPage(QWebEnginePage):
    """Throwaway page for ``target=_blank``: an http(s) link goes to the system browser, any other
    scheme (``file:``, custom protocols handed to the shell) is dropped, as in ``_AppPage``."""

    def acceptNavigationRequest(self, url, _nav_type, _is_main_frame) -> bool:
        if url.scheme() in _WEB_SCHEMES:
            QDesktopServices.openUrl(url)
        QTimer.singleShot(0, self.deleteLater)
        return False


class _AppPage(QWebEnginePage):
    """Keeps navigation inside the app; everything else opens in the system browser."""

    def __init__(self, profile: QWebEngineProfile, base: str, parent=None) -> None:
        super().__init__(profile, parent)
        self._base = base

    def acceptNavigationRequest(self, url, _nav_type, is_main_frame) -> bool:
        if not is_main_frame:  # iframes (data:, blob:, other origins) belong to the page, not the browser
            return True
        if is_inside_app(url.toString(), self._base):
            return True
        if url.scheme() in _WEB_SCHEMES:
            QDesktopServices.openUrl(url)
        return False

    def createWindow(self, _window_type) -> QWebEnginePage:
        return _ExternalPage(self.profile(), self)


class AppWindow(QMainWindow):
    def __init__(self, info: AppInfo, port: int) -> None:
        super().__init__()
        self._settings = QSettings("DataForge", f"apps/{info.workspace}/{info.name}")
        self.setWindowTitle(f"{info.title} · {info.workspace}")
        self.setWindowIcon(QIcon(str(info.icon)) if info.icon is not None else default_icon())
        self.resize(1100, 750)
        geometry = self._settings.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._lost_banner = ServerLostBanner()
        layout.addWidget(self._lost_banner)

        base = f"http://127.0.0.1:{port}{info.url_path}"
        self._view = QWebEngineView()
        page = _AppPage(app_profile(), base, self._view)
        self._view.setPage(page)
        page.profile().downloadRequested.connect(self._on_download_requested)
        self._view.loadFinished.connect(self._on_load_finished)
        self._lost_banner.restarted.connect(lambda: self._view.reload())
        layout.addWidget(self._view)

        self._heartbeat = PresenceHeartbeat(port, "app", info.workspace, info.name, parent=self)
        self._heartbeat.server_lost.connect(self.show_server_lost)
        self._heartbeat.server_ok.connect(self.hide_server_lost)
        QApplication.instance().aboutToQuit.connect(self._save_geometry)  # Ctrl+C skips closeEvent
        self._view.load(QUrl(base))

    def start_presence(self) -> None:
        self._heartbeat.start()

    def stop_presence(self) -> None:
        self._heartbeat.stop()

    def _on_download_requested(self, request) -> None:
        """Downloads (e.g. a CSV export) go to the user's Downloads folder, with a status message."""
        if request.page() is not self._view.page():  # the profile is shared by every app window
            return
        request.setDownloadDirectory(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation))
        name = request.downloadFileName()
        request.isFinishedChanged.connect(lambda: self._show_download_result(request, name))
        request.accept()

    def _show_download_result(self, request, name: str) -> None:
        if request.state() == QWebEngineDownloadRequest.DownloadState.DownloadCompleted:
            self.statusBar().showMessage(f"Baixado: {name}", 8000)
        else:  # interrupted or cancelled
            self.statusBar().showMessage(f"Falha ao baixar: {name}", 8000)

    def _save_geometry(self) -> None:
        self._settings.setValue("geometry", self.saveGeometry())

    def show_server_lost(self) -> None:
        self._lost_banner.show()

    def hide_server_lost(self) -> None:
        self._lost_banner.hide()

    def _on_load_finished(self, ok: bool) -> None:
        """A failed load is not proof the server is gone (refused external link, 404, 503):
        ask for a beat and let ``server_lost`` decide."""
        if ok:
            self._lost_banner.hide()
        else:
            self._heartbeat.beat_now()

    def closeEvent(self, event) -> None:
        self._save_geometry()
        QApplication.instance().aboutToQuit.disconnect(self._save_geometry)  # saved; nothing left to do at quit
        self.stop_presence()
        event.accept()


def run_app_window(workspace: str, app: str) -> int:
    """Open the window of ``app`` in ``workspace``. Returns the Qt exit code (1 = could not open)."""
    if QApplication.instance() is None:  # QtWebEngine needs this before the QApplication exists
        QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    qapp = QApplication.instance() or QApplication(sys.argv)

    try:
        info = load_app(resolve_workspace(workspace), app)
        error = None if info is not None else f"App '{app}' não encontrado no workspace '{workspace}'."
    except ValueError as exc:
        error = str(exc)
    if error is not None:
        # Under pythonw (the desktop shortcut) stderr is None: the dialog is all the user sees.
        if sys.stderr is not None:
            print(error, file=sys.stderr)
        QMessageBox.critical(None, "DataForge", error)
        return 1

    # Deliver SIGINT (Ctrl+C) to Qt's event loop; see dataforge.gui.run_app.
    signal.signal(signal.SIGINT, lambda *_: qapp.quit())
    sigint_timer = QTimer()
    sigint_timer.start(200)
    sigint_timer.timeout.connect(lambda: None)

    # The cyclic collector runs on the GUI thread only (df-3yh), as in dataforge.gui.run_app.
    gc_guard.install(qapp)

    try:
        port = instance.ensure_server()
    except instance.InstanceError as exc:
        QMessageBox.critical(None, "DataForge", str(exc))
        return 1

    window = AppWindow(info, port)
    window.start_presence()
    window.show()
    try:
        return qapp.exec()
    finally:
        window.stop_presence()
        sigint_timer.stop()
