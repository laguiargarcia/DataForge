"""The commit hooks run the leak guard on the staged files and on the commit message (df-bsb2.2.2).

This reads the repo's own hook config, so it lives apart from tests/test_leakguard.py: the guard's
mutation run copies only what the guard's code needs, not the repo config.
"""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PRE_COMMIT_CONFIG = ROOT / ".pre-commit-config.yaml"
GUARD_ENTRY = "uv run --frozen python scripts/leakguard.py"


def test_pre_commit_checks_the_commit_message_with_the_guard():
    """A real value typed in a commit message is refused at commit time, not only by --history."""
    config = yaml.safe_load(PRE_COMMIT_CONFIG.read_text(encoding="utf-8"))
    hooks = {h["id"]: h for repo in config["repos"] for h in repo["hooks"]}
    assert "commit-msg" in config["default_install_hook_types"]
    assert hooks["leakguard-message"]["entry"] == hooks["leakguard"]["entry"] == GUARD_ENTRY
    assert hooks["leakguard-message"]["stages"] == ["commit-msg"]
    # Only the message hook runs on the message: the file hooks stay at the commit's own stage.
    assert config["default_stages"] == ["pre-commit"]
    assert "tools/leakguard.py" not in PRE_COMMIT_CONFIG.read_text(encoding="utf-8")
    assert "ALLOWLIST" not in (ROOT / ".gitleaks.toml").read_text(encoding="utf-8")


def test_guard_hooks_run_in_the_project_venv_so_polars_comes_only_from_uv_lock():
    """No hook pins polars: a second pin drifts from uv.lock and the guard breaks only at someone's commit (df-08kz)."""
    config = yaml.safe_load(PRE_COMMIT_CONFIG.read_text(encoding="utf-8"))
    hooks = {h["id"]: h for repo in config["repos"] for h in repo["hooks"]}
    for hook in hooks.values():
        assert not any("polars" in dep for dep in hook.get("additional_dependencies", []))
    for hook_id in ("leakguard", "leakguard-message"):
        assert hooks[hook_id]["entry"] == GUARD_ENTRY
        assert hooks[hook_id]["language"] == "unsupported"
