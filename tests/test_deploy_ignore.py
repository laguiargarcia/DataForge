from pathlib import Path
from dataforge.deploy.ignore import DeployIgnore


def _mk(ws: Path, rel: str, content: str) -> Path:
    p = ws / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


def test_missing_file_matches_nothing(tmp_path):
    ig = DeployIgnore.load(tmp_path)
    assert ig.matches("scripts/x.py") is False


def test_glob_and_dir_patterns(tmp_path):
    _mk(tmp_path, "deploy/.deployignore",
        "*.duckdb\nraw/\nconnections/connections.yaml\n")
    ig = DeployIgnore.load(tmp_path)
    assert ig.matches("cleansed.duckdb") is True
    assert ig.matches("raw/accounts/data.parquet") is True
    assert ig.matches("connections/connections.yaml") is True
    assert ig.matches("scripts/landing/landing2raw.py") is False


def test_negation_supported(tmp_path):
    _mk(tmp_path, "deploy/.deployignore", "config/*\n!config/keep.yaml\n")
    ig = DeployIgnore.load(tmp_path)
    assert ig.matches("config/secret.yaml") is True
    assert ig.matches("config/keep.yaml") is False


def test_pycache_ignored_by_default_without_file(tmp_path):
    # No .deployignore at all — Python build artifacts must still be excluded so
    # resolve_pairs's scripts/**/* glob never promotes a .pyc to the target.
    ig = DeployIgnore.load(tmp_path)
    assert ig.matches("scripts/cleansed2curated/__pycache__/dim_calendar.cpython-312.pyc") is True
    assert ig.matches("scripts/x.pyc") is True
    assert ig.matches("scripts/x.pyo") is True
    assert ig.matches("scripts/landing/landing2raw.py") is False


def test_pycache_default_survives_custom_file(tmp_path):
    # A workspace .deployignore with unrelated lines doesn't drop the built-in defaults.
    _mk(tmp_path, "deploy/.deployignore", "scripts/report/\n")
    ig = DeployIgnore.load(tmp_path)
    assert ig.matches("scripts/cleansed2curated/__pycache__/x.pyc") is True
    assert ig.matches("scripts/report/export_bridge.py") is True


def test_default_pycache_ignore_is_negatable(tmp_path):
    # User file lines apply after defaults, so an explicit re-include still wins.
    _mk(tmp_path, "deploy/.deployignore", "!keep.pyc\n")
    ig = DeployIgnore.load(tmp_path)
    assert ig.matches("scripts/x.pyc") is True
    assert ig.matches("keep.pyc") is False


def test_web_build_artifacts_ignored_by_default(tmp_path):
    # df-ve7: apps/** pairs must never promote node dependencies or Playwright output.
    ig = DeployIgnore.load(tmp_path)
    assert ig.matches("apps/financas/node_modules/react/index.js") is True
    assert ig.matches("apps/financas/test-results/.last-run.json") is True
    assert ig.matches("apps/financas/dist/index.html") is False
    assert ig.matches("apps/financas/src/App.tsx") is False
