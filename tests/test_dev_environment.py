"""The tools the repo runs from its own venv come from uv.lock (df-n8ac.20).

`uv sync` removes whatever the lock does not list, so a tool installed by hand vanishes on the next
sync: after #73 the shared git hook (`.venv/bin/python -m pre_commit`) failed for every worktree.
CI's venv is exactly `uv sync --frozen`, so a tool missing from the lock fails here first.
"""
import importlib.util

import pytest

# Module -> who runs it from the venv.
_TOOLS = {
    "pre_commit": ".git/hooks/pre-commit (pre-commit install)",
    "pytest": "every test run",
    "mutmut": "scripts/testloop/cli.py mutate",
    "diff_cover": "scripts/testloop/cli.py diff-cover",
    "hypothesis": "the property tests",
    "ruff": "scripts/quality/gate.py",
    "vulture": "scripts/quality/gate.py --full",
    "importlinter": "scripts/quality/gate.py --full",
    "deptry": "scripts/quality/gate.py --full",
}


@pytest.mark.parametrize("module", sorted(_TOOLS))
def test_the_tool_is_in_the_synced_venv(module):
    assert importlib.util.find_spec(module) is not None, f"{module} ({_TOOLS[module]}) is not in uv.lock"
