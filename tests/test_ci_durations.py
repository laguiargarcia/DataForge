"""scripts/ci_durations.py: the shards' durations become one file, the whole suite once."""
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ci_durations.py"


def _write(path: Path, data: dict) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_merges_disjoint_shards_into_one_sorted_file(tmp_path):
    a = _write(tmp_path / "a.json", {"tests/t.py::b": 2.0, "tests/t.py::a": 1.0})
    b = _write(tmp_path / "b.json", {"tests/u.py::c": 3.5})
    out = tmp_path / "out" / ".test_durations"
    out.parent.mkdir()
    r = subprocess.run([sys.executable, str(SCRIPT), str(out), str(a), str(b)],
                       capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr
    merged = json.loads(out.read_text(encoding="utf-8"))
    assert merged == {"tests/t.py::a": 1.0, "tests/t.py::b": 2.0, "tests/u.py::c": 3.5}
    assert list(merged) == sorted(merged)
    assert "3 tests from 2 shard file(s)" in r.stdout


def test_without_files_it_prints_usage_and_fails(tmp_path):
    r = subprocess.run([sys.executable, str(SCRIPT), str(tmp_path / "out")], capture_output=True, text=True,
                       check=False)
    assert r.returncode == 2
    assert "SHARD_FILE" in r.stderr
