import os
from pathlib import Path

import pytest


@pytest.fixture
def app_client(workspace_dir, make_app, test_client):
    app_dir = make_app(workspace_dir, "demo")
    (app_dir / "dist" / "assets").mkdir()
    (app_dir / "dist" / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    return test_client


def test_index_served(app_client):
    r = app_client.get("/apps/test_ws/demo/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_no_trailing_slash_redirects(app_client):
    r = app_client.get("/apps/test_ws/demo", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"].endswith("/apps/test_ws/demo/")


def test_spa_fallback_for_route_without_extension(app_client):
    r = app_client.get("/apps/test_ws/demo/qualquer/rota")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_missing_asset_is_404_not_index(app_client):
    assert app_client.get("/apps/test_ws/demo/assets/nao-existe.js").status_code == 404


def test_js_mime_ignores_windows_registry(app_client, monkeypatch):
    monkeypatch.setattr("mimetypes.guess_type", lambda *a, **k: ("text/plain", None))
    r = app_client.get("/apps/test_ws/demo/assets/app.js")
    assert r.headers["content-type"].startswith("text/javascript")


@pytest.mark.parametrize("path", [
    "..%2f..%2fworkspace.yaml", "%2e%2e/%2e%2e/workspace.yaml", "..%5c..%5cworkspace.yaml",
])
def test_traversal_never_leaves_dist(app_client, path):
    r = app_client.get(f"/apps/test_ws/demo/{path}")
    assert "name: test_ws" not in r.text
    assert r.status_code == 404 or (r.status_code == 200 and r.text == "<html></html>")


def test_unknown_workspace_or_app_is_404(app_client):
    assert app_client.get("/apps/nao/demo/").status_code == 404
    assert app_client.get("/apps/test_ws/nao/").status_code == 404
    assert app_client.get("/apps/..%2f/demo/").status_code == 404


def test_app_without_dist_is_404(workspace_dir, make_app, test_client):
    make_app(workspace_dir, "semdist", dist=False)
    assert test_client.get("/apps/test_ws/semdist/").status_code == 404


def test_new_build_is_served_without_restart(app_client, workspace_dir):
    index = workspace_dir / "apps/demo/dist/index.html"
    index.write_text("novo", encoding="utf-8")
    assert app_client.get("/apps/test_ws/demo/").text == "novo"
    assert "no-cache" in app_client.get("/apps/test_ws/demo/").headers["cache-control"]


@pytest.mark.parametrize("url", [
    "/apps/test_ws/demo/assets/nao-existe.js",  # missing asset
    "/apps/nao/demo/",                           # unknown workspace
    "/apps/test_ws/nao/",                        # unknown app
    "/apps/test_ws/demo/..%2f..%2fworkspace.yaml",
    "/apps/test_ws/demo/%00",                    # NUL
    "/apps/test_ws/demo/C%3A%5Cx",               # drive path
    "/apps/test_ws/demo/%5C%5C192.0.2.1%5Cs%5Cx",  # UNC
])
def test_404_responses_are_no_cache(app_client, url):
    r = app_client.get(url)
    assert r.status_code == 404
    assert "no-cache" in r.headers["cache-control"]


def test_redirect_is_no_cache(app_client):
    r = app_client.get("/apps/test_ws/demo", follow_redirects=False)
    assert "no-cache" in r.headers["cache-control"]


@pytest.mark.skipif(os.name == "nt", reason="a backslash cannot be part of a Windows folder name")
def test_folder_named_with_backslash_is_404(workspace_dir, make_app, test_client):
    app_dir = make_app(workspace_dir, "a\\b")
    assert (app_dir / "dist" / "index.html").is_file()
    assert test_client.get("/apps/test_ws/a%5Cb/").status_code == 404


@pytest.mark.parametrize("segment", ["C%3A%5Cfora", "%5C%5C127.0.0.1%5CC%24%5Cx"])
def test_absolute_app_segment_is_404_without_touching_disk(app_client, monkeypatch, caplog, segment):
    def boom(*a, **k):
        raise AssertionError("app.yaml read for an invalid app name")
    monkeypatch.setattr(Path, "read_text", boom)
    r = app_client.get(f"/apps/test_ws/{segment}/")
    assert r.status_code == 404
    assert "no-cache" in r.headers["cache-control"]
    assert "cannot read app.yaml" not in caplog.text


@pytest.mark.parametrize("path", [
    "%00",
    "a%00b",
    "%5C%5C192.0.2.1%5Cs%5Cx",
    "C%3A%5Cx",
    "C%3A/x",
    "assets%5Capp.js",
    "index.html%3A%3A%24DATA",
    "/etc/passwd",
])
def test_unsafe_path_is_404(app_client, path):
    r = app_client.get(f"/apps/test_ws/demo/{path}")
    assert r.status_code == 404
    assert "no-cache" in r.headers["cache-control"]


def test_unc_path_is_refused_before_resolve(app_client, monkeypatch):
    """On Windows, resolving a UNC path contacts the host; Linux cannot show that effect."""
    def boom(*a, **k):
        raise AssertionError("Path.resolve called for a UNC path")
    monkeypatch.setattr(Path, "resolve", boom)
    assert app_client.get("/apps/test_ws/demo/%5C%5C192.0.2.1%5Cs%5Cx").status_code == 404
