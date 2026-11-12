"""Host and Origin checks on every HTTP request (no login: DNS rebinding and cross-site POST)."""
import asyncio
import os

import pytest
from typer.testing import CliRunner

from dataforge.api import app as api_app
from dataforge.cli import app as cli_app
from dataforge.compat import allowed_hosts

RUN_URL = "/api/workspaces/x/jobs/j/run"


@pytest.mark.parametrize("url", [
    "/health",
    "/api/workspaces/test_ws/tables/vendas",
    "/apps/test_ws/demo/",
])
def test_foreign_host_is_403(workspace_dir, make_app, test_client, url):
    make_app(workspace_dir, "demo")
    assert test_client.get(url, headers={"Host": "evil.example"}).status_code == 403


@pytest.mark.parametrize("host", [
    "evil.example:8000",
    "127.0.0.1.evil.example",
    "127.0.0.1@evil.example",
    "evil.example:127.0.0.1",
    "[::1",
    "[::2]:8000",
    "",
])
def test_lookalike_host_is_403(test_client, host):
    assert test_client.get("/health", headers={"Host": host}).status_code == 403


@pytest.mark.parametrize("host", [
    "127.0.0.1", "127.0.0.1:8000", "localhost:8000", "LOCALHOST", "[::1]", "[::1]:8000",
])
def test_loopback_host_passes(test_client, host):
    assert test_client.get("/health", headers={"Host": host}).status_code == 200


def test_missing_host_is_403():
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "GET", "path": "/health", "raw_path": b"/health",
             "root_path": "", "scheme": "http", "query_string": b"", "headers": [],
             "http_version": "1.1", "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 8000)}
    asyncio.run(api_app(scope, receive, send))
    assert sent[0]["status"] == 403


def test_extra_host_from_env_passes(test_client, monkeypatch):
    monkeypatch.setenv("DATAFORGE_ALLOWED_HOSTS", "minha-maquina.lan, outra")
    assert test_client.get("/health", headers={"Host": "minha-maquina.lan:8000"}).status_code == 200
    assert test_client.get("/health", headers={"Host": "outra"}).status_code == 200
    assert test_client.get("/health", headers={"Host": "testserver"}).status_code == 403


def test_allowed_hosts_defaults_to_loopback(monkeypatch):
    monkeypatch.delenv("DATAFORGE_ALLOWED_HOSTS")
    assert allowed_hosts() == {"127.0.0.1", "localhost", "::1"}


def test_allowed_hosts_adds_names_and_star_disables(monkeypatch):
    monkeypatch.setenv("DATAFORGE_ALLOWED_HOSTS", "A.lan, ,b")
    assert allowed_hosts() == {"127.0.0.1", "localhost", "::1", "a.lan", "b"}
    monkeypatch.setenv("DATAFORGE_ALLOWED_HOSTS", "a.lan,*")
    assert allowed_hosts() is None


def test_post_from_other_site_is_403(test_client):
    assert test_client.post(RUN_URL, headers={"Origin": "http://evil.example"}).status_code == 403


def test_post_from_same_host_other_port_is_403(test_client):
    assert test_client.post(RUN_URL, headers={"Origin": "http://testserver:9999"}).status_code == 403


def test_post_with_origin_equal_to_host_is_not_403(test_client):
    assert test_client.post(RUN_URL, headers={"Origin": "http://testserver"}).status_code != 403
    r = test_client.post(RUN_URL, headers={"Host": "127.0.0.1:8000", "Origin": "http://127.0.0.1:8000"})
    assert r.status_code != 403


def test_post_without_origin_is_not_403(test_client):
    assert test_client.post(RUN_URL).status_code != 403


@pytest.mark.parametrize("origin", ["null", "testserver", "http://testserver/x", "http://u@testserver"])
def test_post_with_opaque_or_malformed_origin_is_403(test_client, origin):
    assert test_client.post(RUN_URL, headers={"Origin": origin}).status_code == 403


def test_get_with_other_origin_is_not_refused(test_client):
    assert test_client.get("/health", headers={"Origin": "http://evil.example"}).status_code == 200


def test_star_disables_only_the_host_check(test_client, monkeypatch):
    monkeypatch.setenv("DATAFORGE_ALLOWED_HOSTS", "*")
    assert test_client.get("/health", headers={"Host": "evil.example"}).status_code == 200
    assert test_client.post(RUN_URL, headers={"Origin": "http://other.example"}).status_code == 403
    r = test_client.post(RUN_URL, headers={"Host": "192.0.2.1:8000", "Origin": "http://192.0.2.1:8000"})
    assert r.status_code != 403


@pytest.mark.parametrize("host", ["0.0.0.0", "::"])
def test_serve_on_all_interfaces_warns_and_prints_loopback_url(workspace_dir, make_app, monkeypatch, host):
    make_app(workspace_dir, "demo")
    calls = []
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: calls.append(k))
    out = CliRunner().invoke(cli_app, ["serve", "--host", host, "--port", "8123"]).output
    warnings = [line for line in out.splitlines() if "sem login" in line]
    assert len(warnings) == 1
    assert "rede" in warnings[0]
    assert "http://127.0.0.1:8123/apps/test_ws/demo/" in out
    assert f"http://{host}" not in out
    assert os.environ["DATAFORGE_ALLOWED_HOSTS"] == "*"
    assert calls[0]["host"] == host


def test_serve_with_named_host_keeps_existing_values(monkeypatch):
    monkeypatch.setenv("DATAFORGE_ALLOWED_HOSTS", "ja-existia")
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: None)
    out = CliRunner().invoke(cli_app, ["serve", "--host", "minha-maquina.lan"]).output
    assert "sem login" not in out
    assert "http://minha-maquina.lan:8000" in out
    assert os.environ["DATAFORGE_ALLOWED_HOSTS"] == "ja-existia,minha-maquina.lan"
    assert allowed_hosts() == {"127.0.0.1", "localhost", "::1", "ja-existia", "minha-maquina.lan"}


def test_serve_without_previous_value_sets_only_the_host(monkeypatch):
    monkeypatch.delenv("DATAFORGE_ALLOWED_HOSTS")
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: None)
    CliRunner().invoke(cli_app, ["serve", "--host", "minha-maquina.lan"])
    assert os.environ["DATAFORGE_ALLOWED_HOSTS"] == "minha-maquina.lan"
