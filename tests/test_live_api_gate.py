"""tests/live_api.py's gated job: the test, not a race, decides how its task ends (df-sjde).

On Windows the script once read the release file between its creation and its write, saw it empty,
exited 0, and a run the test released as failed streamed as success."""
import subprocess
import sys
import time

from tests.live_api import _GATED_SCRIPT, release_gated_job


def test_an_empty_release_file_is_not_an_exit_code(tmp_path):
    script = tmp_path / "scripts" / "gated.py"
    script.parent.mkdir()
    script.write_text(_GATED_SCRIPT, encoding="utf-8")
    (script.parent / "release").write_bytes(b"")   # created, not written yet
    proc = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.DEVNULL)
    time.sleep(0.3)
    assert proc.poll() is None   # still waiting for the exit code
    (script.parent / "release").write_bytes(b"1")
    assert proc.wait(timeout=10) == 1


def test_release_leaves_only_the_written_exit_code(tmp_path):
    (tmp_path / "scripts").mkdir()
    release_gated_job(tmp_path, 1)
    assert [p.name for p in (tmp_path / "scripts").iterdir()] == ["release"]
    assert (tmp_path / "scripts" / "release").read_bytes() == b"1"
