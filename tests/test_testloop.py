"""Black-box tests for scripts/testloop/cli.py (runs the CLI in a subprocess)."""

import ast
import functools
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
import tomllib
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
pytest_args = ["-o", "addopts=", "-p", "no:cacheprovider"]
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
    assert data["command"] == "map" and data["tests"] == ["tests/test_mod.py"]
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
    assert "sample/other.py" in proc.stderr and "testloop.toml" in proc.stderr
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
    assert data["passed"] is True and data["collected"] == 1 and data["failed"] == []


def test_check_failure_exits_1_and_names_test(sample_repo_failing):
    result = run(sample_repo_failing, "check", "sample/mod.py")
    assert result.returncode == 1
    data = read_json(sample_repo_failing, "check", "sample/mod.py")
    assert data["passed"] is False and data["failed"] == ["tests/test_mod.py::test_fails"]


def test_check_zero_collected_exits_2_and_never_passes(sample_repo_empty_test_file):
    result = run(sample_repo_empty_test_file, "check", "sample/mod.py")
    assert result.returncode == 2
    data = read_json(sample_repo_empty_test_file, "check", "sample/mod.py")
    assert data["passed"] is False and data["collected"] == 0 and data["exit_code"] == 5


def test_check_pytest_crash_exits_2_not_1(sample_repo):
    toml = sample_repo / "testloop.toml"
    toml.write_text(toml.read_text().replace('"no:cacheprovider"]', '"no:cacheprovider", "--no-such-option"]'))
    result = run(sample_repo, "check", "sample/mod.py")
    assert result.returncode == 2
    data = read_json(sample_repo, "check", "sample/mod.py")
    assert data["passed"] is False and data["exit_code"] == 4 and data["error"]
    assert "no-such-option" in result.stderr


def test_check_xfail_is_reported_and_passes(sample_repo):
    (sample_repo / "tests" / "test_mod.py").write_text(
        TEST_POS + "\nimport pytest\n\n\n@pytest.mark.xfail(strict=True, reason='df-x')\n"
        "def test_known():\n    assert classify(1) == 'nope'\n"
    )
    data = run_ok(sample_repo, "check", "sample/mod.py")
    assert data["passed"] is True and data["xfailed"] == ["tests/test_mod.py::test_known"]


def test_check_same_result_twice(sample_repo_failing):
    first, second = check_twice(sample_repo_failing)
    assert first == second and first["failed"]


def test_check_all_skipped_exits_2_and_never_passes(sample_repo):
    (sample_repo / "tests" / "test_mod.py").write_text(
        "import pytest\n\n\n@pytest.mark.skip(reason='r')\ndef test_s():\n    assert False\n"
    )
    result = run(sample_repo, "check", "sample/mod.py")
    assert result.returncode == 2
    data = read_json(sample_repo, "check", "sample/mod.py")
    assert data["passed"] is False and data["collected"] == 1
    assert data["skipped"] == ["tests/test_mod.py::test_s"] and "actually ran" in result.stderr


def test_check_skip_mixed_with_pass_still_passes(sample_repo):
    (sample_repo / "tests" / "test_mod.py").write_text(
        TEST_POS + "\nimport pytest\n\n\n@pytest.mark.skip(reason='r')\ndef test_s():\n    pass\n"
    )
    data = run_ok(sample_repo, "check", "sample/mod.py")
    assert data["passed"] is True and data["skipped"] == ["tests/test_mod.py::test_s"]


@pytest.mark.parametrize("command", ["check", "map"])
def test_stale_result_removed_when_next_run_is_tool_error(sample_repo, command):
    run_ok(sample_repo, command, "sample/mod.py")
    assert result_path(sample_repo, command, "sample/mod.py").is_file()
    for f in (sample_repo / "tests").glob("test_mod*.py"):
        f.unlink()
    result = run(sample_repo, command, "sample/mod.py")
    assert result.returncode == 2 and "no tests match" in result.stderr
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
    assert data["complete"] is True and data["survived"] and data["survived"][0]["diff"]
    assert "@@" in data["survived"][0]["diff"] and data["survived"][0]["id"]
    assert data["total"] == data["killed"] + len(data["survived"]) + len(data["timeout"]) + len(data["not_tested"])
    assert data["score"] == pytest.approx(
        data["killed"] / (data["killed"] + len(data["survived"]) + len(data["not_tested"]))
    )
    assert data["elapsed_s"] >= 0 and data["warnings"] == []


def test_mutate_strong_suite_kills_mutants(sample_repo_strong):
    data = run_ok(sample_repo_strong, "mutate", "sample/mod.py")
    assert data["killed"] > 0 and data["survived"] == []
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
    assert data["score"] != 1.0 and data["survived"] == []   # partial counts never look like a finished score
    assert list(tmp_root.iterdir()) == []                    # temp copy removed
    assert processes_running_in(tmp_root) == []              # whole process group killed


# --- Task 6: harness of the alone-vs-skill comparison ------------------------------------------

ROOT = CLI.parent.parent.parent
EVALS = ROOT / "scripts" / "testloop" / "evals"
PLAN = "dataforge/deploy/plan.py"
ORIGINAL_TESTS = 15


