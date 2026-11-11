from typer.testing import CliRunner
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
