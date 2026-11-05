import pytest
from pathlib import Path
from dataforge.compat import resolve_workspace, resolve_db


def test_resolve_with_env(tmp_path, monkeypatch):
    """DATAFORGE_HOME set → workspace name resolves to DATAFORGE_HOME/workspaces/<name>."""
    monkeypatch.setenv("DATAFORGE_HOME", str(tmp_path))
    ws_dir = tmp_path / "workspaces" / "foo"
    ws_dir.mkdir(parents=True)
    (ws_dir / "workspace.yaml").write_text("name: foo\n")
    result = resolve_workspace("foo")
    assert result == ws_dir


def test_resolve_none_cwd_fallback(tmp_path, monkeypatch):
    """No name, workspace.yaml in CWD → returns CWD."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "workspace.yaml").touch()
    result = resolve_workspace(None)
    assert result == tmp_path


def test_resolve_none_no_yaml_raises(tmp_path, monkeypatch):
    """No name, no workspace.yaml in CWD → raises ValueError."""
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError):
        resolve_workspace(None)


def test_resolve_db_default(tmp_path):
    """No explicit db URL → SQLite URL constructed from workspace path / data/."""
    workspace_path = tmp_path / "ws"
    workspace_path.mkdir()
    result = resolve_db(None, workspace_path)
    assert result.startswith("sqlite:///")
    assert "ws" in result
    assert result.endswith("/data/dataforge.db")


def test_resolve_db_explicit():
    """Explicit db URL → returned unchanged."""
    custom_url = "postgresql://user:pass@localhost/mydb"
    result = resolve_db(custom_url, Path("/irrelevant"))
    assert result == custom_url
