from unittest.mock import patch

import httpx
import pytest
import uvicorn
from typer.testing import CliRunner

from dataforge import cli, instance
from dataforge.cli import app

runner = CliRunner()


def test_validate_missing_workspace(tmp_path, monkeypatch):
    """validate with no workspace.yaml in CWD and no --workspace flag must exit non-zero."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["validate"])
    assert result.exit_code != 0


def test_validate_with_workspace_flag(tmp_path, monkeypatch):
    """validate --workspace <name> resolves workspace when workspaces/<name>/ exists under CWD."""
    # resolve_workspace("myws") returns Path("workspaces") / "myws" — relative to CWD
    ws_dir = tmp_path / "workspaces" / "myws"
    ws_dir.mkdir(parents=True)
    (ws_dir / "workspace.yaml").write_text(
        "name: myws\nversion: '1.0'\nengine: duckdb\ndatabase: sqlite\n"
    )
    (ws_dir / "jobs").mkdir()

    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["validate", "--workspace", "myws"])
    # connections.yaml missing → exit 1, but workspace.yaml parses correctly
    # Either 0 (everything valid) or 1 (connections missing) — must not crash
    assert result.exit_code in (0, 1)
    # workspace line must appear in output
    assert "myws" in result.output


def test_jobs_list_no_workspace(tmp_path, monkeypatch):
    """jobs list with no workspace context must exit non-zero."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["jobs", "list"])
    assert result.exit_code != 0


def test_init_creates_workspace(tmp_path, monkeypatch):
    """init <name> creates workspaces/<name>/workspace.yaml under data_dir()."""
    monkeypatch.setenv("DATAFORGE_HOME", str(tmp_path))
    result = runner.invoke(app, ["init", "myws"])
    assert result.exit_code == 0
    assert (tmp_path / "workspaces" / "myws" / "workspace.yaml").exists()


def test_cli_run_reports_skipped_and_succeeds_when_no_leaf_failure(
    tmp_path, monkeypatch
):
    """t1 succeeds; t2 has `when: failed` so it must be skipped.
    Exit 0 (no int task failed) and t2 must show neutral PULADO label,
    not FALHOU."""
    ws = tmp_path / "workspaces" / "ws"
    (ws / "jobs" / "j").mkdir(parents=True)
    (ws / "scripts").mkdir()
    (ws / "logs").mkdir()
    (ws / "workspace.yaml").write_text(
        "name: ws\nversion: '1.0'\nengine: duckdb\ndatabase: sqlite\n"
    )
    (ws / "scripts" / "ok.py").write_text("print('ok')\n")
    (ws / "scripts" / "fail.py").write_text("import sys; sys.exit(1)\n")
    (ws / "jobs" / "j" / "job.yaml").write_text(
        "name: j\n"
        "tasks:\n"
        "  - name: t1\n    script: ok.py\n"
        "    retry: {attempts: 1, delay_seconds: 0}\n"
        "  - name: t2\n    script: fail.py\n"
        "    depends_on: [{task: t1, when: failed}]\n"
        "    retry: {attempts: 1, delay_seconds: 0}\n"
    )
    monkeypatch.setenv("DATAFORGE_HOME", str(tmp_path))

    result = runner.invoke(app, ["run", "ws", "j"])
    assert "t2" in result.stdout
    assert "PULADO" in result.stdout
    t2_line = next(line for line in result.stdout.splitlines() if "t2:" in line)
    assert "FALHOU" not in t2_line
    assert result.exit_code == 0


# --- df-h864: the exit code follows the run's status --------------------------------------------

# fetch fails and takes its chain down with it; calendar is an independent root that succeeds.
_UNHANDLED = (
    "  - name: fetch\n    script: fail.py\n"
    "  - name: calendar\n    script: ok.py\n"
    "  - name: transform\n    script: ok.py\n    depends_on: [fetch]\n"
    "  - name: report\n    script: ok.py\n    depends_on: [transform, calendar]\n"
)
# Same failure, but a `when: failed` dependent takes it over (the alert pattern, D10).
_HANDLED = _UNHANDLED + (
    "  - name: notify\n    script: ok.py\n    depends_on: [{task: fetch, when: failed}]\n"
)


def _no_retry(tasks_yaml):
    """Give every task one attempt and no delay (a task does not inherit the job's retry)."""
    return tasks_yaml.replace("\n    script:", "\n    retry: {attempts: 1, delay_seconds: 0}\n    script:")


