import logging
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

import dataforge
from dataforge.apps import discover_apps, load_app
from dataforge.cli import app as cli_app


def test_discover_finds_app_with_dist(workspace_dir, make_app):
    make_app(workspace_dir, "demo", title="Demo")
    apps = discover_apps()
    assert [(a.workspace, a.name, a.title) for a in apps] == [("test_ws", "demo", "Demo")]
    assert apps[0].url_path == "/apps/test_ws/demo/"



def test_discover_walks_every_workspace_and_sorts_by_workspace_then_name(dataforge_home, make_app,
                                                                     caplog):
    """A workspace with no apps/ folder, a stray file and a folder without app.yaml are passed
    over without a warning. A folder with an invalid name is warned about and skipped, and the
    folders after it are still read. The apps found come back sorted across workspaces."""
    def workspace(name):
        ws = dataforge_home / "workspaces" / name
        ws.mkdir()
        (ws / "workspace.yaml").write_text(f"name: {name}\n", encoding="utf-8")
        return ws

    workspace("a_none")                      # no apps/ at all
    b, c = workspace("b_ws"), workspace("c_ws")
    make_app(c, "zeta")
    make_app(c, "alpha")
    make_app(c, "bad name")                  # sorts between alpha and zeta
    make_app(b, "mid")
    (b / "apps" / "README.md").write_text("not an app", encoding="utf-8")
    (b / "apps" / "draft").mkdir()           # no app.yaml yet
    with caplog.at_level(logging.WARNING, logger="dataforge.apps"):
        found = discover_apps()
    assert [(a.workspace, a.name) for a in found] == [("b_ws", "mid"), ("c_ws", "alpha"), ("c_ws", "zeta")]
    assert [r.args[0].name for r in caplog.records] == ["bad name"]

def test_app_without_dist_is_skipped_with_warning(workspace_dir, make_app, caplog):
    make_app(workspace_dir, "semdist", dist=False)
    assert discover_apps() == []
    assert "semdist" in caplog.text


def test_invalid_yaml_is_skipped_with_warning(workspace_dir, make_app, caplog):
    make_app(workspace_dir, "ruim", raw_yaml="a: [")
    assert discover_apps() == []
    assert "ruim" in caplog.text


def test_name_must_match_directory(workspace_dir, make_app, caplog):
    make_app(workspace_dir, "pasta", name="outro")
    assert discover_apps() == []
    assert "pasta" in caplog.text


@pytest.mark.parametrize("name", ["a\\b", "C:\\fora", "..", "a/b", "a b", "demo\n", "a\x00b", ""])
def test_load_app_invalid_name_is_silent_and_never_reads(workspace_dir, monkeypatch, caplog, name):
    def boom(*a, **k):
        raise AssertionError("app.yaml read for an invalid app name")
    monkeypatch.setattr(Path, "read_text", boom)
    caplog.set_level(logging.WARNING, logger="dataforge.apps")
    assert load_app(workspace_dir, name) is None
    assert caplog.records == []


def test_discover_warns_about_folder_with_invalid_name(workspace_dir, make_app, caplog):
    make_app(workspace_dir, "minha pasta")
    assert discover_apps() == []
    assert "minha pasta" in caplog.text


def test_title_defaults_to_name(workspace_dir, make_app):
    make_app(workspace_dir, "demo", title=None)
    assert discover_apps()[0].title == "demo"


def test_startup_survives_bad_app(workspace_dir, make_app, test_client):
    make_app(workspace_dir, "ruim", raw_yaml="a: [")
    with test_client as client:
        assert client.get("/health").status_code == 200


def test_cors_no_longer_allows_other_origins(test_client):
    r = test_client.get("/health", headers={"Origin": "http://evil.example"})
    assert r.status_code == 200
    assert "access-control-allow-origin" not in r.headers


def test_core_has_no_finance_terms():
    pattern = re.compile(r"pluggy|getfinance|financ|fatura|fact_transaction", re.I)
    hits = [p for p in Path(dataforge.__file__).parent.rglob("*.py")
            if pattern.search(p.read_text(encoding="utf-8"))]
    assert hits == []


def test_serve_prints_app_urls(workspace_dir, make_app, monkeypatch):
    make_app(workspace_dir, "demo", title="Demo [v0]")
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: None)
    out = CliRunner().invoke(cli_app, ["serve", "--port", "8123"]).output
    assert "Demo [v0]" in out
    assert "http://127.0.0.1:8123/apps/test_ws/demo/" in out
    assert "nenhum workspace" not in out


def test_serve_warns_when_no_workspace_is_found(tmp_path, monkeypatch):
    # df-pr6: with DATAFORGE_HOME on the wrong folder, serve must say where it looked.
    monkeypatch.setenv("DATAFORGE_HOME", str(tmp_path))
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: None)
    out = CliRunner().invoke(cli_app, ["serve"]).output
    assert "nenhum workspace encontrado" in out
    assert str(tmp_path / "workspaces") in out.replace("\n", "")


def test_serve_binds_dataforge_port_when_no_port_is_given(tmp_path, monkeypatch):
    # df-oift: serve, the GUI daemon and every client must agree on one port.
    monkeypatch.setenv("DATAFORGE_HOME", str(tmp_path))
    monkeypatch.setenv("DATAFORGE_PORT", "8123")
    seen = {}
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: seen.update(k))
    CliRunner().invoke(cli_app, ["serve"])
    assert seen["port"] == 8123


def test_serve_explains_an_invalid_dataforge_port(tmp_path, monkeypatch):
    # df-yvgx: a clear message and exit 1, not a traceback.
    monkeypatch.setenv("DATAFORGE_HOME", str(tmp_path))
    monkeypatch.setenv("DATAFORGE_PORT", "abc")
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: None)
    result = CliRunner().invoke(cli_app, ["serve"])
    assert result.exit_code == 1
    assert "DATAFORGE_PORT must be a port number" in result.output
