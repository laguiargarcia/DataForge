"""AppWindow: standalone window for a workspace app (no real page load needed)."""
import os

os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

import pytest

pytest.importorskip("PySide6.QtWebEngineWidgets")

from PySide6.QtCore import QUrl
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWidgets import QApplication

from dataforge.apps import AppInfo


def _get_app():
    return QApplication.instance() or QApplication(["test", "-platform", "offscreen"])


@pytest.fixture(scope="module", autouse=True)
def _module_home(tmp_path_factory):
    """The web profile is one per process: keep one data dir for the whole module."""
    mp = pytest.MonkeyPatch()
    home = tmp_path_factory.mktemp("home")
    mp.setenv("DATAFORGE_HOME", str(home))
    yield home
    mp.undo()


@pytest.fixture
def info(tmp_path):
    return AppInfo(workspace="ws", name="painel", title="Painel", dist=tmp_path,
                   app_dir=tmp_path, icon=None)


@pytest.fixture
def window(info, monkeypatch):
    _get_app()
    from PySide6.QtCore import QSettings
    QSettings("DataForge", "apps/ws/painel").clear()
    from dataforge.gui.app_window import AppWindow
    monkeypatch.setattr("dataforge.gui.app_window.QWebEngineView.load", lambda self, url: None)
    win = AppWindow(info, 8765)
    yield win
    win._view.deleteLater()
    win.close()
    QSettings("DataForge", "apps/ws/painel").clear()


@pytest.mark.parametrize("url,expected", [
    ("http://127.0.0.1:8765/apps/ws/a/", True),
    ("http://127.0.0.1:8765/apps/ws/a/x/y.js?z=1", True),
    ("http://127.0.0.1:8765/apps/outro/", False),
    ("http://127.0.0.1:9999/apps/ws/a/", False),
    ("https://exemplo.com", False),
    ("https://127.0.0.1:8765/apps/ws/a/", False),
    ("http://127.0.0.1:8765/apps/ws/ab/", False),
    ("http://127.0.0.1:8765/apps/ws/a/../../b/", False),
])
def test_is_inside_app(url, expected):
    from dataforge.gui.app_window import is_inside_app
    assert is_inside_app(url, "http://127.0.0.1:8765/apps/ws/a/") is expected


def test_titulo_e_icone(window):
    assert window.windowTitle() == "Painel · ws"


def test_icone_do_app(info, tmp_path, monkeypatch):
    _get_app()
    from PySide6.QtGui import QImage
    png = tmp_path / "i.png"
    img = QImage(8, 8, QImage.Format.Format_RGB32)
    img.fill(0xFF0000)
    assert img.save(str(png))
    from dataforge.gui.app_window import AppWindow
    monkeypatch.setattr("dataforge.gui.app_window.QWebEngineView.load", lambda self, url: None)
    win = AppWindow(AppInfo("ws", "painel", "Painel", tmp_path, tmp_path, icon=png), 8765)
    assert not win.windowIcon().isNull()
    win._view.deleteLater()
    win.close()


def test_url_carregada(info, monkeypatch):
    _get_app()
    from dataforge.gui.app_window import AppWindow
    seen = []
    monkeypatch.setattr("dataforge.gui.app_window.QWebEngineView.load", lambda self, url: seen.append(url.toString()))
    win = AppWindow(info, 8765)
    assert seen == ["http://127.0.0.1:8765/apps/ws/painel/"]
    win._view.deleteLater()
    win.close()


def test_navegacao_externa_abre_no_sistema(window, monkeypatch):
    opened = []
    monkeypatch.setattr("dataforge.gui.app_window.QDesktopServices.openUrl", lambda u: opened.append(u.toString()))
    page = window._view.page()
    nav = QWebEnginePage.NavigationType.NavigationTypeLinkClicked
    assert page.acceptNavigationRequest(QUrl("https://exemplo.com/x"), nav, True) is False
    assert opened == ["https://exemplo.com/x"]
    assert page.acceptNavigationRequest(QUrl("http://127.0.0.1:8765/apps/ws/painel/a"), nav, True) is True
    assert opened == ["https://exemplo.com/x"]


def test_target_blank_abre_no_sistema(window, monkeypatch):
    opened = []
    monkeypatch.setattr("dataforge.gui.app_window.QDesktopServices.openUrl", lambda u: opened.append(u.toString()))
    page = window._view.page()
    popup = page.createWindow(QWebEnginePage.WebWindowType.WebBrowserTab)
    assert popup is not None
    nav = QWebEnginePage.NavigationType.NavigationTypeLinkClicked
    assert popup.acceptNavigationRequest(QUrl("https://exemplo.com/novo"), nav, True) is False
    assert opened == ["https://exemplo.com/novo"]
    popup.deleteLater()


