"""Black-box tests for scripts/testloop/cli.py (runs the CLI in a subprocess)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

CLI = Path(__file__).resolve().parent.parent / "scripts" / "testloop" / "cli.py"

MOD_SRC = (
    "def classify(x):\n"
    "    if x > 0:\n"
    '        return "pos"\n'
    '    return "nonpos"\n'
)
IF_LINE = 2

TEST_POS = "from sample.mod import classify\n\n\ndef test_pos():\n    assert classify(1) == 'pos'\n"
TEST_FULL = TEST_POS + "\n\ndef test_neg():\n    assert classify(-1) == 'nonpos'\n"
TEST_UNRELATED = "def test_unrelated():\n    assert 1 + 1 == 2\n"

TOML = f"""python = {json.dumps(sys.executable)}
pytest_args = ["-o", "addopts=", "-p", "no:cacheprovider", "-p", "no:randomly"]
check_args = []

[targets."sample/mod.py"]
tests = ["tests/test_mod*.py"]
copy = ["sample/**/*.py"]
"""


def make_repo(root: Path, test_src: str | None, test_name: str = "test_mod.py") -> Path:
    (root / "sample").mkdir()
    (root / "sample" / "__init__.py").write_text("")
    (root / "sample" / "mod.py").write_text(MOD_SRC)
    (root / "testloop.toml").write_text(TOML)
    if test_src is not None:
        (root / "tests").mkdir()
        (root / "tests" / test_name).write_text(test_src)
    return root


@pytest.fixture
def sample_repo(tmp_path):
    return make_repo(tmp_path, TEST_POS)


@pytest.fixture
def sample_repo_full(tmp_path):
    return make_repo(tmp_path, TEST_FULL)


@pytest.fixture
def sample_repo_unrelated_tests(tmp_path):
    return make_repo(tmp_path, TEST_UNRELATED)


@pytest.fixture
def sample_repo_no_tests(tmp_path):
    return make_repo(tmp_path, None)


def run(repo, *args):
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=repo, capture_output=True, text=True, timeout=300,
    )


def result_path(repo, command, module):
    return Path(repo) / ".testloop" / f"{command}-{module.replace('/', '_')}.json"


def read_json(repo, command, module):
    return json.loads(result_path(repo, command, module).read_text())


def run_ok(repo, command, module, *extra):
    proc = run(repo, command, module, *extra)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert Path(proc.stdout.strip()) == result_path(repo, command, module)
    return read_json(repo, command, module)


def test_map_lists_uncovered_branch(sample_repo):
    data = run_ok(sample_repo, "map", "sample/mod.py")
    assert [b["from"] for b in data["uncovered_branches"]] == [IF_LINE]
    assert "tests/test_mod.py::test_pos" in data["line_tests"][str(IF_LINE)]
    assert data["source_sha"].startswith("sha256:")
    assert data["command"] == "map"
    assert data["tests"] == ["tests/test_mod.py"]
    assert data["summary"]["num_branches"] == 2


def test_map_full_coverage_has_no_gaps(sample_repo_full):
    assert run_ok(sample_repo_full, "map", "sample/mod.py")["uncovered_branches"] == []


def test_map_unknown_module_exits_2(sample_repo):
    assert run(sample_repo, "map", "sample/nope.py").returncode == 2


def test_map_module_never_imported_exits_2(sample_repo_unrelated_tests):
    assert run(sample_repo_unrelated_tests, "map", "sample/mod.py").returncode == 2


def test_map_no_matching_tests_exits_2(sample_repo_no_tests):
    assert run(sample_repo_no_tests, "map", "sample/mod.py").returncode == 2


@pytest.mark.parametrize("command", ["map", "check", "mutate"])
def test_module_without_target_entry_exits_2(sample_repo, command):
    (sample_repo / "sample" / "other.py").write_text(MOD_SRC)
    (sample_repo / "tests" / "test_other.py").write_text(TEST_POS)
    proc = run(sample_repo, command, "sample/other.py")
    assert proc.returncode == 2
    assert "sample/other.py" in proc.stderr
    assert "testloop.toml" in proc.stderr
    assert not result_path(sample_repo, command, "sample/other.py").exists()


def test_map_broken_python_exits_2_with_one_line_error(sample_repo):
    toml = sample_repo / "testloop.toml"
    toml.write_text(toml.read_text().replace(sys.executable, "/nonexistent/py"))
    proc = run(sample_repo, "map", "sample/mod.py")
    assert proc.returncode == 2
    assert "/nonexistent/py" in proc.stderr
    assert "Traceback" not in proc.stderr


# ---- check ----

TEST_FAILING = TEST_POS + "\n\ndef test_fails():\n    assert classify(1) == 'nope'\n"
TEST_EMPTY = "# no tests here\n"


@pytest.fixture
def sample_repo_failing(tmp_path):
    return make_repo(tmp_path, TEST_FAILING)


@pytest.fixture
def sample_repo_empty_test_file(tmp_path):
    return make_repo(tmp_path, TEST_EMPTY)


def check_twice(repo):
    out = []
    for _ in range(2):
        run(repo, "check", "sample/mod.py")
        data = read_json(repo, "check", "sample/mod.py")
        data.pop("generated_at")
        out.append(data)
    return out


def test_check_passes(sample_repo):
    data = run_ok(sample_repo, "check", "sample/mod.py")
    assert data["passed"] is True
    assert data["collected"] == 1
    assert data["failed"] == []


def test_check_failure_exits_1_and_names_test(sample_repo_failing):
    result = run(sample_repo_failing, "check", "sample/mod.py")
    assert result.returncode == 1
    data = read_json(sample_repo_failing, "check", "sample/mod.py")
    assert data["passed"] is False
    assert data["failed"] == ["tests/test_mod.py::test_fails"]


def test_check_zero_collected_exits_2_and_never_passes(sample_repo_empty_test_file):
    result = run(sample_repo_empty_test_file, "check", "sample/mod.py")
    assert result.returncode == 2
    data = read_json(sample_repo_empty_test_file, "check", "sample/mod.py")
    assert data["passed"] is False
    assert data["collected"] == 0
    assert data["exit_code"] == 5


def test_check_pytest_crash_exits_2_not_1(sample_repo):
    toml = sample_repo / "testloop.toml"
    toml.write_text(toml.read_text().replace('"no:randomly"]', '"no:randomly", "--no-such-option"]'))
    result = run(sample_repo, "check", "sample/mod.py")
    assert result.returncode == 2
    data = read_json(sample_repo, "check", "sample/mod.py")
    assert data["passed"] is False
    assert data["exit_code"] == 4
    assert data["error"]
    assert "no-such-option" in result.stderr


def test_check_xfail_is_reported_and_passes(sample_repo):
    (sample_repo / "tests" / "test_mod.py").write_text(
        TEST_POS + "\nimport pytest\n\n\n@pytest.mark.xfail(strict=True, reason='df-x')\n"
        "def test_known():\n    assert classify(1) == 'nope'\n"
    )
    data = run_ok(sample_repo, "check", "sample/mod.py")
    assert data["passed"] is True
    assert data["xfailed"] == ["tests/test_mod.py::test_known"]


def test_check_same_result_twice(sample_repo_failing):
    first, second = check_twice(sample_repo_failing)
    assert first == second
    assert first["failed"]


def test_check_all_skipped_exits_2_and_never_passes(sample_repo):
    (sample_repo / "tests" / "test_mod.py").write_text(
        "import pytest\n\n\n@pytest.mark.skip(reason='r')\ndef test_s():\n    assert False\n"
    )
    result = run(sample_repo, "check", "sample/mod.py")
    assert result.returncode == 2
    data = read_json(sample_repo, "check", "sample/mod.py")
    assert data["passed"] is False
    assert data["collected"] == 1
    assert data["skipped"] == ["tests/test_mod.py::test_s"]
    assert "actually ran" in result.stderr


def test_check_skip_mixed_with_pass_still_passes(sample_repo):
    (sample_repo / "tests" / "test_mod.py").write_text(
        TEST_POS + "\nimport pytest\n\n\n@pytest.mark.skip(reason='r')\ndef test_s():\n    pass\n"
    )
    data = run_ok(sample_repo, "check", "sample/mod.py")
    assert data["passed"] is True
    assert data["skipped"] == ["tests/test_mod.py::test_s"]


@pytest.mark.parametrize("command", ["check", "map"])
def test_stale_result_removed_when_next_run_is_tool_error(sample_repo, command):
    run_ok(sample_repo, command, "sample/mod.py")
    assert result_path(sample_repo, command, "sample/mod.py").is_file()
    for f in (sample_repo / "tests").glob("test_mod*.py"):
        f.unlink()
    result = run(sample_repo, command, "sample/mod.py")
    assert result.returncode == 2
    assert "no tests match" in result.stderr
    assert not result_path(sample_repo, command, "sample/mod.py").exists()


# ---------------------------------------------------------------- mutate

TEST_STRONG = TEST_FULL + (
    "\n\ndef test_zero():\n    assert classify(0) == 'nonpos'\n"
)
TEST_SLOW = TEST_POS + (
    "\n\ndef test_slow():\n"
    "    import time\n"
    "    time.sleep(2)\n"
)


@pytest.fixture
def sample_repo_strong(tmp_path):
    return make_repo(tmp_path, TEST_STRONG)


@pytest.fixture
def sample_repo_slow(tmp_path):
    return make_repo(tmp_path, TEST_SLOW)


def snapshot_excluding_testloop(repo):
    import hashlib

    snap = {}
    for p in sorted(Path(repo).rglob("*")):
        rel = p.relative_to(repo)
        if p.is_file() and rel.parts[0] != ".testloop":
            snap[rel.as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return snap


def run_with_tmpdir(repo, tmp_root, *args):
    import os

    tmp_root.mkdir(exist_ok=True)
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=repo, capture_output=True, text=True, timeout=300,
        env={**os.environ, "TMPDIR": str(tmp_root)},
    )


def processes_running_in(root):
    import os

    found = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            if os.readlink(f"/proc/{pid}/cwd").startswith(str(root)):
                found.append(pid)
        except OSError:
            continue
    return found


def test_mutate_reports_survivor_with_diff(sample_repo):        # does not check "nonpos"
    data = run_ok(sample_repo, "mutate", "sample/mod.py")
    assert data["complete"] is True
    assert data["survived"]
    assert data["survived"][0]["diff"]
    assert "@@" in data["survived"][0]["diff"]
    assert data["survived"][0]["id"]
    assert data["total"] == data["killed"] + len(data["survived"]) + len(data["timeout"]) + len(data["not_tested"])
    assert data["score"] == pytest.approx(
        data["killed"] / (data["killed"] + len(data["survived"]) + len(data["not_tested"]))
    )
    assert data["elapsed_s"] >= 0
    assert data["warnings"] == []


def test_mutate_strong_suite_kills_mutants(sample_repo_strong):
    data = run_ok(sample_repo_strong, "mutate", "sample/mod.py")
    assert data["killed"] > 0
    assert data["survived"] == []
    assert data["score"] == 1.0


def test_check_args_reach_map_and_mutate(sample_repo_strong):
    """With no filter this suite covers every branch and kills every mutant (see
    test_mutate_strong_suite_kills_mutants), so a gap proves both commands ran with check_args."""
    toml = sample_repo_strong / "testloop.toml"
    toml.write_text(toml.read_text().replace("check_args = []", 'check_args = ["-k", "test_pos"]'))
    assert [b["from"] for b in run_ok(sample_repo_strong, "map", "sample/mod.py")["uncovered_branches"]] == [IF_LINE]
    assert run_ok(sample_repo_strong, "mutate", "sample/mod.py")["survived"]


def test_mutate_refuses_when_baseline_fails(sample_repo_failing, tmp_path):
    tmp_root = tmp_path / "tmp"
    result = run_with_tmpdir(sample_repo_failing, tmp_root, "mutate", "sample/mod.py")
    assert result.returncode == 2
    assert "baseline" in result.stderr
    assert not result_path(sample_repo_failing, "mutate", "sample/mod.py").exists()
    assert list(tmp_root.iterdir()) == []


def test_mutate_leaves_repo_untouched(sample_repo):
    before = snapshot_excluding_testloop(sample_repo)           # path -> sha
    run_ok(sample_repo, "mutate", "sample/mod.py")
    assert snapshot_excluding_testloop(sample_repo) == before


def test_mutate_cleans_temp_dir_on_success(sample_repo, tmp_path):
    tmp_root = tmp_path / "tmp"
    result = run_with_tmpdir(sample_repo, tmp_root, "mutate", "sample/mod.py")
    assert result.returncode == 0, result.stderr
    assert list(tmp_root.iterdir()) == []


@pytest.mark.parametrize("budget", ["1", "8"])   # 1: dies during baseline; 8: usually mid-mutation
def test_mutate_budget_marks_incomplete(sample_repo_slow, tmp_path, budget):
    tmp_root = tmp_path / "tmp"
    result = run_with_tmpdir(
        sample_repo_slow, tmp_root, "mutate", "sample/mod.py", "--budget-seconds", budget
    )
    assert result.returncode == 2
    assert "budget" in result.stderr
    data = read_json(sample_repo_slow, "mutate", "sample/mod.py")
    assert data["complete"] is False
    assert any("budget" in w for w in data["warnings"])
    # Partial counts never look like a finished score. Survivors found before the cut are
    # reported; how many depends on disk speed, so their number is not asserted.
    assert data["score"] != 1.0
    assert list(tmp_root.iterdir()) == []                    # temp copy removed
    assert processes_running_in(tmp_root) == []              # whole process group killed


# ---------------------------------------------------------------- gate

def set_min_score(repo, value):
    # The target table is the last one in TOML, so an appended key lands in it.
    with (Path(repo) / "testloop.toml").open("a") as f:
        f.write(f"min_score = {value}\n")


def test_gate_passes_at_or_above_min_score(sample_repo_strong):
    set_min_score(sample_repo_strong, 1.0)
    run_ok(sample_repo_strong, "mutate", "sample/mod.py")
    data = run_ok(sample_repo_strong, "gate", "sample/mod.py")
    assert data["passed"] is True
    assert data["score"] == 1.0
    assert data["min_score"] == 1.0


def test_gate_below_min_score_exits_1_and_names_survivors(sample_repo):
    set_min_score(sample_repo, 1.0)
    survivors = [s["id"] for s in run_ok(sample_repo, "mutate", "sample/mod.py")["survived"]]
    proc = run(sample_repo, "gate", "sample/mod.py")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    data = read_json(sample_repo, "gate", "sample/mod.py")
    assert data["passed"] is False
    assert data["score"] < 1.0
    assert data["survived"] == survivors
    assert survivors
    assert all(s in proc.stderr for s in survivors)


def test_gate_without_min_score_exits_2(sample_repo_strong):
    run_ok(sample_repo_strong, "mutate", "sample/mod.py")
    proc = run(sample_repo_strong, "gate", "sample/mod.py")
    assert proc.returncode == 2
    assert "min_score" in proc.stderr


def test_gate_without_mutate_result_exits_2(sample_repo_strong):
    set_min_score(sample_repo_strong, 0.5)
    proc = run(sample_repo_strong, "gate", "sample/mod.py")
    assert proc.returncode == 2
    assert "mutate" in proc.stderr


def test_gate_refuses_a_result_for_older_code(sample_repo_strong):
    set_min_score(sample_repo_strong, 0.5)
    run_ok(sample_repo_strong, "mutate", "sample/mod.py")
    mod = Path(sample_repo_strong) / "sample" / "mod.py"
    mod.write_text(mod.read_text() + "\n# changed\n")
    proc = run(sample_repo_strong, "gate", "sample/mod.py")
    assert proc.returncode == 2
    assert "stale" in proc.stderr


def test_gate_refuses_a_partial_result(sample_repo_strong):
    set_min_score(sample_repo_strong, 0.5)
    run_ok(sample_repo_strong, "mutate", "sample/mod.py")
    path = result_path(sample_repo_strong, "mutate", "sample/mod.py")
    data = json.loads(path.read_text())
    data["complete"] = False
    path.write_text(json.dumps(data))
    proc = run(sample_repo_strong, "gate", "sample/mod.py")
    assert proc.returncode == 2
    assert "partial" in proc.stderr

