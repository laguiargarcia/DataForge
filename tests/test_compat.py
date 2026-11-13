import pytest
from pathlib import Path
from unittest.mock import patch
from dataforge.compat import (
    DEFAULT_API_PORT, api_port, data_dir, db_url, resolve_workspace, resolve_db, list_workspaces,
)


def test_data_dir_env_set(tmp_path, monkeypatch):
    """DATAFORGE_HOME set → data_dir() returns Path(DATAFORGE_HOME)."""
    monkeypatch.setenv("DATAFORGE_HOME", str(tmp_path))
    result = data_dir()
    assert result == tmp_path


def test_data_dir_platformdirs(tmp_path, monkeypatch):
    """DATAFORGE_HOME unset → data_dir() falls back to platformdirs.user_data_dir."""
    monkeypatch.delenv("DATAFORGE_HOME", raising=False)
    with patch("platformdirs.user_data_dir", return_value=str(tmp_path)) as mock_pd:
        result = data_dir()
    assert result == tmp_path
    mock_pd.assert_called_once()


def test_data_dir_first_creation_notice(tmp_path, monkeypatch, capsys):
    """data_dir() prints creation notice once when directory does not yet exist."""
    new_dir = tmp_path / "new_data_dir"
    monkeypatch.setenv("DATAFORGE_HOME", str(new_dir))
    result = data_dir()
    assert result == new_dir
    assert new_dir.exists()
    captured = capsys.readouterr()
    assert f"Created DataForge data directory: {new_dir}" in captured.out


def test_data_dir_no_notice_existing(tmp_path, monkeypatch, capsys):
    """data_dir() is silent when the directory already exists."""
    monkeypatch.setenv("DATAFORGE_HOME", str(tmp_path))
    data_dir()
    captured = capsys.readouterr()
    assert captured.out == ""


def test_db_url_linux(tmp_path):
    """db_url() returns a sqlite:/// URL with forward slashes for a Linux-style path."""
    path = tmp_path / "dataforge.db"
    result = db_url(path)
    assert result.startswith("sqlite:///")
    assert "\\" not in result
    assert "dataforge.db" in result


def test_db_url_windows():
    """db_url() returns a forward-slash URL even for a Windows-style path (simulated)."""
    # On Linux, Path("C:/...").resolve() resolves relative to CWD, so we simulate
    # what Windows would produce by using a mock that returns a posix string with C:/.
    win_posix = "C:/Users/Test/DataForge/dataforge.db"

    class FakeResolved:
        def as_posix(self):
            return win_posix

    win_path = Path("C:/Users/Test/DataForge/dataforge.db")
    with patch.object(type(win_path), "resolve", return_value=FakeResolved()):
        result = db_url(win_path)

    assert result.startswith("sqlite:///C:/")
    assert "\\" not in result


def test_resolve_workspace_with_name(tmp_path, monkeypatch):
    """resolve_workspace(name) returns data_dir() / 'workspaces' / name when workspace.yaml exists."""
    monkeypatch.setenv("DATAFORGE_HOME", str(tmp_path))
    ws_dir = tmp_path / "workspaces" / "myws"
    ws_dir.mkdir(parents=True)
    (ws_dir / "workspace.yaml").write_text("name: myws\n")
    result = resolve_workspace("myws")
    assert result == ws_dir


def test_resolve_workspace_with_name_not_found(tmp_path, monkeypatch):
    """resolve_workspace(name) raises ValueError when workspace does not exist."""
    monkeypatch.setenv("DATAFORGE_HOME", str(tmp_path))
    with pytest.raises(ValueError, match="Workspace 'noexist' not found"):
        resolve_workspace("noexist")


def test_list_workspaces(tmp_path, monkeypatch):
    """list_workspaces() returns sorted list of workspace directories."""
    monkeypatch.setenv("DATAFORGE_HOME", str(tmp_path))
    ws_dir = tmp_path / "workspaces" / "alpha"
    ws_dir.mkdir(parents=True)
    (ws_dir / "workspace.yaml").write_text("name: alpha\n")
    result = list_workspaces()
    assert result == [ws_dir]