def _workspace_with_job(tmp_path, monkeypatch, tasks_yaml):
    """DATAFORGE_HOME=tmp_path with workspace `ws`, job `j` and a trigger `daily` that runs it."""
    ws = tmp_path / "workspaces" / "ws"
    for d in ("jobs/j", "scripts", "logs", "triggers"):
        (ws / d).mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")
    (ws / "scripts" / "ok.py").write_text("print('ok')\n")
    (ws / "scripts" / "fail.py").write_text("import sys; sys.exit(1)\n")
    (ws / "jobs" / "j" / "job.yaml").write_text(
        "name: j\ntasks:\n" + _no_retry(tasks_yaml))
    (ws / "triggers" / "daily.yaml").write_text(
        "name: daily\ncron: '0 9 * * *'\nenabled: true\njobs:\n"
        "  - {workspace: ws, job: j, params: {}}\n")
    monkeypatch.setenv("DATAFORGE_HOME", str(tmp_path))
    return ws


def _recorded_statuses(tmp_path):
    from sqlalchemy.orm import Session
    from dataforge.compat import db_url
    from dataforge.db import JobRunORM, init_db
    with Session(init_db(db_url(tmp_path / "dataforge.db"))) as s:
        return [r.status for r in s.query(JobRunORM).all()]


_COMMANDS = {
    "run": ["run", "ws", "j"],
    "scheduled-run": ["scheduled-run", "ws", "daily"],
    "jobs run": ["jobs", "run", "j", "--workspace", "ws"],
}


@pytest.mark.parametrize("command", list(_COMMANDS))
def test_a_failure_nobody_takes_over_exits_1(tmp_path, monkeypatch, command):
    """The 2026-11-12 incident: every leaf skipped behind a failed fetch exited 0 under
    scheduled-run, so the OS scheduler recorded success."""
    _workspace_with_job(tmp_path, monkeypatch, _UNHANDLED)
    with patch("httpx.post"):
        result = runner.invoke(app, _COMMANDS[command])
    assert result.exit_code == 1, result.output
    if command != "jobs run":   # jobs run writes no global JobRunORM row
        assert _recorded_statuses(tmp_path) == ["failed"]


@pytest.mark.parametrize("command", list(_COMMANDS))
def test_a_failure_a_handler_takes_over_exits_0(tmp_path, monkeypatch, command):
    """A handled failure is a successful run (D10), and the exit code agrees with job_runs."""
    _workspace_with_job(tmp_path, monkeypatch, _HANDLED)
    with patch("httpx.post"):
        result = runner.invoke(app, _COMMANDS[command])
    assert result.exit_code == 0, result.output
    assert "FALHOU" in result.output or command == "scheduled-run"
    if command != "jobs run":
        assert _recorded_statuses(tmp_path) == ["success"]


def test_scheduled_run_exits_1_when_a_job_entry_raises(tmp_path, monkeypatch):
    """A trigger entry naming a job that does not exist is logged and fails the process."""
    ws = _workspace_with_job(tmp_path, monkeypatch, _HANDLED)
    (ws / "triggers" / "daily.yaml").write_text(
        "name: daily\ncron: '0 9 * * *'\nenabled: true\njobs:\n"
        "  - {workspace: ws, job: j, params: {}}\n"
        "  - {workspace: ws, job: missing, params: {}}\n")
    with patch("httpx.post") as notify:
        result = runner.invoke(app, ["scheduled-run", "ws", "daily"])
    assert result.exit_code == 1
    notify.assert_called_once()   # the daemon is still told the trigger ran


def _ok_job_workspace(home):
    ws = home / "workspaces" / "ws"
    (ws / "jobs" / "j").mkdir(parents=True)
    (ws / "scripts").mkdir()
    (ws / "logs").mkdir()
    (ws / "workspace.yaml").write_text("name: ws\nversion: '1.0'\nengine: duckdb\ndatabase: sqlite\n")
    (ws / "scripts" / "ok.py").write_text("print('ok')\n")
    (ws / "jobs" / "j" / "job.yaml").write_text(
        "name: j\ntasks:\n  - name: t1\n    script: ok.py\n    retry: {attempts: 1, delay_seconds: 0}\n"
    )
    return ws


