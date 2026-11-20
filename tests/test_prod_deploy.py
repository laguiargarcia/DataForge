"""scripts/prod/deploy.sh (df-n8ac.11): what the post-merge hook deploys, and when it rolls back.

The script runs for real in a throwaway git repository; uv, wslpath and powershell.exe are stubs on
PATH that record their arguments, so no test touches prod, Windows or the network.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.sessions_repo import add_worktree, git, make_repo, write

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="deploy.sh runs in WSL (bash, wslpath)")

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "prod" / "deploy.sh"
MIRROR = SCRIPT.with_name("mirror_workspace.py")

_STUBS = {
    # uv build --wheel --out-dir DIR SRC / uv export ... --output-file FILE
    "uv": """#!/usr/bin/env bash
echo "uv $*" >> "$STUB_LOG"
while [[ $# -gt 0 ]]; do
  case $1 in
    --out-dir) touch "$2/dataforge-0.1.0-py3-none-any.whl"; shift ;;
    --output-file) echo "pyyaml==6.0.3" > "$2"; shift ;;
  esac
  shift
done
""",
    "wslpath": '#!/usr/bin/env bash\necho "W:$2"\n',
    # One exit code per call, from STUB_RCS ("3" or "1 0"...); 0 once they run out.
    "powershell.exe": """#!/usr/bin/env bash
echo "ps $*" >> "$STUB_LOG"
n=$(grep -c '^ps ' "$STUB_LOG")
rcs=($STUB_RCS)
exit "${rcs[$((n-1))]:-0}"
""",
}


@pytest.fixture
def prod(tmp_path):
    """A main checkout holding deploy.sh's inputs, stubs on PATH and a prod config."""
    repo = make_repo(tmp_path)
    write(repo / ".python-version", "3.14\n")
    write(repo / "scripts" / "prod" / "mirror_workspace.py", MIRROR.read_text(encoding="utf-8"))
    write(repo / "workspaces" / "GetFinance" / "scripts" / "a.py", "a = 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "release")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in _STUBS.items():
        (bin_dir / name).write_text(body, encoding="utf-8")
        (bin_dir / name).chmod(0o755)
    home = tmp_path / "prod"
    (home / "workspaces" / "GetFinance").mkdir(parents=True)
    (home / "workspaces" / "GetFinance" / ".env").write_text("prod's own\n", encoding="utf-8")
    config = tmp_path / "prod.env"
    config.write_text(f"PROD_HOME={home}\nPROD_TMP={tmp_path / 'ctmp'}\n", encoding="utf-8")
    # UV: `uv run` exports the real uv's path, which deploy.sh prefers to PATH (df-n8ac.28).
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "UV": str(bin_dir / "uv"),
           "DATAFORGE_PROD_CONFIG": str(config), "STUB_LOG": str(tmp_path / "calls.log"), "STUB_RCS": "0"}
    return {"repo": repo, "env": env, "log": tmp_path / "calls.log", "home": home, "tmp": tmp_path}


def _deploy(cwd: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(SCRIPT)], cwd=cwd, env=env, capture_output=True, text=True, check=False)


def _ps_calls(log: Path) -> list[str]:
    return [line for line in log.read_text(encoding="utf-8").splitlines() if line.startswith("ps ")]


def test_deploys_head_with_the_lock_constraints_and_the_release_python(prod):
    r = _deploy(prod["repo"], prod["env"])
    assert r.returncode == 0, r.stderr
    sha = git(prod["repo"], "rev-parse", "HEAD").strip()
    assert f"deploy: {sha[:12]} OK" in r.stdout
    [call] = _ps_calls(prod["log"])
    work = prod["tmp"] / "ctmp" / f"df-deploy-{sha[:12]}"
    assert f"-File W:{work}/src/scripts/prod/update-prod.ps1" in call   # the release's own update-prod
    assert f"-VenvPath W:{prod['home']}/.venv-prod" in call
    assert f"-Wheel W:{work}/dataforge-0.1.0-py3-none-any.whl" in call
    assert f"-Constraints W:{work}/constraints.txt" in call
    assert "-Python 3.14" in call
    assert "-Requirements" not in call   # every package the workspaces need is in uv.lock
    assert "-Rollback" not in call
    assert (work / "src" / "tracked.txt").is_file()   # the wheel is built from HEAD's tree


