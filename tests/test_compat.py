import pytest
from pathlib import Path
from unittest.mock import patch
from dataforge.compat import data_dir, db_url, resolve_workspace, resolve_db, list_workspaces


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