def test_list_workspaces_sorted(tmp_path, monkeypatch):
    """list_workspaces() returns alphabetically sorted workspace directories."""
    monkeypatch.setenv("DATAFORGE_HOME", str(tmp_path))
    for name in ("zeta", "alpha", "beta"):
        ws = tmp_path / "workspaces" / name
        ws.mkdir(parents=True)
        (ws / "workspace.yaml").write_text(f"name: {name}\n")
    result = list_workspaces()
    assert [p.name for p in result] == ["alpha", "beta", "zeta"]


def test_resolve_workspace_rejects_traversal(tmp_path, monkeypatch):
    """resolve_workspace rejects names with path-traversal characters."""
    monkeypatch.setenv("DATAFORGE_HOME", str(tmp_path))
    with pytest.raises(ValueError, match="Invalid workspace name"):
        resolve_workspace("../evil")
    with pytest.raises(ValueError, match="Invalid workspace name"):
        resolve_workspace("a/b")
    with pytest.raises(ValueError, match="Invalid workspace name"):
        resolve_workspace("foo bar")


def test_resolve_db_default(tmp_path):
    """resolve_db(None, workspace_path) returns db_url(workspace_path / 'data/dataforge.db')."""
    result = resolve_db(None, tmp_path)
    expected = db_url(tmp_path / "data" / "dataforge.db")
    assert result == expected


def test_resolve_db_explicit():
    """resolve_db with explicit URL returns it unchanged."""
    custom_url = "sqlite:///x"
    result = resolve_db(custom_url, Path("/irrelevant"))
    assert result == custom_url


def test_api_port_defaults_when_the_variable_is_unset(monkeypatch):
    monkeypatch.delenv("DATAFORGE_PORT", raising=False)
    assert api_port() == DEFAULT_API_PORT == 8000


def test_api_port_reads_the_variable(monkeypatch):
    monkeypatch.setenv("DATAFORGE_PORT", "8123")
    assert api_port() == 8123


@pytest.mark.parametrize("value", ["abc", "0", "70000", "-1", ""])
def test_api_port_rejects_a_value_that_is_not_a_port(monkeypatch, value):
    # df-yvgx: say which variable is wrong and what it must be, not a bare int() error.
    monkeypatch.setenv("DATAFORGE_PORT", value)
    with pytest.raises(ValueError, match=r"^DATAFORGE_PORT must be a port number \(1-65535\), got "):
        api_port()


def test_set_data_dir_resolve_caminho_relativo_e_til(tmp_path, monkeypatch):
    import os
    from dataforge.compat import set_data_dir
    monkeypatch.setenv("DATAFORGE_HOME", "x")
    monkeypatch.chdir(tmp_path)
    set_data_dir("rel")
    assert os.environ["DATAFORGE_HOME"] == str((tmp_path / "rel").resolve())
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # what expanduser reads on Windows
    set_data_dir("~/h")
    assert os.environ["DATAFORGE_HOME"] == str((tmp_path / "h").resolve())


def test_webengine_profile_dir_sob_o_data_dir(dataforge_home):
    from dataforge.compat import webengine_profile_dir
    d = webengine_profile_dir()
    assert d.is_relative_to(dataforge_home)
    assert d.is_dir()


def test_locks_dir_e_criado_sob_o_data_dir(dataforge_home):
    from dataforge.compat import locks_dir, run_lock_path
    d = locks_dir()
    assert d == dataforge_home / "locks"
    assert d.is_dir()
    assert run_lock_path(dataforge_home / "workspaces" / "meu_ws") == d / "meu_ws.lock"


def test_run_lock_fora_do_home_nao_grava_no_home(dataforge_home, tmp_path):
    from dataforge.compat import run_lock_path
    fora = tmp_path / "outro" / "workspaces" / "ws"
    lock = run_lock_path(fora)
    assert lock == (tmp_path / "outro" / "locks" / "ws.lock").resolve()
    assert lock.parent.is_dir()
    assert not (dataforge_home / "locks").exists()


def test_logs_dir_e_criado_sob_o_data_dir(dataforge_home):
    from dataforge.compat import logs_dir
    d = logs_dir()
    assert d == dataforge_home / "logs"
    assert d.is_dir()