@pytest.mark.parametrize("args", [["run", "ws", "j"], ["jobs", "run", "j", "-w", "ws"]])
def test_cli_run_avisa_que_espera_outro_job(tmp_path, monkeypatch, args):
    """Another run holds the workspace lock: the CLI says it is waiting instead of hanging silently."""
    import threading
    from dataforge.runlock import workspace_run_lock
    monkeypatch.setenv("DATAFORGE_HOME", str(tmp_path))
    ws = _ok_job_workspace(tmp_path)
    told = threading.Event()
    real = cli.rprint

    def spy(*a, **k):
        if any("aguardando" in str(x) for x in a):
            told.set()
        real(*a, **k)

    monkeypatch.setattr(cli, "rprint", spy)
    held, release = threading.Event(), threading.Event()

    def holder():
        with workspace_run_lock(ws, "outro-run", poll_seconds=0.05):
            held.set()
            told.wait(10)  # keep the lock until the CLI said it waits (or give up)
            release.set()

    t = threading.Thread(target=holder, daemon=True)
    t.start()
    assert held.wait(5)
    result = runner.invoke(app, args)
    t.join(10)
    assert result.exit_code == 0, result.output
    assert told.is_set()
    assert result.output.count("aguardando outro job do workspace terminar") == 1
    assert "t1" in result.output


def test_cli_run_sem_espera_nao_avisa(tmp_path, monkeypatch):
    monkeypatch.setenv("DATAFORGE_HOME", str(tmp_path))
    _ok_job_workspace(tmp_path)
    result = runner.invoke(app, ["run", "ws", "j"])
    assert result.exit_code == 0, result.output
    assert "aguardando" not in result.output


# --- dataforge stop (Task 7) ---

NONE = (instance.Probe.NONE, None)
OURS = (instance.Probe.OURS, {})


def _fake_post(monkeypatch, response=None, exc=None, probes=(OURS, NONE)):
    """Fake httpx.post and instance.probe (successive answers; the last one repeats)."""
    calls = []
    answers = list(probes)

    def post(url, **kwargs):
        calls.append((url, kwargs))
        if exc:
            raise exc
        return response

    def probe(*_a, **_k):
        return answers.pop(0) if len(answers) > 1 else answers[0]

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(instance, "probe", probe)
    monkeypatch.setattr(cli, "_STOP_WAIT_SECONDS", 0.3)
    return calls


def test_stop_sem_servidor(monkeypatch):
    calls = _fake_post(monkeypatch, probes=(NONE,))
    result = runner.invoke(app, ["stop"])
    assert result.exit_code == 0
    assert "nenhum DataForge rodando" in result.output
    assert calls == []


def test_stop_nao_posta_em_outro_programa(monkeypatch):
    calls = _fake_post(monkeypatch, probes=((instance.Probe.OTHER_PROGRAM, None),))
    result = runner.invoke(app, ["stop"])
    assert result.exit_code == 1
    assert "ocupada por outro programa" in result.output
    assert calls == []


def test_stop_nao_posta_em_outro_home(monkeypatch):
    calls = _fake_post(monkeypatch, probes=((instance.Probe.OTHER_HOME, {"home": "/x"}),))
    result = runner.invoke(app, ["stop"])
    assert result.exit_code == 1
    assert "é de outro DataForge" in result.output
    assert calls == []


def test_stop_desliga_outra_versao_do_mesmo_home(monkeypatch):
    calls = _fake_post(
        monkeypatch, response=httpx.Response(202, json={"ok": True}),
        probes=((instance.Probe.OTHER_VERSION, {}), NONE),
    )
    result = runner.invoke(app, ["stop"])
    assert result.exit_code == 0
    assert len(calls) == 1


def test_stop_desliga(monkeypatch):
    monkeypatch.setenv("DATAFORGE_PORT", "18999")
    calls = _fake_post(monkeypatch, response=httpx.Response(202, json={"ok": True}))
    result = runner.invoke(app, ["stop"])
    assert result.exit_code == 0
    assert "DataForge desligado" in result.output
    url, kwargs = calls[0]
    assert url == "http://127.0.0.1:18999/api/instance/shutdown"
    assert kwargs["params"] == {"force": "false"}
    assert kwargs["trust_env"] is False


def test_stop_force_envia_force(monkeypatch):
    calls = _fake_post(monkeypatch, response=httpx.Response(202, json={"ok": True}))
    assert runner.invoke(app, ["stop", "--force"]).exit_code == 0
    assert calls[0][1]["params"] == {"force": "true"}


def test_stop_com_job_rodando_falha_sem_force(monkeypatch):
    _fake_post(monkeypatch, response=httpx.Response(409, json={"detail": "há job rodando"}))
    result = runner.invoke(app, ["stop"])
    assert result.exit_code == 1
    assert "há job rodando" in result.output


@pytest.mark.parametrize("exc", [httpx.ReadTimeout("slow"), httpx.RemoteProtocolError("bad")])
def test_stop_erro_de_servidor_vivo_nao_vira_nenhum_rodando(monkeypatch, exc):
    _fake_post(monkeypatch, exc=exc)
    result = runner.invoke(app, ["stop"])
    assert result.exit_code == 1
    assert "nenhum DataForge rodando" not in result.output