@pytest.mark.parametrize("url", ["file:///C:/Windows/System32/calc.exe", "ms-settings:privacy",
                                 "javascript:alert(1)"])
def test_target_blank_sem_http_nao_abre_no_sistema(window, monkeypatch, url):
    opened = []
    monkeypatch.setattr("dataforge.gui.app_window.QDesktopServices.openUrl", lambda u: opened.append(u.toString()))
    popup = window._view.page().createWindow(QWebEnginePage.WebWindowType.WebBrowserTab)
    nav = QWebEnginePage.NavigationType.NavigationTypeLinkClicked
    assert popup.acceptNavigationRequest(QUrl(url), nav, True) is False
    assert opened == []
    popup.deleteLater()


def test_geometria_lembrada(info, monkeypatch):
    _get_app()
    from dataforge.gui.app_window import AppWindow
    from PySide6.QtCore import QSettings
    QSettings("DataForge", "apps/ws/painel").clear()
    monkeypatch.setattr("dataforge.gui.app_window.QWebEngineView.load", lambda self, url: None)
    w1 = AppWindow(info, 8765)
    w1.resize(777, 555)
    w1.close()
    w1._view.deleteLater()
    w2 = AppWindow(info, 8765)
    assert (w2.width(), w2.height()) == (777, 555)
    w2._view.deleteLater()
    w2.close()
    QSettings("DataForge", "apps/ws/painel").clear()


def test_ligar_de_novo_recarrega(window, monkeypatch):
    calls = []
    monkeypatch.setattr("dataforge.instance.ensure_server", lambda: calls.append("ensure") or 8765)
    monkeypatch.setattr(window._view, "reload", lambda: calls.append("reload"))
    assert window._lost_banner.isHidden()
    window.show_server_lost()
    assert not window._lost_banner.isHidden()
    window._lost_banner.restart_btn.click()
    assert calls == ["ensure", "reload"]
    assert window._lost_banner.isHidden()


def test_load_finished_falso_pede_batida_e_nao_mostra_aviso(window, monkeypatch):
    beats = []
    monkeypatch.setattr(window._heartbeat, "beat_now", lambda: beats.append(1))
    window._view.loadFinished.emit(False)
    assert window._lost_banner.isHidden()
    assert beats == [1]
    window._heartbeat.server_lost.emit()
    assert not window._lost_banner.isHidden()
    window._view.loadFinished.emit(True)
    assert window._lost_banner.isHidden()


@pytest.mark.parametrize("url", ["data:text/html,x", "blob:http://127.0.0.1:8765/abc", "about:blank",
                                 "javascript:alert(1)", "file:///etc/passwd"])
def test_main_frame_sem_http_nao_abre_no_sistema(window, monkeypatch, url):
    opened = []
    monkeypatch.setattr("dataforge.gui.app_window.QDesktopServices.openUrl", lambda u: opened.append(u.toString()))
    nav = QWebEnginePage.NavigationType.NavigationTypeLinkClicked
    assert window._view.page().acceptNavigationRequest(QUrl(url), nav, True) is False
    assert opened == []


@pytest.mark.parametrize("url", ["data:text/html,x", "https://exemplo.com/f", "blob:http://127.0.0.1:8765/abc"])
def test_subframe_fora_do_app_passa_sem_abrir(window, monkeypatch, url):
    opened = []
    monkeypatch.setattr("dataforge.gui.app_window.QDesktopServices.openUrl", lambda u: opened.append(u.toString()))
    nav = QWebEnginePage.NavigationType.NavigationTypeLinkClicked
    assert window._view.page().acceptNavigationRequest(QUrl(url), nav, False) is True
    assert opened == []


def test_perfil_persistente_sob_o_data_dir(window, _module_home):
    from dataforge.gui.app_window import app_profile
    profile = app_profile()
    assert not profile.isOffTheRecord()
    assert str(_module_home) in profile.persistentStoragePath()
    assert window._view.page().profile() is profile


def test_download_aceito_na_pasta_de_downloads(window):
    from unittest.mock import MagicMock
    req = MagicMock()
    req.page.return_value = window._view.page()
    req.downloadFileName.return_value = "dados.csv"
    window._on_download_requested(req)
    req.setDownloadDirectory.assert_called_once()
    req.accept.assert_called_once()


