"""scripts/prod/update-prod.ps1 (df-n8ac.30): a failed move to another Python leaves prod as it was.

Runs the real script in Windows PowerShell against throwaway venvs. uv is a fake (-Uv) that fails
at the step a test names, so no test downloads anything or touches prod.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="update-prod.ps1 runs in Windows PowerShell")

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "prod" / "update-prod.ps1"

# Logs each call; exits 2 on the subcommand in FAKE_UV_FAIL; `venv` makes a real (pip-less) venv.
FAKE_UV = """
Add-Content -LiteralPath $env:FAKE_UV_LOG -Value ($args -join ' ')
if ($args[0] -eq $env:FAKE_UV_FAIL) { exit 2 }
if ($args[0] -eq 'venv') { & $env:FAKE_UV_PYTHON -m venv --without-pip $args[-1]; exit $LASTEXITCODE }
exit 0
"""


@pytest.fixture
def prod(tmp_path):
    venv = tmp_path / ".venv-prod"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True)
    (venv / "marker.txt").write_text("the old venv", encoding="utf-8")
    (venv / "dataforge-releases").mkdir()
    (venv / "dataforge-releases" / "current.json").write_text("{}", encoding="utf-8")
    uv = tmp_path / "uv.ps1"
    uv.write_text(FAKE_UV, encoding="ascii")
    return {"tmp": tmp_path, "venv": venv, "uv": uv, "log": tmp_path / "uv.log"}


def _update(prod, fail: str) -> subprocess.CompletedProcess:
    # -Python 3.0 differs from any venv this test makes, so the script always moves to it.
    env = {**os.environ, "FAKE_UV_LOG": str(prod["log"]), "FAKE_UV_FAIL": fail,
           "FAKE_UV_PYTHON": sys.executable}
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT),
         "-VenvPath", str(prod["venv"]), "-Wheel", str(prod["tmp"] / "missing.whl"), "-Python", "3.0",
         "-Uv", str(prod["uv"]), "-DataForgeHome", str(prod["tmp"])],
        env=env, capture_output=True, text=True, check=False)


def _assert_as_it_was(prod):
    assert (prod["venv"] / "marker.txt").read_text(encoding="utf-8") == "the old venv"
    state = prod["venv"] / "dataforge-releases"
    assert (state / "current.json").read_text(encoding="utf-8") == "{}"
    assert [p.name for p in (state / "releases").iterdir()] == []
    assert [p.name for p in prod["tmp"].iterdir() if p.name.startswith(".venv-prod.")] == []


@pytest.mark.parametrize("fail", ["self", "python", "venv"])
def test_a_uv_step_that_fails_leaves_the_old_venv(prod, fail):
    r = _update(prod, fail)
    assert r.returncode == 4, r.stdout + r.stderr
    assert "nothing changed" in r.stdout
    _assert_as_it_was(prod)


def test_an_install_that_fails_in_the_new_venv_brings_the_old_one_back(prod):
    r = _update(prod, "none")
    assert r.returncode == 4, r.stdout + r.stderr
    assert "venv --no-project --seed --python 3.0" in prod["log"].read_text(encoding="utf-8")
    _assert_as_it_was(prod)