def test_stop_porta_continua_ocupada_nao_diz_desligado(monkeypatch):
    _fake_post(monkeypatch, response=httpx.Response(202, json={"ok": True}), probes=(OURS,))
    result = runner.invoke(app, ["stop"])
    assert result.exit_code == 1
    assert "DataForge desligado" not in result.output
    assert "ainda" in result.output


# --- dataforge serve: shutdown handler and uvicorn.run parity ---

class _FakeServer:
    started = True
    exc = None

    def __init__(self, config):
        self.config = config
        self.seen = {}
        type(self).last = self

    def run(self):
        self.seen["handled"] = instance.request_shutdown()
        self.seen["exit"] = self.should_exit
        if type(self).exc:
            raise type(self).exc


@pytest.fixture
def fake_server(monkeypatch, tmp_path):
    monkeypatch.setenv("DATAFORGE_HOME", str(tmp_path))
    _FakeServer.started = True
    _FakeServer.exc = None
    _FakeServer.should_exit = False
    if hasattr(_FakeServer, "last"):
        del _FakeServer.last
    monkeypatch.setattr(uvicorn, "Server", _FakeServer)
    return _FakeServer


def test_serve_registers_a_handler_that_stops_the_server(fake_server):
    result = runner.invoke(app, ["serve", "--port", "18998"])
    assert result.exit_code == 0, result.output
    assert fake_server.last.seen["handled"] is True
    assert fake_server.last.seen["exit"] is True
    assert instance.request_shutdown() is False  # handler cleared afterwards


def test_serve_ctrl_c_sai_com_zero(fake_server):
    fake_server.exc = KeyboardInterrupt
    result = runner.invoke(app, ["serve", "--port", "18998"])
    assert result.exit_code == 0, result.output
    assert instance.request_shutdown() is False


def test_serve_que_nao_subiu_sai_com_3(fake_server):
    fake_server.started = False
    result = runner.invoke(app, ["serve", "--port", "18998"])
    assert result.exit_code == 3
    assert instance.request_shutdown() is False


@pytest.mark.parametrize("before", [None, "8000"])
def test_serve_porta_vale_para_os_backends(fake_server, monkeypatch, before):
    """--port reaches DATAFORGE_PORT, so app backends get the right DATAFORGE_API (and clients find it)."""
    import os
    from dataforge import compat
    if before is None:
        monkeypatch.delenv(compat.PORT_ENV, raising=False)
    else:
        monkeypatch.setenv(compat.PORT_ENV, before)
    result = runner.invoke(app, ["serve", "--port", "18998"])
    assert result.exit_code == 0, result.output
    assert os.environ[compat.PORT_ENV] == "18998"
    assert compat.api_port() == 18998


def test_serve_reload_nao_registra_handler(fake_server, monkeypatch):
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: calls.append((a, k)))
    result = runner.invoke(app, ["serve", "--port", "18998", "--reload"])
    assert result.exit_code == 0, result.output
    assert calls == [(("dataforge.api:app",), {"host": "127.0.0.1", "port": 18998, "reload": True})]
    assert not hasattr(fake_server, "last")  # no uvicorn.Server was built
    assert instance.request_shutdown() is False


def test_cli_app_inexistente(dataforge_home, monkeypatch):
    from unittest.mock import MagicMock
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")  # the window process creates a QApplication first
    box = MagicMock()
    monkeypatch.setattr("dataforge.gui.app_window.QMessageBox.critical", box)  # modal: would block the test
    result = CliRunner().invoke(app, ["app", "nao_existe", "x"])
    assert result.exit_code == 1
    assert "nao_existe" in result.output
    assert "nao_existe" in box.call_args.args[2]


def test_cli_app_chama_janela_e_home(tmp_path, monkeypatch):
    monkeypatch.delenv("DATAFORGE_HOME", raising=False)
    seen = {}

    def fake(ws, name):
        import os
        seen.update(ws=ws, name=name, home=os.environ.get("DATAFORGE_HOME"))
        return 0

    monkeypatch.setattr("dataforge.gui.app_window.run_app_window", fake)
    result = CliRunner().invoke(app, ["app", "w", "a", "--home", str(tmp_path)])
    assert result.exit_code == 0
    assert seen == {"ws": "w", "name": "a", "home": str(tmp_path)}
    monkeypatch.delenv("DATAFORGE_HOME", raising=False)