@pytest.mark.parametrize("state,message", [
    ("DownloadCompleted", "Baixado: dados.csv"),
    ("DownloadInterrupted", "Falha ao baixar: dados.csv"),
    ("DownloadCancelled", "Falha ao baixar: dados.csv"),
])
def test_mensagem_do_download_segue_o_estado(window, state, message):
    from unittest.mock import MagicMock
    from PySide6.QtWebEngineCore import QWebEngineDownloadRequest
    req = MagicMock()
    req.page.return_value = window._view.page()
    req.downloadFileName.return_value = "dados.csv"
    req.state.return_value = getattr(QWebEngineDownloadRequest.DownloadState, state)
    window._on_download_requested(req)
    slot = req.isFinishedChanged.connect.call_args.args[0]
    slot()
    assert window.statusBar().currentMessage() == message
    window.statusBar().clearMessage()


def test_sem_icone_usa_o_icone_padrao(window):
    assert not window.windowIcon().isNull()


def test_geometria_salva_no_about_to_quit(info, monkeypatch):
    _get_app()
    from PySide6.QtCore import QSettings
    from dataforge.gui.app_window import AppWindow
    QSettings("DataForge", "apps/ws/painel").clear()
    monkeypatch.setattr("dataforge.gui.app_window.QWebEngineView.load", lambda self, url: None)
    win = AppWindow(info, 8765)
    win.resize(640, 480)
    # Emitting aboutToQuit for real would shut QtWebEngine down for the whole process: check the
    # connection instead, and that the slot it points at saves.
    quit_signal = QApplication.instance().aboutToQuit
    assert quit_signal.disconnect(win._save_geometry)  # was connected
    quit_signal.connect(win._save_geometry)
    win._save_geometry()
    assert QSettings("DataForge", "apps/ws/painel").value("geometry") is not None
    win._view.deleteLater()
    win.close()
    QSettings("DataForge", "apps/ws/painel").clear()


def test_start_e_stop_presence(window, monkeypatch):
    calls = []
    monkeypatch.setattr(window._heartbeat, "start", lambda: calls.append("start"))
    monkeypatch.setattr(window._heartbeat, "stop", lambda: calls.append("stop"))
    window.start_presence()
    window.stop_presence()
    assert calls == ["start", "stop"]


def test_heartbeat_server_lost_mostra_aviso(window):
    window._heartbeat.server_lost.emit()
    assert not window._lost_banner.isHidden()
    window._heartbeat.server_ok.emit()
    assert window._lost_banner.isHidden()
    window._heartbeat.stop()


@pytest.fixture
def critical_box(monkeypatch):
    """QMessageBox.critical recorded instead of shown (under pythonw it is all the user sees)."""
    from unittest.mock import MagicMock
    box = MagicMock()
    monkeypatch.setattr("dataforge.gui.app_window.QMessageBox.critical", box)
    monkeypatch.setattr("dataforge.gui.app_window.signal.signal", lambda *a: None)  # no SIGINT handler in pytest
    return box


def test_run_app_window_inexistente(dataforge_home, capsys, critical_box):
    from dataforge.gui.app_window import run_app_window
    assert run_app_window("nao_existe", "x") == 1
    assert "nao_existe" in capsys.readouterr().err
    critical_box.assert_called_once()
    assert critical_box.call_args.args[1] == "DataForge"
    assert "nao_existe" in critical_box.call_args.args[2]


def test_run_app_window_app_inexistente(workspace_dir, capsys, monkeypatch, critical_box):
    from dataforge.gui.app_window import run_app_window
    called = []
    monkeypatch.setattr("dataforge.instance.ensure_server", lambda: called.append(1) or 1)
    assert run_app_window("test_ws", "sumiu") == 1
    assert "sumiu" in capsys.readouterr().err
    assert called == []
    critical_box.assert_called_once()
    assert critical_box.call_args.args[1] == "DataForge"
    assert "sumiu" in critical_box.call_args.args[2]


def test_run_app_window_move_o_coletor_para_a_thread_da_gui(workspace_dir, make_app, monkeypatch, critical_box):
    # df-3yh: as run_app, the app window collects garbage on the GUI thread only, before the server.
    from dataforge.gui import app_window
    from dataforge.instance import InstanceError
    make_app(workspace_dir, "painel")
    events = []
    monkeypatch.setattr(app_window.gc_guard, "install", lambda parent: events.append("gc_guard"))

    def no_server():
        events.append("ensure_server")
        raise InstanceError("sem servidor")

    monkeypatch.setattr(app_window.instance, "ensure_server", no_server)
    assert app_window.run_app_window("test_ws", "painel") == 1
    assert events == ["gc_guard", "ensure_server"]
    assert "sem servidor" in critical_box.call_args.args[2]