def test_a_busy_prod_is_left_alone_not_rolled_back(prod):
    r = _deploy(prod["repo"], {**prod["env"], "STUB_RCS": "3"})
    assert r.returncode == 3
    assert "nothing changed" in r.stderr
    assert len(_ps_calls(prod["log"])) == 1


def test_a_failed_python_move_is_not_rolled_back(prod):
    # update-prod already put the old venv back (exit 4, df-n8ac.30); a rollback would move prod.
    r = _deploy(prod["repo"], {**prod["env"], "STUB_RCS": "4"})
    assert r.returncode == 4
    assert "prod is as it was" in r.stderr
    assert len(_ps_calls(prod["log"])) == 1


def test_a_failed_install_rolls_back(prod):
    r = _deploy(prod["repo"], {**prod["env"], "STUB_RCS": "1 0"})
    assert r.returncode == 1
    calls = _ps_calls(prod["log"])
    assert len(calls) == 2
    assert "-Rollback" in calls[1]
    assert "rolling back" in r.stderr


def test_a_failed_rollback_is_reported(prod):
    r = _deploy(prod["repo"], {**prod["env"], "STUB_RCS": "1 1"})
    assert r.returncode == 1
    assert "the rollback failed too" in r.stderr


def test_a_worktree_never_deploys(prod):
    wt = add_worktree(prod["repo"], "df-x")
    r = _deploy(wt, prod["env"])
    assert (r.returncode, r.stdout) == (0, "")
    assert not prod["log"].exists()


def test_another_branch_of_the_main_checkout_never_deploys(prod):
    git(prod["repo"], "checkout", "-q", "-b", "other")
    r = _deploy(prod["repo"], prod["env"])
    assert (r.returncode, r.stdout) == (0, "")
    assert not prod["log"].exists()


def test_without_the_prod_config_nothing_is_deployed(prod, tmp_path):
    r = _deploy(prod["repo"], {**prod["env"], "DATAFORGE_PROD_CONFIG": str(tmp_path / "missing.env")})
    assert r.returncode == 0
    assert "nothing deployed" in r.stdout
    assert not prod["log"].exists()


def _mirrored(prod) -> Path:
    return prod["home"] / "workspaces" / "GetFinance" / "scripts" / "a.py"


def test_the_workspace_of_the_same_commit_is_mirrored_after_the_install(prod):
    r = _deploy(prod["repo"], prod["env"])
    assert r.returncode == 0, r.stderr
    assert _mirrored(prod).read_text(encoding="utf-8") == "a = 1\n"
    assert (prod["home"] / "workspaces" / "GetFinance" / ".env").read_text(encoding="utf-8") == "prod's own\n"
    assert "1 written" in r.stdout


@pytest.mark.parametrize("rcs", ["3", "4", "1 0"])
def test_no_mirror_when_the_install_did_not_go_through(prod, rcs):
    _deploy(prod["repo"], {**prod["env"], "STUB_RCS": rcs})
    assert not _mirrored(prod).exists()


def test_prod_still_a_git_checkout_installs_the_engine_but_keeps_the_workspace(prod):
    (prod["home"] / "workspaces" / "GetFinance" / ".git").mkdir()
    r = _deploy(prod["repo"], prod["env"])
    assert r.returncode == 0, r.stderr
    assert "still a git checkout" in r.stdout
    assert not _mirrored(prod).exists()
    assert len(_ps_calls(prod["log"])) == 1


def test_a_failed_mirror_rolls_the_engine_back(prod):
    ws = prod["home"] / "workspaces" / "GetFinance"
    shutil.rmtree(ws)
    ws.write_text("a file where the workspace folder goes\n", encoding="utf-8")
    r = _deploy(prod["repo"], prod["env"])
    assert r.returncode == 1
    calls = _ps_calls(prod["log"])
    assert len(calls) == 2
    assert "-Rollback" in calls[1]
    assert "mirror: FAILED" in r.stderr
