import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dataforge import shortcut
from dataforge.apps import AppInfo
from dataforge.cli import app

HOME = Path("C:/Users/X/My Projects/DataForge")


def _info(tmp_path, icon="app.ico", title="Finanças d'Ana"):
    return AppInfo("GetFinance-Show", "financas", title, tmp_path / "dist", tmp_path,
                   tmp_path / icon if icon else None)


def _fake_run(calls, desktop="C:\\Users\\X\\OneDrive\\Desktop\n", kws=None):
    def run(cmd, **kw):
        calls.append(cmd)
        if kws is not None:
            kws.append(kw)
        if "GetFolderPath" in cmd[-1]:
            return subprocess.CompletedProcess(cmd, 0, desktop, "")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    return run


def test_comando_powershell():
    lnk, target = Path("D/Finanças d'Ana.lnk"), Path("C:/Py/pythonw.exe")
    workdir, icon = Path("C:/home"), Path("C:/ic/app.ico")
    cmd = shortcut.shortcut_ps_command(lnk, target, "-m dataforge app w a", workdir, icon, "Finanças d'Ana")
    assert cmd[:3] == ["powershell", "-NoProfile", "-Command"]
    assert len(cmd) == 4
    script = cmd[3]
    quoted_lnk = str(lnk).replace("'", "''")
    assert f"CreateShortcut('{quoted_lnk}')" in script
    assert "d''Ana.lnk" in script
    assert f"$s.TargetPath = '{target}'" in script
    assert "$s.Arguments = '-m dataforge app w a'" in script
    assert f"$s.WorkingDirectory = '{workdir}'" in script
    assert "$s.Description = 'Finanças d''Ana'" in script
    assert f"$s.IconLocation = '{icon}'" in script
    assert script.endswith("$s.Save()")


def test_sem_icone_nao_define_iconlocation():
    cmd = shortcut.shortcut_ps_command(Path("a.lnk"), Path("p.exe"), "", Path("h"), None, "t")
    assert "IconLocation" not in cmd[3]


def test_png_nao_define_iconlocation(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", _fake_run(calls))
    shortcut.create_app_shortcut(_info(tmp_path, icon="app.png"), HOME)
    assert "IconLocation" not in calls[-1][3]


def test_ico_maiusculo_define_iconlocation(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", _fake_run(calls))
    shortcut.create_app_shortcut(_info(tmp_path, icon="APP.ICO"), HOME)
    assert "IconLocation" in calls[-1][3]


def test_desktop_vem_do_powershell_e_home_com_espacos(tmp_path, monkeypatch):
    calls, kws = [], []
    monkeypatch.setattr(subprocess, "run", _fake_run(calls, kws=kws))
    lnk = shortcut.create_app_shortcut(_info(tmp_path), HOME)
    assert calls[0][-1] == (
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8; [Environment]::GetFolderPath('Desktop')"
    )
    assert all(kw["encoding"] == "utf-8" and "text" not in kw for kw in kws)
    assert "OneDrive" in str(lnk)
    assert str(lnk).endswith("Finanças d'Ana (GetFinance-Show).lnk")
    script = calls[1][3]
    expected_args = f'-m dataforge app GetFinance-Show financas --home "{HOME}"'
    assert f"$s.Arguments = '{expected_args}'" in script
    assert f"$s.WorkingDirectory = '{HOME}'" in script


def test_desktop_com_acentos_round_trip(monkeypatch):
    out = "C:\\Users\\José\\OneDrive\\Área de Trabalho\r\n"
    monkeypatch.setattr(subprocess, "run", _fake_run([], desktop=out))
    assert str(shortcut.desktop_dir()) == out.strip()


@pytest.mark.parametrize("res", [(1, "x"), (0, "  \n")])
def test_desktop_falha(tmp_path, monkeypatch, res):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, res[0], res[1], ""))
    with pytest.raises(RuntimeError, match="Área de Trabalho"):
        shortcut.create_app_shortcut(_info(tmp_path), HOME)


def test_pythonw_ao_lado_ou_fallback(tmp_path, monkeypatch):
    exe = tmp_path / "python.exe"
    exe.write_text("")
    monkeypatch.setattr(sys, "executable", str(exe))
    assert shortcut.pythonw_path() == exe
    (tmp_path / "pythonw.exe").write_text("")
    assert shortcut.pythonw_path() == tmp_path / "pythonw.exe"


@pytest.fixture
def ws_app(tmp_path, monkeypatch):
    monkeypatch.setenv("DATAFORGE_HOME", str(tmp_path))
    d = tmp_path / "workspaces" / "w"
    (d / "apps" / "a" / "dist").mkdir(parents=True)
    (d / "workspace.yaml").write_text("name: w\n")
    (d / "apps" / "a" / "app.yaml").write_text("name: a\ntitle: Meu App\n")
    (d / "apps" / "a" / "dist" / "index.html").write_text("<html></html>")
    return tmp_path


def _no_app_window(monkeypatch):
    """Fail if the command opens the window. Call it BEFORE faking sys.platform: the patch imports
    dataforge.gui.app_window, hence PySide6, whose import differs per platform (on Linux a faked
    win32 makes it call os.add_dll_directory, which only Windows has)."""
    monkeypatch.setattr("dataforge.gui.app_window.run_app_window", lambda *a: pytest.fail("abriu a janela"))


def test_atalho_fora_do_windows_imprime_comando(ws_app, monkeypatch):
    _no_app_window(monkeypatch)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(shortcut.subprocess, "run", lambda *a, **k: pytest.fail("não deve rodar"))
    r = CliRunner().invoke(app, ["app", "w", "a", "--atalho"])
    assert r.exit_code == 0, r.output
    assert "dataforge app w a --home" in r.output
    assert sys.executable in r.output


def test_atalho_no_windows_chama_powershell(ws_app, monkeypatch):
    calls = []
    _no_app_window(monkeypatch)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "run", _fake_run(calls))
    r = CliRunner().invoke(app, ["app", "w", "a", "--atalho"])
    assert r.exit_code == 0, r.output
    assert len(calls) == 2
    assert "Atalho criado" in r.output
    assert "Meu App (w).lnk" in r.output


def test_atalho_workspace_inexistente(ws_app):
    r = CliRunner().invoke(app, ["app", "nope", "a", "--atalho"])
    assert r.exit_code == 1
    assert "nope" in r.output


def test_atalho_app_inexistente(ws_app):
    r = CliRunner().invoke(app, ["app", "w", "zzz", "--atalho"])
    assert r.exit_code == 1
    assert "zzz" in r.output


def test_atalho_home_relativo_vira_absoluto(ws_app, monkeypatch):
    monkeypatch.chdir(ws_app)
    monkeypatch.setenv("DATAFORGE_HOME", ".")
    calls = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "run", _fake_run(calls))
    r = CliRunner().invoke(app, ["app", "w", "a", "--atalho"])
    assert r.exit_code == 0, r.output
    assert f"--home {ws_app}" in calls[1][3].replace('"', "")