@functools.lru_cache(maxsize=None)
def _load_eval(name):
    """Import an eval script by path, once; it puts its own folder on sys.path, like cli.py does."""
    spec = importlib.util.spec_from_file_location(f"testloop_eval_{name}", EVALS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _base_is_current():
    """The seeded patches are pinned to one plan.py. Once it moves on, the harness refuses to run
    (test_score_refuses_when_the_base_moved covers that), so these tests have nothing to exercise."""
    if not (EVALS / "prepare.py").is_file():
        return True  # harness not written yet: let the tests fail for real
    wanted = _load_eval("prepare").PLAN_BASE_SHA
    return wanted == "sha256:" + hashlib.sha256((ROOT / PLAN).read_bytes()).hexdigest()


needs_base = pytest.mark.skipif(
    not _base_is_current(), reason="seeded patches are pinned to an older plan.py; re-pin to use the harness")


def prepare(variant, tmp_path):
    copy = tmp_path / variant
    result = subprocess.run(
        [sys.executable, str(EVALS / "prepare.py"), "--variant", variant, str(copy)],
        capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(copy)
    return copy


def score(copy, **options):
    """Score in-process, optionally restricted to some seeds and without the slow mutate step."""
    return _load_eval("score").score_copy(copy, **{"mutate": False, **options})


def seed_names():
    return sorted(p.stem for p in (EVALS / "seeded_bugs").glob("*.patch"))


def _rel_files(root):
    return sorted(
        p.relative_to(root).as_posix() for p in root.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    )


def _tree_shas(root):
    return {rel: hashlib.sha256((root / rel).read_bytes()).hexdigest() for rel in _rel_files(root)}


# Keys are matched against seeded patch names: each seed has one test that only that seed must break,
# which also proves the seed is observable at all. Never copied anywhere except a scored scratch copy.
WITNESS_TESTS = '''
from pathlib import Path
from dataforge.deploy.ignore import DeployIgnore
from dataforge.deploy.models import Action, PairRule, PipelineModel
from dataforge.deploy.pairing import resolve_pairs
from dataforge.deploy.plan import build_plan

TRIGGER = "name: t\\ncron: '0 9 * * *'\\nenabled: true\\njobs: [%s]\\n"


def put(root, rel, content="x\\n"):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def plan(tmp_path, source_glob, target, prune=False):
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    pipeline = PipelineModel(target="Prod", prune=prune, pairs=[PairRule(source=source_glob, target=target)])
    return build_plan(src, tgt, pipeline, "p", DeployIgnore.load(src), resolve_pairs(src, pipeline)).items


def by_target(items):
    return {i.target_path: i for i in items}


def test_witness_01(tmp_path):
    put(tmp_path / "Dev", "scripts/a.py")
    put(tmp_path / "Prod", "deploy/.ledger/entries.json")
    assert "deploy/.ledger/entries.json" not in by_target(plan(tmp_path, "scripts/**", "deploy/", prune=True))


def test_witness_02(tmp_path):
    put(tmp_path / "Dev", "scripts/a.py")
    assert plan(tmp_path, "scripts/**", "deploy/.ledger/") == []


def test_witness_03(tmp_path):
    put(tmp_path / "Dev", "scripts/a.py")
    real = put(tmp_path / "Prod", "elsewhere.py")
    (tmp_path / "Prod" / "scripts").mkdir()
    (tmp_path / "Prod" / "scripts" / "link.py").symlink_to(real)
    assert "scripts/link.py" not in by_target(plan(tmp_path, "scripts/**", "scripts/", prune=True))


def test_witness_04(tmp_path):
    put(tmp_path / "Dev", "scripts/a.py")
    put(tmp_path / "Dev", "deploy/.deployignore", "scripts/a.py\\n")
    assert by_target(plan(tmp_path, "scripts/**", "bin/"))["bin/a.py"].action == Action.skip_ignored


def test_witness_05(tmp_path):
    put(tmp_path / "Dev", "scripts/a.py")
    put(tmp_path / "Dev", "deploy/.deployignore", "vault_/\\n")
    put(tmp_path / "Prod", "scripts/vault_/data.bin")
    assert "scripts/vault_/data.bin" not in by_target(plan(tmp_path, "scripts/**", "scripts/", prune=True))


def test_witness_07(tmp_path):
    put(tmp_path / "Dev", "triggers/t.yaml", TRIGGER % "a")
    put(tmp_path / "Prod", "triggers/t.yaml", TRIGGER % "b")
    assert by_target(plan(tmp_path, "triggers/*.yaml", "triggers/"))["triggers/t.yaml"].action == Action.modify


def test_witness_08(tmp_path):
    put(tmp_path / "Dev", "triggers/t.yaml", TRIGGER % "a")
    put(tmp_path / "Prod", "triggers/t.yaml", "- not\\n- a mapping\\n")
    item = by_target(plan(tmp_path, "triggers/*.yaml", "triggers/"))["triggers/t.yaml"]
    assert item.action == Action.modify and item.before_sha


def test_witness_09(tmp_path):
    put(tmp_path / "Dev", "scripts/a.py")
    put(tmp_path / "Prod", "lib/other.py")
    assert "lib/other.py" not in by_target(plan(tmp_path, "scripts/**", "lib/sub/", prune=True))
'''


def add_tests(copy, source, name="test_deploy_plan_extra.py"):
    (copy / "tests" / name).write_text(source)


def prepend_to_conftest(copy, source):
    conftest = copy / "tests" / "conftest.py"
    conftest.write_text(source + conftest.read_text())


@pytest.fixture
def alone_copy(tmp_path):
    return prepare("alone", tmp_path)


@pytest.fixture(scope="module")
def prepared_copy_with_strong_tests(tmp_path_factory):
    copy = prepare("alone", tmp_path_factory.mktemp("strong"))
    add_tests(copy, WITNESS_TESTS)
    # an agent leaves bytecode behind; it must not mask a patched plan.py
    subprocess.run([sys.executable, "-m", "pytest", "-q", "tests/test_deploy_plan.py"], cwd=copy, capture_output=True)
    return copy


@pytest.fixture(scope="module")
def strong_score(prepared_copy_with_strong_tests):
    """The real CLI end to end, mutate included: run once, about two minutes."""
    copy = prepared_copy_with_strong_tests
    result = subprocess.run(
        [sys.executable, str(EVALS / "score.py"), str(copy)], capture_output=True, text=True, timeout=1800,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(copy / "score.json")
    return json.loads((copy / "score.json").read_text())


@needs_base
def test_prepare_alone_has_no_tools_or_skill(tmp_path):
    copy = prepare("alone", tmp_path)
    assert not (copy / "scripts/testloop").exists() and not (copy / ".claude").exists()
    assert not (copy / "testloop.toml").exists()


@needs_base
def test_prepare_skill_has_tools_but_no_seeded_bugs(tmp_path):
    copy = prepare("skill", tmp_path)
    assert (copy / ".claude/skills/test-loop/SKILL.md").exists()
    assert not (copy / "scripts/testloop/evals").exists()
    assert (copy / "scripts/testloop/cli.py").exists()


@needs_base
def test_prepare_never_copies_generated_properties(tmp_path):
    assert not (prepare("skill", tmp_path) / "tests/test_deploy_plan_properties.py").exists()


@needs_base
def test_prepare_variants_start_from_the_same_code_and_tests(tmp_path):
    alone, skill = prepare("alone", tmp_path), prepare("skill", tmp_path)
    for folder in ("dataforge", "tests"):
        assert _tree_shas(alone / folder) == _tree_shas(skill / folder)
    assert _rel_files(alone / "tests") == ["__init__.py", "conftest.py", "test_deploy_plan.py"]
    assert _tree_shas(alone / "dataforge") == {
        rel: sha for rel, sha in _tree_shas(ROOT / "dataforge").items()
    }


@needs_base
def test_prepare_copy_is_outside_the_repo_and_self_sufficient(tmp_path):
    copy = prepare("skill", tmp_path)
    toml = tomllib.loads((copy / "testloop.toml").read_text())
    assert toml["python"] == str(ROOT / ".venv/bin/python") and Path(toml["python"]).is_absolute()
    # the skill's own tools run from inside the copy and see the copy's 15 tests
    checked = subprocess.run([toml["python"], "scripts/testloop/cli.py", "check", PLAN], cwd=copy,
                             capture_output=True, text=True, timeout=300)
    assert checked.returncode == 0, checked.stderr
    assert read_json(copy, "check", PLAN)["collected"] == ORIGINAL_TESTS


@needs_base
def test_prepare_alone_runs_its_tests_against_its_own_code(tmp_path):
    copy = prepare("alone", tmp_path)
    python = str(ROOT / ".venv/bin/python")
    ran = subprocess.run([python, "-m", "pytest", "-q", "tests/test_deploy_plan.py"], cwd=copy,
                         capture_output=True, text=True, timeout=300)
    assert ran.returncode == 0 and f"{ORIGINAL_TESTS} passed" in ran.stdout, ran.stdout
    origin = subprocess.run([python, "-c", "import dataforge.deploy.plan as m; print(m.__file__)"],
                            cwd=copy, capture_output=True, text=True, timeout=60)
    assert Path(origin.stdout.strip()).resolve() == (copy / PLAN).resolve()


@needs_base
@pytest.mark.parametrize("inside", ["scripts/testloop/evals/_never", "tests/_never"])
def test_prepare_refuses_a_destination_inside_the_repo(inside):
    result = subprocess.run(
        [sys.executable, str(EVALS / "prepare.py"), "--variant", "alone", str(ROOT / inside)],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 2 and "outside" in result.stderr
    assert not (ROOT / inside).exists()


@needs_base
def test_prepare_refuses_a_used_destination(tmp_path):
    (tmp_path / "alone").mkdir()
    (tmp_path / "alone" / "old.txt").write_text("x")
    result = subprocess.run(
        [sys.executable, str(EVALS / "prepare.py"), "--variant", "alone", str(tmp_path / "alone")],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 2 and "not empty" in result.stderr


@needs_base
@pytest.mark.parametrize("variant", ["alone", "skill"])
def test_prepare_leaves_no_trace_of_the_seeded_bugs(tmp_path, variant):
    copy = prepare(variant, tmp_path)
    names = _rel_files(copy)
    assert not [n for n in names if n.endswith(".patch") or "seeded" in n or "evals" in n]
    for rel in names:
        text = (copy / rel).read_bytes().decode("utf-8", errors="replace").lower()
        assert "seeded" not in text and "seeded_bugs" not in text, rel
    sidecar = copy.with_name(copy.name + ".manifest.json")
    assert sidecar.is_file() and not (copy / sidecar.name).exists()


def test_prepare_prompt_is_neutral_and_the_same_for_both():
    text = (EVALS / "prompt.md").read_text()
    lowered = text.lower()
    for banned in ("seed", "plant", "mutat", "mutant", "propert", "hypothesis", "inject", "patch", "score",
                   "evals", "variant", "experiment", "compar", "/mnt/", ".venv", "{copy}"):
        assert banned not in lowered, banned
    assert not re.search(r"\b(repo|arm|arms)\b", lowered)
    for name in seed_names():
        assert name not in text
    assert "dataforge/deploy/plan.py" in text and "tests/test_deploy_plan*.py" in text
    assert "interpreter.txt" in text and "30 minutes" in text and "git" in lowered
    assert "- Work only inside your copy.\n" in text
    assert text.index("**alone:**") > text.index("30 minutes")   # the goal comes first, the arm lines after
    alone_line = next(line for line in text.splitlines() if line.startswith("- **alone:**"))
    skill_line = next(line for line in text.splitlines() if line.startswith("- **skill:**"))
    assert "skill" in alone_line.lower() and ".claude/skills/test-loop/SKILL.md" in skill_line


def _print_prompt(arm):
    result = subprocess.run([sys.executable, str(EVALS / "prepare.py"), "--print-prompt", arm],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_prepare_prompt_of_an_arm_differs_only_in_its_last_line_and_names_no_arm():
    alone, skill = _print_prompt("alone"), _print_prompt("skill")
    assert alone.splitlines()[:-1] == skill.splitlines()[:-1]
    assert alone.splitlines()[-1] == "- Do not use any skill."
    assert skill.splitlines()[-1].startswith("- Follow `.claude/skills/test-loop/SKILL.md`")
    for text in (alone, skill):
        assert "**" not in text and "alone" not in text.lower()


def test_score_seeded_patches_apply_without_fuzz_and_touch_only_plan(tmp_path):
    names = seed_names()
    assert len(names) >= 5
    target = tmp_path / PLAN
    target.parent.mkdir(parents=True)
    wanted = _load_eval("prepare").PLAN_BASE_SHA
    base = (ROOT / PLAN).read_bytes()
    if wanted != "sha256:" + hashlib.sha256(base).hexdigest():
        pytest.skip("seeded patches are pinned to an older plan.py")
    results = set()
    for name in names:
        patch_file = EVALS / "seeded_bugs" / f"{name}.patch"
        assert re.findall(r"^\+\+\+ (\S+)", patch_file.read_text(), re.M) == [f"b/{PLAN}"]
        target.write_bytes(base)
        applied = subprocess.run(["patch", "-p1", "-F0", "--no-backup-if-mismatch", "-i", str(patch_file)],
                                 cwd=tmp_path, capture_output=True, text=True)
        assert applied.returncode == 0 and "fuzz" not in applied.stdout and "offset" not in applied.stdout, applied.stdout
        results.add(target.read_bytes())
        ast.parse(target.read_bytes())
    assert base not in results and len(results) == len(names)   # each one a real and distinct change


@needs_base
@pytest.mark.integration
def test_score_counts_caught_seeded_bugs(strong_score):
    data = strong_score
    assert data["seeded_caught"] <= data["seeded_total"] and data["seeded_total"] >= 5
    assert data["seeded_caught"] == data["seeded_total"] == len(seed_names())
    assert data["seeded_tool_errors"] == 0 and data["seeded_caught_via_xfail_flip"] == 0
    assert data["seeded_caught_direct"] == data["seeded_caught"] and data["seeded_unreproduced"] == 0
    for name, seed in data["seeded"].items():
        witness = f"tests/test_deploy_plan_extra.py::test_witness_{name[:2]}"
        assert seed["status"] == "caught" and witness in seed["new_failures"], name


@needs_base
@pytest.mark.integration
def test_score_records_mutation_and_tests_added(strong_score):
    data = strong_score
    assert data["variant"] == "alone"
    assert data["tests_added"] == WITNESS_TESTS.count("def test_witness_")
    assert data["collected"] == ORIGINAL_TESTS + data["tests_added"]
    assert data["mutation_complete"] is True and data["mutation_error"] is None
    assert data["mutation_total"] == data["killed"] + data["survived"] + data["mutation_timeouts"]
    assert data["mutation_total"] > 0
    assert data["killed"] > 0 and data["mutation_score"] == pytest.approx(data["killed"] / data["mutation_total"])
    assert data["import_proof"]["mutate_sessions"] > 0


@needs_base
@pytest.mark.integration
def test_score_original_tests_catch_no_seeded_bug(alone_copy):
    data = score(alone_copy)
    assert data["seeded_total"] == len(seed_names()) and data["seeded_caught"] == 0
    assert data["seeded_tool_errors"] == 0
    assert {s["status"] for s in data["seeded"].values()} == {"missed"}
    assert data["tests_added"] == 0 and data["collected"] == ORIGINAL_TESTS
    assert data["baseline_failed"] == []


@needs_base
def test_score_refuses_a_copy_that_imports_the_repo_code(alone_copy):
    prepend_to_conftest(alone_copy, f"import sys\nsys.path.insert(0, {str(ROOT)!r})\n")
    with pytest.raises(_load_eval("score").ImportProofError, match="repo|expected"):
        score(alone_copy, seeds=seed_names()[:1])
    assert not (alone_copy / "score.json").exists()


@needs_base
def test_score_cli_refusal_exits_2_and_leaves_no_score(alone_copy):
    prepend_to_conftest(alone_copy, f"import sys\nsys.path.insert(0, {str(ROOT)!r})\n")
    (alone_copy / "score.json").write_text("{}")   # a stale result must not survive
    result = subprocess.run([sys.executable, str(EVALS / "score.py"), str(alone_copy)],
                            capture_output=True, text=True, timeout=300)
    assert result.returncode == 2 and "imported" in result.stderr
    assert not (alone_copy / "score.json").exists()


def test_score_refuses_a_session_with_no_import_recorded(tmp_path):
    module = _load_eval("score")
    with pytest.raises(module.ImportProofError):
        module.verify_check_proof([], tmp_path)
    with pytest.raises(module.ImportProofError):
        module.verify_check_proof([{"dataforge": None, "dataforge.deploy.plan": None, "plan_sha": None}], tmp_path)


def test_score_refuses_a_mutate_run_that_touched_the_repo(tmp_path):
    module = _load_eval("score")
    inside = "/tmp/testloop-mutate-abc/mutants/dataforge/deploy/plan.py"
    good = {"dataforge": "/tmp/testloop-mutate-abc/mutants/dataforge/__init__.py", "dataforge.deploy.plan": inside}
    bad = {"dataforge": str(ROOT / "dataforge/__init__.py"), "dataforge.deploy.plan": str(ROOT / PLAN)}
    assert module.verify_mutate_proof([good, good]) == 2
    with pytest.raises(module.ImportProofError):
        module.verify_mutate_proof([good, bad])
    with pytest.raises(module.ImportProofError):
        module.verify_mutate_proof([])


@needs_base
@pytest.mark.parametrize("rel", [PLAN, "dataforge/deploy/ignore.py", "dataforge/added_by_agent.py"])
def test_score_refuses_changed_production_code(alone_copy, rel):
    path = alone_copy / rel
    path.write_text((path.read_text() if path.exists() else "") + "\n# edited\n")
    with pytest.raises(_load_eval("score").ScoreError, match="production|base"):
        score(alone_copy)


@needs_base
def test_score_refuses_when_the_base_moved(alone_copy, monkeypatch):
    module = _load_eval("score")
    monkeypatch.setattr(module, "PLAN_BASE_SHA", "sha256:" + "0" * 64)
    with pytest.raises(module.ScoreError, match="base"):
        module.score_copy(alone_copy, mutate=False)


@needs_base
def test_score_refuses_without_the_manifest(alone_copy):
    alone_copy.with_name(alone_copy.name + ".manifest.json").unlink()
    with pytest.raises(_load_eval("score").ScoreError, match="manifest"):
        score(alone_copy)


CRASH_WHEN_PATCHED = f"""
import hashlib, pathlib, pytest
if hashlib.sha256(pathlib.Path({PLAN!r}).read_bytes()).hexdigest() != {{sha!r}}:
    pytest.exit("boom", returncode=3)
"""


@needs_base
@pytest.mark.parametrize("crash_in", ["conftest", "test_module"])   # before the session, and during collection
def test_score_tool_error_on_a_patched_run_is_reported_never_caught(alone_copy, crash_in):
    sha = hashlib.sha256((ROOT / PLAN).read_bytes()).hexdigest()
    crash = CRASH_WHEN_PATCHED.format(sha=sha)
    if crash_in == "conftest":
        prepend_to_conftest(alone_copy, crash)
    else:
        add_tests(alone_copy, crash + "\n\ndef test_fine():\n    pass\n")
    names = seed_names()[:2]
    data = score(alone_copy, seeds=names)
    assert data["seeded_caught"] == 0 and data["seeded_tool_errors"] == 2
    for name in names:
        assert data["seeded"][name]["status"] == "tool_error" and data["seeded"][name]["exit"] == 2
        assert data["seeded"][name]["detail"]


@needs_base
def test_score_failures_already_red_are_not_catches(alone_copy):
    add_tests(alone_copy, "def test_always_red():\n    assert False\n")
    data = score(alone_copy, seeds=seed_names()[:2])
    assert data["baseline_failed"] == ["tests/test_deploy_plan_extra.py::test_always_red"]
    assert data["seeded_caught"] == 0 and data["seeded_tool_errors"] == 0
    assert data["tests_added"] == 1


@needs_base
def test_score_red_baseline_is_named_when_mutate_refuses(alone_copy):
    add_tests(alone_copy, "def test_always_red():\n    assert False\n")
    data = score(alone_copy, seeds=[], mutate=True)
    assert data["mutation_score"] is None and data["mutation_complete"] is False
    assert "baseline" in data["mutation_error"]


@needs_base
def test_score_changes_nothing_in_the_copy_but_its_own_file(alone_copy):
    before = _tree_shas(alone_copy)
    score(alone_copy, seeds=seed_names()[:1])
    after = _tree_shas(alone_copy)
    assert after.pop("score.json") and after == before


@needs_base
def test_score_red_baseline_still_lets_a_new_failure_count(alone_copy):
    add_tests(alone_copy, "def test_always_red():\n    assert False\n" + WITNESS_TESTS)
    name = seed_names()[0]
    data = score(alone_copy, seeds=[name])
    assert data["seeded_caught"] == 1
    assert data["seeded"][name]["new_failures"] == [f"tests/test_deploy_plan_extra.py::test_witness_{name[:2]}"]


STRICT_XFAIL_FLIP = f"""
import hashlib, pathlib, pytest

@pytest.mark.xfail(strict=True, reason="flips when plan.py changes")
def test_flips():
    assert hashlib.sha256(pathlib.Path({PLAN!r}).read_bytes()).hexdigest() != {{sha!r}}
"""


@needs_base
def test_score_strict_xfail_flip_counts_and_is_noted(alone_copy):
    sha = hashlib.sha256((ROOT / PLAN).read_bytes()).hexdigest()
    add_tests(alone_copy, STRICT_XFAIL_FLIP.format(sha=sha))
    names = seed_names()[:2]
    data = score(alone_copy, seeds=names)
    assert data["baseline_xfailed"] == ["tests/test_deploy_plan_extra.py::test_flips"]
    assert data["seeded_caught"] == 2 and data["seeded_caught_via_xfail_flip"] == 2
    assert data["seeded_caught_direct"] == 0
    assert all(data["seeded"][n]["via_xfail_flip"] for n in names)


@needs_base
def test_score_does_not_trust_settings_left_in_the_copy(alone_copy):
    (alone_copy / "testloop.toml").write_text('python = "/nonexistent"\n')
    (alone_copy / "pytest.ini").write_text("[pytest]\naddopts = --definitely-not-an-option\n")
    data = score(alone_copy, seeds=seed_names()[:1])
    assert data["seeded_tool_errors"] == 0 and data["collected"] == ORIGINAL_TESTS


@needs_base
def test_score_flags_test_files_it_cannot_count(alone_copy):
    (alone_copy / "tests" / "test_elsewhere.py").write_text("def test_x():\n    pass\n")
    (alone_copy / "tests" / "test_deploy_plan.py").write_text(
        (alone_copy / "tests" / "test_deploy_plan.py").read_text() + "\n\ndef test_more():\n    pass\n")
    data = score(alone_copy, seeds=seed_names()[:1])
    assert data["tests_added"] == 1
    assert data["uncounted_test_files"] == ["tests/test_elsewhere.py"]
    assert data["original_files_modified"] == ["tests/test_deploy_plan.py"]


# --- Task 6 fix round: flake gates, scratch home, headless launch ------------------------------

def counting_test(counter, body):
    """A test that knows how many times it has run: the clones are throwaway, the counter is not."""
    return f"""
import pathlib

COUNTER = pathlib.Path({str(counter)!r})


def test_counted():
    run = int(COUNTER.read_text()) + 1 if COUNTER.exists() else 1
    COUNTER.write_text(str(run))
    {body}
"""


@needs_base
@pytest.mark.parametrize("body", ["assert run == 1", "assert run != 1"], ids=["first_run_only", "red_only_first"])
def test_score_unstable_test_in_the_baseline_scores_no_catch(alone_copy, tmp_path, body):
    add_tests(alone_copy, counting_test(tmp_path / "counter", body))
    data = score(alone_copy, seeds=seed_names()[:3])
    node = "tests/test_deploy_plan_extra.py::test_counted"
    assert data["seeded_caught"] == 0 and data["seeded_caught_direct"] == 0
    assert data["baseline_failed"] == [node] and data["baseline_unstable"] == [node]


@needs_base
def test_score_a_new_failure_must_reproduce(alone_copy, tmp_path):
    # runs 1 and 2 are the baseline, run 3 is the first patched run, run 4 the second
    add_tests(alone_copy, counting_test(tmp_path / "counter", "assert run != 3"))
    name = seed_names()[0]
    data = score(alone_copy, seeds=[name])
    seed = data["seeded"][name]
    assert data["seeded_caught"] == 0 and data["seeded_unreproduced"] == 1 and data["baseline_failed"] == []
    assert seed["status"] == "missed" and seed["new_failures"] == []
    assert seed["unreproduced_failures"] == ["tests/test_deploy_plan_extra.py::test_counted"]
    assert "reproduce" in seed["detail"]


def _scripted_tool(module, monkeypatch, answers):
    """Replace the CLI with scripted answers, so odd exits can be tested without provoking them."""
    answers = iter(answers)
    monkeypatch.setattr(module, "_run_tool", lambda *a, **k: next(answers))
    monkeypatch.setattr(module, "verify_check_proof", lambda *a, **k: None)


GREEN = {"failed": [], "xfailed": [], "skipped": [], "collected": 15}


@needs_base
def test_score_check_exit_1_without_a_result_is_a_tool_error(alone_copy, monkeypatch):
    module = _load_eval("score")
    name = seed_names()[0]
    _scripted_tool(module, monkeypatch, [(0, "", GREEN, []), (0, "", GREEN, []), (1, "died", None, [])])
    seed = module.score_copy(alone_copy, mutate=False, seeds=[name])["seeded"][name]
    assert seed["status"] == "tool_error" and seed["exit"] == 1 and "died" in seed["detail"]


@needs_base
def test_score_second_patched_run_crashing_is_a_tool_error_not_a_catch(alone_copy, monkeypatch):
    module = _load_eval("score")
    name = seed_names()[0]
    red = {**GREEN, "failed": ["t"]}
    _scripted_tool(module, monkeypatch, [(0, "", GREEN, []), (0, "", GREEN, []), (1, "", red, []), (2, "boom", None, [])])
    data = module.score_copy(alone_copy, mutate=False, seeds=[name])
    assert data["seeded_caught"] == 0 and data["seeded_tool_errors"] == 1 and data["seeded"][name]["exit"] == 2


@needs_base
def test_score_timeouts_are_counted(alone_copy, monkeypatch):
    module = _load_eval("score")
    name = seed_names()[0]
    _scripted_tool(module, monkeypatch, [(0, "", GREEN, []), (0, "", GREEN, []), (None, "killed", None, [])])
    data = module.score_copy(alone_copy, mutate=False, seeds=[name])
    assert data["seeded_timeouts"] == 1 and data["seeded_tool_errors"] == 1 and data["seeded_caught"] == 0


@needs_base
def test_score_mutants_that_timed_out_are_reported(alone_copy, monkeypatch):
    module = _load_eval("score")
    result = {"score": 0.5, "total": 3, "killed": 1, "survived": [{"id": "x", "diff": ""}], "timeout": ["y"],
              "complete": True, "warnings": []}
    monkeypatch.setattr(module, "_run_tool", lambda *a, **k: (0, "", result, []))
    monkeypatch.setattr(module, "verify_mutate_proof", lambda records: 1)
    mutation, _ = module._score_mutation(alone_copy, alone_copy.parent / "scratch", alone_copy)
    assert mutation["mutation_timeouts"] == 1 and mutation["survived"] == 1 and mutation["killed"] == 1


@needs_base
def test_score_gives_tests_a_scratch_dataforge_home(alone_copy, tmp_path):
    seen = tmp_path / "home.txt"
    add_tests(alone_copy, f"""
import os, pathlib


def test_home():
    pathlib.Path({str(seen)!r}).write_text(os.environ["DATAFORGE_HOME"])
""")
    score(alone_copy, seeds=[])
    home = Path(seen.read_text())
    assert "testloop-score-" in home.as_posix() and not home.is_relative_to(ROOT)


@needs_base
def test_score_one_stall_in_a_generated_test_is_not_a_failure(alone_copy):
    add_tests(alone_copy, """
import time
from hypothesis import given, settings, strategies as st


@settings(max_examples=2)
@given(st.integers())
def test_stalls(value):
    time.sleep(0.3)
""")
    assert score(alone_copy, seeds=[])["baseline_failed"] == []


@needs_base
def test_prepare_deterministic_profile_has_no_deadline(alone_copy):
    code = ("import tests.conftest; from hypothesis import settings; "
            "settings.load_profile('deterministic'); print(settings().deadline)")
    result = subprocess.run([str(ROOT / ".venv/bin/python"), "-c", code], cwd=alone_copy,
                            capture_output=True, text=True, timeout=120)
    assert result.stdout.strip() == "None", result.stderr


@needs_base
@pytest.mark.parametrize("arm", ["alone", "skill"])
def test_prepare_interpreter_file_is_the_only_way_to_the_interpreter(tmp_path, arm):
    copy = prepare(arm, tmp_path)
    assert (copy / "interpreter.txt").read_text().strip() == str(ROOT / ".venv/bin/python")


def prepare_run(arm, number, root):
    result = subprocess.run(
        [sys.executable, str(EVALS / "prepare.py"), "--variant", arm, "--run", str(number), "--root", str(root)],
        capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, result.stderr
    return Path(result.stdout.strip())


@needs_base
def test_prepare_run_uses_a_neutral_folder_and_a_mapping_outside_the_copy(tmp_path):
    root = tmp_path / "runs"
    copies = {n: prepare_run(arm, n, root) for n, arm in ((1, "alone"), (2, "skill"))}
    assert copies[1].parent != copies[2].parent
    for copy in copies.values():
        assert copy.parent.parent == root and copy.name == "copy"
        assert not re.search("alone|skill", str(copy.relative_to(root)))
        assert "alone" not in copy.with_name("copy.manifest.json").read_text()
        assert "skill" not in copy.with_name("copy.manifest.json").read_text()
    runs = json.loads((root / "runs.json").read_text())
    assert runs == {"1": {"arm": "alone", "copy": str(copies[1])}, "2": {"arm": "skill", "copy": str(copies[2])}}


@needs_base
def test_prepare_run_refuses_a_number_in_use_and_an_arm_in_the_path(tmp_path):
    root = tmp_path / "runs"
    prepare_run("alone", 1, root)
    base = [sys.executable, str(EVALS / "prepare.py"), "--variant", "alone"]
    again = subprocess.run([*base, "--run", "1", "--root", str(root)], capture_output=True, text=True, timeout=60)
    assert again.returncode == 2 and "already" in again.stderr
    named = subprocess.run([*base, "--run", "2", "--root", str(tmp_path / "alone-runs")],
                           capture_output=True, text=True, timeout=60)
    assert named.returncode == 2 and "arm" in named.stderr


@needs_base
def test_prepare_verify_run_checks_the_mapping(tmp_path):
    copy = prepare_run("alone", 1, tmp_path / "runs")
    verify = [sys.executable, str(EVALS / "prepare.py"), "--verify-run"]
    assert subprocess.run([*verify, "1", "alone", str(copy)], capture_output=True, timeout=60).returncode == 0
    assert subprocess.run([*verify, "1", "skill", str(copy)], capture_output=True, timeout=60).returncode == 2
    assert subprocess.run([*verify, "2", "alone", str(copy)], capture_output=True, timeout=60).returncode == 2


@needs_base
def test_score_reads_the_arm_of_a_run_from_the_mapping(tmp_path):
    copy = prepare_run("skill", 3, tmp_path / "runs")
    data = score(copy, seeds=[])
    assert data["variant"] == "skill" and data["run"] == 3


RUN_ARM = EVALS / "run_arm.sh"


def test_score_run_arm_is_valid_shell_and_never_bypasses_permissions():
    # Static on purpose: this script starts a real session, and only the controller runs it.
    assert subprocess.run(["bash", "-n", str(RUN_ARM)], capture_output=True).returncode == 0
    text = RUN_ARM.read_text()
    for forbidden in ("bypassPermissions", "dangerously-skip-permissions", "--dangerously"):
        assert forbidden not in text
    launch = next(line for line in text.splitlines() if "claude -p" in line)
    for needed in ('env -i HOME="$HOME" PATH="$PATH" DATAFORGE_HOME="$SCRATCH" timeout 1800 claude -p "$PROMPT"',
                   "--model claude-sonnet-5-5", "--setting-sources project", "--strict-mcp-config",
                   "--disable-slash-commands", "--permission-mode auto", "--output-format stream-json", "--verbose",
                   '> "$LOGDIR/run-$N.jsonl"'):
        assert needed in launch, needed
    assert text.count("claude -p") == 1 and 'cd "$COPY"' in text


def test_score_run_arm_changes_only_the_prompt_between_arms():
    uses = [line.strip() for line in RUN_ARM.read_text().splitlines() if "$ARM" in line]
    assert uses and all("prepare.py" in line for line in uses), uses   # the arm reaches only the prompt and the check


def _write_log(logdir, number, lines, meta=None):
    logdir.mkdir(exist_ok=True)
    (logdir / f"run-{number}.jsonl").write_text("".join(json.dumps(line) + "\n" for line in lines))
    if meta:
        (logdir / f"run-{number}.meta.json").write_text(json.dumps(meta))


def _check_logs(logdir):
    result = subprocess.run([sys.executable, str(EVALS / "check_logs.py"), str(logdir)],
                            capture_output=True, text=True, timeout=60)
    return result.returncode, json.loads(result.stdout) if result.stdout else None


RESULT_EVENT = {"type": "result", "duration_ms": 90000, "num_turns": 4,
                "usage": {"input_tokens": 10, "output_tokens": 20,
                          "cache_creation_input_tokens": 30, "cache_read_input_tokens": 40}}


def test_score_check_logs_extracts_time_and_tokens_and_passes_a_clean_run(tmp_path):
    python = str(ROOT / ".venv/bin/python")
    _write_log(tmp_path, 1, [{"type": "assistant", "text": f"{python} -m pytest"}, RESULT_EVENT],
               {"run": 1, "exit": 0, "seconds": 95})
    code, data = _check_logs(tmp_path)
    assert code == 0
    run = data["runs"]["run-1"]
    assert run["seconds"] == 95 and run["capped"] is False and run["hits"] == {}
    assert run["tokens"] == {"input": 10, "output": 20, "cache_creation": 30, "cache_read": 40}


def test_score_check_logs_counts_tokens_of_a_run_cut_off_by_the_limit(tmp_path):
    usage = {"input_tokens": 5, "output_tokens": 7}
    message = {"type": "assistant", "message": {"id": "m1", "usage": usage}}
    # the same message appears once per content block, and must count once
    _write_log(tmp_path, 2, [message, message, {"type": "assistant", "message": {"id": "m2", "usage": usage}}],
               {"run": 2, "exit": 124, "seconds": 1800})
    code, data = _check_logs(tmp_path)
    run = data["runs"]["run-2"]
    assert code == 0 and run["capped"] is True and run["seconds"] == 1800
    assert run["tokens"]["input"] == 10 and run["tokens"]["output"] == 14


@pytest.mark.parametrize("text, key", [
    (str(ROOT / "docs/INDEX.md"), "repo_path"),
    ("scripts/testloop/evals/seeded_bugs/x.patch", "seeded_bugs"),
    ("read CLAUDE.md first", "CLAUDE.md"),
    ("tests/test_deploy_plan_properties.py", "test_deploy_plan_properties"),
    ("see df-886.8", "df-886"),
    ("cat ../../runs.json", "runs.json"),
    ("cat ../copy.manifest.json", ".manifest.json"),
])
def test_score_check_logs_flags_each_leak(tmp_path, text, key):
    _write_log(tmp_path, 1, [{"type": "user", "text": text}, RESULT_EVENT])
    code, data = _check_logs(tmp_path)
    assert code == 1 and key in data["runs"]["run-1"]["hits"] and data["flagged"] == ["run-1"]
    assert text in data["runs"]["run-1"]["samples"][key][0]


def test_score_check_logs_reports_expected_noise_without_flagging(tmp_path):
    venv_frame = str(ROOT / ".venv/lib/python3.12/site-packages/_pytest/main.py")
    _write_log(tmp_path, 1, [{"type": "user", "text": f"{venv_frame} .testloop/check.json"}, RESULT_EVENT])
    code, data = _check_logs(tmp_path)
    assert code == 0 and data["flagged"] == []
    assert data["runs"]["run-1"]["notes"] == {"repo_venv_path": 1, ".testloop": 1}


def test_score_check_logs_refuses_an_empty_folder(tmp_path):
    assert _check_logs(tmp_path)[0] == 2


def test_score_predeclared_decisions_are_written_down():
    text = (EVALS / "PREDECLARED.md").read_text()
    for needed in ("seeded_caught_direct", "0.6996", "30 minutes", "{01, 02}", "{03, 04, 05, 07, 08, 09}",
                   "RESULTS.md", "mutmut", "hypothesis"):
        assert needed in text, needed
