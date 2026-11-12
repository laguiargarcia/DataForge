"""`dataforge validate`: each YAML it checks, the errors it collects, and its exit code.

Rich wraps long lines at the console width, so outputs are compared with whitespace collapsed.
"""
import pytest
from typer.testing import CliRunner

from dataforge import cli
from dataforge.cli import app

runner = CliRunner()

WORKSPACE = "name: ws\nversion: '1.0'\nengine: duckdb\ndatabase: sqlite\n"
CONNECTIONS = "connections:\n  local: {type: duckdb, path: data/x.db}\n"


@pytest.fixture
def ws(tmp_path, monkeypatch):
    """A valid workspace in the CWD; returns a writer to change or add its files."""
    monkeypatch.chdir(tmp_path)

    def write(rel, text):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")

    write("workspace.yaml", WORKSPACE)
    write("connections/connections.yaml", CONNECTIONS)
    write("jobs/load/job.yaml", "name: load\ntasks:\n  - {name: a, script: a.py}\n"
                                "  - {name: b, script: b.py, depends_on: [a]}\n")
    write("jobs/main/job.yaml", "name: main\ntasks:\n  - {name: run_load, job: load}\n")
    return write


def validate(*args):
    result = runner.invoke(app, ["validate", *args])
    return result.exit_code, " ".join(result.output.split())


def test_a_valid_workspace_lists_what_it_loaded_and_passes(ws):
    code, out = validate()
    assert code == 0
    for line in ["workspace.yaml — ws (engine=duckdb)", "jobs/ — 2 job(s) carregados",
                 "load — 2 task(s)", "main — 1 task(s)", "connections.yaml — 1 conexão(ões)",
                 "Workspace válido."]:
        assert line in out
    assert "Erros encontrados" not in out


def test_an_unknown_workspace_name_exits_with_the_reason(ws):
    code, out = validate("--workspace", "nope")
    assert code == 1
    assert "Workspace 'nope' not found" in out


def test_a_workspace_field_error_names_the_field(ws):
    ws("workspace.yaml", "version: '1.0'\n")
    code, out = validate()
    assert code == 1
    assert "✗ workspace.yaml: campo 'name' — Field required" in out


def test_a_missing_workspace_yaml_is_reported(ws, tmp_path, monkeypatch):
    """resolve_workspace already refuses a folder without workspace.yaml, so this only happens
    when the file goes away in between; validate still reports it instead of crashing."""
    (tmp_path / "workspace.yaml").unlink()
    monkeypatch.setattr(cli, "resolve_workspace", lambda _name: tmp_path)
    code, out = validate()
    assert code == 1
    assert "✗ workspace.yaml não encontrado" in out


def test_a_task_field_error_names_the_field(ws):
    ws("jobs/load/job.yaml", "name: load\ntasks:\n  - {script: a.py}\n")
    code, out = validate()
    assert code == 1
    assert "✗ jobs/: campo 'name' — Field required" in out


def test_any_other_job_load_error_is_reported_with_its_message(ws):
    ws("jobs/load/job.yaml", "name: load\ntasks: [\n")
    code, out = validate()
    assert code == 1
    assert "✗ jobs/: while parsing" in out


def test_missing_connections_are_reported(ws, tmp_path):
    (tmp_path / "connections" / "connections.yaml").unlink()
    code, out = validate()
    assert code == 1
    assert "✗ connections.yaml: [Errno 2]" in out


def test_a_cycle_is_reported_per_job(ws):
    ws("jobs/load/job.yaml", "name: load\ntasks:\n  - {name: a, script: a.py, depends_on: [b]}\n"
                             "  - {name: b, script: b.py, depends_on: [a]}\n")
    code, out = validate()
    assert code == 1
    assert "✗ job 'load': cycle detected in pipeline dependency graph" in out


def test_a_task_calling_a_missing_job_is_reported(ws):
    ws("jobs/main/job.yaml", "name: main\ntasks:\n  - {name: run_x, job: ghost}\n")
    code, out = validate()
    assert code == 1
    assert "✗ job 'main', task 'run_x': job referenciado 'ghost' não existe" in out


def test_every_error_is_listed_not_just_the_first(ws, tmp_path):
    ws("workspace.yaml", "version: '1.0'\n")
    (tmp_path / "connections" / "connections.yaml").unlink()
    ws("jobs/main/job.yaml", "name: main\ntasks:\n  - {name: run_x, job: ghost}\n")
    code, out = validate()
    assert code == 1
    assert out.count("✗") == 3
