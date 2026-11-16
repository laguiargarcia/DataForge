"""scripts/ci_durations.py: the shards' durations become one file, the whole suite once."""
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ci_durations.py"


def _shard(root: Path, name: str, data: dict) -> None:
    path = root / "durations" / name / ".test_durations"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _run(cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT)], cwd=cwd, capture_output=True, text=True, check=False)


def test_merges_disjoint_shards_into_one_sorted_file(tmp_path):
    _shard(tmp_path, "durations-linux-1", {"tests/t.py::b": 2.0, "tests/t.py::a": 1.0})
    _shard(tmp_path, "durations-linux-2", {"tests/u.py::c": 3.5})
    r = _run(tmp_path)
    assert r.returncode == 0, r.stderr
    merged = json.loads((tmp_path / ".test_durations").read_text(encoding="utf-8"))
    assert merged == {"tests/t.py::a": 1.0, "tests/t.py::b": 2.0, "tests/u.py::c": 3.5}
    assert list(merged) == sorted(merged)
    assert "3 tests from 2 shard file(s)" in r.stdout


def test_without_shard_files_it_writes_nothing_and_fails(tmp_path):
    r = _run(tmp_path)
    assert r.returncode == 1
    assert "nothing to merge" in r.stderr
    assert not (tmp_path / ".test_durations").exists()
