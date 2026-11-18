"""The commit hooks run the leak guard on the staged files and on the commit message (df-bsb2.2.2).

This reads the repo's own hook config, so it lives apart from tests/test_leakguard.py: the guard's
mutation run copies only what the guard's code needs, not the repo config.
"""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PRE_COMMIT_CONFIG = ROOT / ".pre-commit-config.yaml"


def test_pre_commit_checks_the_commit_message_with_the_guard():
    """A real value typed in a commit message is refused at commit time, not only by --history."""
    config = yaml.safe_load(PRE_COMMIT_CONFIG.read_text(encoding="utf-8"))
    hooks = {h["id"]: h for repo in config["repos"] for h in repo["hooks"]}
    assert "commit-msg" in config["default_install_hook_types"]
    assert hooks["leakguard-message"]["entry"] == hooks["leakguard"]["entry"] == "python scripts/leakguard.py"
    assert hooks["leakguard-message"]["stages"] == ["commit-msg"]
    # Only the message hook runs on the message: the file hooks stay at the commit's own stage.
    assert config["default_stages"] == ["pre-commit"]
    assert "tools/leakguard.py" not in PRE_COMMIT_CONFIG.read_text(encoding="utf-8")
    assert "ALLOWLIST" not in (ROOT / ".gitleaks.toml").read_text(encoding="utf-8")