def test_set_api_port_vale_para_api_port(monkeypatch):
    from dataforge.compat import PORT_ENV, api_port, set_api_port
    monkeypatch.setenv(PORT_ENV, "8000")
    set_api_port(18555)
    assert api_port() == 18555


@pytest.mark.parametrize(("func", "folder"), [
    ("webengine_profile_dir", "webengine"), ("locks_dir", "locks"), ("logs_dir", "logs"),
])
def test_pastas_do_data_dir_sao_criadas_com_os_pais_e_de_novo(tmp_path, monkeypatch, func, folder):
    """Each folder is exactly <home>/<folder>, created with any missing parent, and a second call
    finds it there without error."""
    from dataforge import compat
    home = tmp_path / "nao" / "existe" / "ainda"
    monkeypatch.setattr(compat, "data_dir", lambda: home)  # a home nobody created yet
    first = getattr(compat, func)()
    assert first == home / folder
    assert first.is_dir()
    assert getattr(compat, func)() == home / folder


def test_run_lock_path_de_novo_no_mesmo_workspace(tmp_path):
    """A second lock path for the same workspace (its locks/ already there) is the same file."""
    from dataforge.compat import run_lock_path
    ws = tmp_path / "home" / "workspaces" / "ws"
    expected = (tmp_path / "home" / "locks" / "ws.lock").resolve()
    assert run_lock_path(ws) == expected
    assert run_lock_path(ws) == expected


@pytest.mark.parametrize(("raw", "expected"), [
    (None, 60.0),       # unset: the default
    ("5", 5.0),
    ("2.5", 2.5),
    ("0", 0.0),         # zero is valid: stop as soon as nothing is open
    ("-1", 60.0),       # negative: the default
    ("-0.5", 60.0),
    ("abc", 60.0),      # not a number: the default
    ("", 60.0),
])
def test_grace_seconds(monkeypatch, raw, expected):
    from dataforge.compat import GRACE_ENV, grace_seconds
    if raw is None:
        monkeypatch.delenv(GRACE_ENV, raising=False)
    else:
        monkeypatch.setenv(GRACE_ENV, raw)
    value = grace_seconds(60.0)
    assert value == expected
    assert type(value) is float


def test_grace_seconds_usa_o_default_que_recebe(monkeypatch):
    from dataforge.compat import GRACE_ENV, grace_seconds
    monkeypatch.delenv(GRACE_ENV, raising=False)
    assert grace_seconds(7.0) == 7.0
    monkeypatch.setenv(GRACE_ENV, "x")
    assert grace_seconds(7.0) == 7.0


@pytest.mark.parametrize("platform", ["linux", "win32"])
@pytest.mark.parametrize("display", [None, ":0"])
@pytest.mark.parametrize("wayland", [None, "wayland-0"])
@pytest.mark.parametrize("qpa", [None, "xcb"])
def test_force_offscreen_without_display(monkeypatch, platform, display, wayland, qpa):
    """Only Linux with neither DISPLAY nor WAYLAND_DISPLAY switches Qt to offscreen (over any value
    already there); anything else leaves QT_QPA_PLATFORM as it was."""
    import os
    import types
    from dataforge import compat
    monkeypatch.setattr(compat, "sys", types.SimpleNamespace(platform=platform))  # not the real sys
    for name, value in (("DISPLAY", display), ("WAYLAND_DISPLAY", wayland), ("QT_QPA_PLATFORM", qpa)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    compat.force_offscreen_without_display()
    headless = platform == "linux" and display is None and wayland is None
    assert os.environ.get("QT_QPA_PLATFORM") == ("offscreen" if headless else qpa)


def test_set_api_port_grava_a_variavel(monkeypatch):
    import os
    from dataforge.compat import PORT_ENV, set_api_port
    monkeypatch.delenv(PORT_ENV, raising=False)
    set_api_port(18556)
    assert os.environ[PORT_ENV] == "18556"


def test_set_data_dir_aceita_path(tmp_path, monkeypatch):
    import os
    from dataforge.compat import set_data_dir
    monkeypatch.delenv("DATAFORGE_HOME", raising=False)
    set_data_dir(tmp_path / "h")
    assert os.environ["DATAFORGE_HOME"] == str((tmp_path / "h").resolve())
