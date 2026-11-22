"""scripts/testloop/affected.py: which mutation targets a PR's changed files can affect (df-gqke), and
the CI jobs it packs their shards into by measured cost; scripts/testloop/costs.py, which measures it
(df-n8ac.39)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "testloop"))

import affected  # the test loop tools are scripts, not an installed package
import costs
import mutate_cmd
from common import load_config, load_costs, write_result
from hypothesis import given, settings
from hypothesis import strategies as st

FILES = {
    "testloop.toml": (
        '[targets."dataforge/deploy/plan.py"]\ntests = ["tests/test_plan*.py"]\n\n'
        '[targets."dataforge/deploy/apply.py"]\ntests = ["tests/test_apply.py"]\n'
    ),
    "dataforge/__init__.py": "",
    "dataforge/models.py": "",
    "dataforge/gui.py": "import dataforge.models\n",
    "dataforge/deploy/__init__.py": "",
    "dataforge/deploy/plan.py": "from .ignore import load\n",
    "dataforge/deploy/ignore.py": "def load():\n    from ..models import X\n",
    "dataforge/deploy/apply.py": "from dataforge.deploy import plan\n",
    "dataforge/deploy/rollback.py": "",
    "dataforge/deploy/data.json": "{}",
    "tests/__init__.py": "",
    "tests/conftest.py": "",
    "tests/_helpers.py": "from dataforge.deploy import rollback\n",
    "tests/test_plan.py": "from dataforge.deploy.plan import load\n",
    "tests/test_apply.py": "import dataforge.deploy.apply\nfrom tests import _helpers\n",
    "docs/x.md": "",
}
PLAN, APPLY = "dataforge/deploy/plan.py", "dataforge/deploy/apply.py"


@pytest.fixture
def repo(tmp_path):
    for rel, text in FILES.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    return tmp_path


def picked(repo, changed):
    return affected.select(load_config(repo), changed)[0]


def test_dependencies_follow_relative_absolute_nested_and_package_imports(repo):
    deps = affected.dependencies(repo, [PLAN, "tests/test_plan.py"])
    assert deps == {
        PLAN, "tests/test_plan.py", "tests/__init__.py",
        "dataforge/__init__.py", "dataforge/deploy/__init__.py",
        "dataforge/deploy/ignore.py",   # from .ignore
        "dataforge/models.py",          # from ..models, inside a function
    }


@pytest.mark.parametrize("changed, expected", [
    ([PLAN], [PLAN, APPLY]),                          # apply imports plan
    (["dataforge/models.py"], [PLAN, APPLY]),         # two hops away, through ignore.py
    ([APPLY], [APPLY]),
    (["tests/test_plan.py"], [PLAN]),
    (["dataforge/deploy/rollback.py"], [APPLY]),      # reached only through a test helper
    (["dataforge/gui.py", "docs/x.md"], []),          # nothing a target imports
    ([], []),
])
def test_a_target_runs_when_its_module_tests_or_imports_change(repo, changed, expected):
    assert picked(repo, changed) == expected


@pytest.mark.parametrize("changed", [
    "testloop.toml",   # no base version given: what changed in it is unknown
    "pyproject.toml", "tests/conftest.py",
    ".github/workflows/ci.yml",   # no base version given either
    "scripts/testloop/mutate_cmd.py", "scripts/testloop/common.py", "scripts/testloop/cli.py",
    "scripts/testloop/plugins/testloop_ascii_locale.py",
    "dataforge/deploy/data.json",   # not Python: the import graph cannot see who reads it
    "dataforge/deleted.py",         # gone from HEAD: who imported it is unknown
])
def test_shared_or_invisible_changes_run_every_target(repo, changed):
    assert picked(repo, [changed]) == [PLAN, APPLY]


BASE_TOML = FILES["testloop.toml"]


@pytest.mark.parametrize("head_toml, expected", [
    # one target's table changed (its min_score): only that target (df-n8ac.34)
    (BASE_TOML.replace('tests = ["tests/test_apply.py"]', 'tests = ["tests/test_apply.py"]\nmin_score = 0.9'),
     [APPLY]),
    # a key outside [targets] changes how every target runs: all of them
    ('pytest_args = ["-x"]\n' + BASE_TOML, [PLAN, APPLY]),
    # only comments or layout: nothing
    ("# a comment\n" + BASE_TOML, []),
])
def test_a_testloop_toml_change_runs_only_the_targets_it_touches(repo, head_toml, expected):
    (repo / "testloop.toml").write_text(head_toml, encoding="utf-8")
    assert affected.select(load_config(repo), ["testloop.toml"], base_toml=BASE_TOML)[0] == expected


def test_a_new_target_in_testloop_toml_runs(repo):
    base = '[targets."dataforge/deploy/plan.py"]\ntests = ["tests/test_plan*.py"]\n'
    assert affected.select(load_config(repo), ["testloop.toml"], base_toml=base)[0] == [APPLY]


@pytest.mark.parametrize("base_toml", [None, "not = [valid toml"])
def test_testloop_toml_without_a_readable_base_runs_every_target(repo, base_toml):
    assert affected.select(load_config(repo), ["testloop.toml"], base_toml=base_toml)[0] == [PLAN, APPLY]


def test_testloop_toml_and_a_module_change_add_up(repo):
    head = BASE_TOML.replace('tests = ["tests/test_apply.py"]', 'tests = ["tests/test_apply.py"]\nmin_score = 0.9')
    (repo / "testloop.toml").write_text(head, encoding="utf-8")
    changed = ["testloop.toml", "tests/test_plan.py"]
    assert affected.select(load_config(repo), changed, base_toml=BASE_TOML)[0] == [PLAN, APPLY]


def test_cli_without_base_ref_prints_every_target(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    assert affected.main([]) == 0
    assert json.loads(capsys.readouterr().out) == [PLAN, APPLY]


def test_cli_with_base_ref_selects_from_the_git_diff(repo, monkeypatch, capsys):
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run([*git, "add", "-A"], cwd=repo, check=True)
    subprocess.run([*git, "commit", "-qm", "base"], cwd=repo, check=True)
    subprocess.run(["git", "switch", "-qc", "pr"], cwd=repo, check=True)
    (repo / "tests/test_plan.py").write_text("from dataforge.deploy.plan import load  # x\n", encoding="utf-8")
    subprocess.run([*git, "commit", "-qam", "pr"], cwd=repo, check=True)
    monkeypatch.chdir(repo)
    assert affected.main(["--base-ref", "main"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == [PLAN]
    assert f"{APPLY}: skipped" in captured.err


def test_cli_reads_testloop_toml_of_the_merge_base(repo, monkeypatch, capsys):
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run([*git, "add", "-A"], cwd=repo, check=True)
    subprocess.run([*git, "commit", "-qm", "base"], cwd=repo, check=True)
    subprocess.run(["git", "switch", "-qc", "pr"], cwd=repo, check=True)
    head = BASE_TOML.replace('tests = ["tests/test_plan*.py"]', 'tests = ["tests/test_plan*.py"]\nmin_score = 0.5')
    (repo / "testloop.toml").write_text(head, encoding="utf-8")
    subprocess.run([*git, "commit", "-qam", "pr"], cwd=repo, check=True)
    monkeypatch.chdir(repo)
    assert affected.main(["--base-ref", "main"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == [PLAN]
    assert f"{PLAN}: its table in testloop.toml changed" in captured.err


def test_cli_bad_base_ref_is_a_tool_error(repo, monkeypatch, capsys):
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    monkeypatch.chdir(repo)
    assert affected.main(["--base-ref", "nope"]) == 2
    assert "git diff against nope failed" in capsys.readouterr().err


@pytest.mark.parametrize("ref", ["--output=/tmp/x", "-p", "main;rm", "a b"])
def test_cli_rejects_a_base_ref_that_is_not_a_ref_name(repo, monkeypatch, capsys, ref):
    """A ref starting with '-' would reach git as an option (Sonar pythonsecurity:S8705)."""
    monkeypatch.chdir(repo)
    assert affected.main([f"--base-ref={ref}"]) == 2
    assert "not a git ref name" in capsys.readouterr().err


def test_cli_matrix_packs_one_item_per_shard_of_each_target_into_jobs(repo, monkeypatch, capsys):
    """df-n8ac.32: --matrix feeds CI's mutation matrix, one item per shard; df-n8ac.39: packed into
    jobs. Without costs every item is estimated at UNMEASURED_SECONDS (60 s: three per 200 s job)."""
    toml = repo / "testloop.toml"
    toml.write_text(toml.read_text(encoding="utf-8").replace(
        'tests = ["tests/test_plan*.py"]\n', 'tests = ["tests/test_plan*.py"]\nshards = 3\n'), encoding="utf-8")
    monkeypatch.chdir(repo)
    assert affected.main(["--matrix"]) == 0
    assert json.loads(capsys.readouterr().out) == [
        {"job": 1, "items": [{"module": APPLY, "shard": 1, "shards": 1}, {"module": PLAN, "shard": 1, "shards": 3},
                             {"module": PLAN, "shard": 2, "shards": 3}]},
        {"job": 2, "items": [{"module": PLAN, "shard": 3, "shards": 3}]},
    ]


def test_cli_matrix_rejects_an_invalid_shards_value(repo, monkeypatch, capsys):
    toml = repo / "testloop.toml"
    toml.write_text(toml.read_text(encoding="utf-8") + "shards = 0\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    assert affected.main(["--matrix"]) == 2
    assert "shards" in capsys.readouterr().err


CI_BASE = """name: ci
on:
  pull_request:
concurrency:
  group: x
jobs:
  tests:
    runs-on: ubuntu-latest
    steps:
      - run: pytest
  mutation-targets:
    runs-on: ubuntu-latest
    steps:
      - run: affected.py
  mutation:
    needs: [mutation-targets]
    steps:
      - run: cli.py mutate "$MODULE"
  mutation-ok:
    needs: [mutation]
    steps:
      - run: cli.py gate "$MODULE"
  report:
    steps:
      - run: ci_report.py
"""
CI = ".github/workflows/ci.yml"


@pytest.mark.parametrize("head, expected", [
    # a job that does not mutate: no target (df-n8ac.40)
    (CI_BASE.replace("- run: pytest", "- run: pytest -x"), []),
    (CI_BASE.replace("- run: ci_report.py", "- run: ci_report.py --all"), []),
    (CI_BASE.replace("  group: x", "  group: y"), []),
    (CI_BASE.replace("jobs:\n", "jobs:\n  lint:\n    steps:\n      - run: ruff\n"), []),
    # what the mutation jobs run, or a top-level key every job sees: every target
    (CI_BASE.replace('cli.py mutate "$MODULE"', 'cli.py mutate "$MODULE" --fast'), [PLAN, APPLY]),
    (CI_BASE.replace("- run: affected.py", "- run: affected.py --all"), [PLAN, APPLY]),
    (CI_BASE.replace('cli.py gate "$MODULE"', 'cli.py gate "$MODULE" || true'), [PLAN, APPLY]),
    (CI_BASE.replace("jobs:\n", "env:\n  X: 1\njobs:\n"), [PLAN, APPLY]),
])
def test_a_ci_yml_change_runs_every_target_only_when_it_touches_the_mutation(repo, head, expected):
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / CI).write_text(head, encoding="utf-8")
    assert affected.select(load_config(repo), [CI], base_ci=CI_BASE)[0] == expected


@pytest.mark.parametrize("tool", [
    "scripts/testloop/affected.py", "scripts/testloop/gate_cmd.py", "scripts/testloop/diffcover_cmd.py",
    "scripts/testloop/map_cmd.py", "scripts/testloop/check_cmd.py", "scripts/testloop/costs.py",
])
def test_a_test_loop_tool_that_mutate_does_not_run_picks_no_target(repo, tool):
    """Their own tests run in the testloop-tools job; a score cannot move with them (df-n8ac.40)."""
    assert picked(repo, [tool]) == []


# ---------------------------------------------------------------- script targets (df-n8ac.36)
# A script split into sibling modules: they import each other as `scripts.x` or, from their own folder
# on sys.path, as bare names; a test may import a script module by its bare name too.
SCRIPT_FILES = {
    "testloop.toml": "".join(
        f'[targets."scripts/{name}.py"]\ntests = ["tests/test_{name.replace("/", "_")}.py"]\n\n'
        for name in ("guard", "guard_files", "guard_patterns", "other", "tool/helper")),
    "tests/__init__.py": "",
    "scripts/guard.py": "from scripts import guard_files\n",
    "scripts/guard_files.py": "from scripts.guard_patterns import mask\n",
    "scripts/guard_patterns.py": "import guard_checkout\n",
    "scripts/guard_checkout.py": "",
    "scripts/other.py": "import json\n",
    "scripts/notes.txt": "",
    "scripts/tool/helper.py": "from sibling import x\n",
    "scripts/tool/sibling.py": "",
    "scripts/guard_unused.py": "",
    "tests/test_guard.py": "from scripts import guard\n",
    "tests/test_guard_files.py": "from scripts import guard_files\n",
    "tests/test_guard_patterns.py": "import guard_patterns\n",
    "tests/test_other.py": "from scripts import other\n",
    "tests/test_tool_helper.py": "from scripts.tool import helper\n",
}
GUARD, GUARD_FILES, GUARD_PATTERNS = "scripts/guard.py", "scripts/guard_files.py", "scripts/guard_patterns.py"
OTHER, HELPER = "scripts/other.py", "scripts/tool/helper.py"


@pytest.fixture
def script_repo(tmp_path):
    for rel, text in SCRIPT_FILES.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    return tmp_path


def test_dependencies_follow_script_modules_by_package_and_bare_sibling_name(script_repo):
    assert affected.dependencies(script_repo, [GUARD, "tests/test_guard.py"]) == {
        GUARD, "tests/test_guard.py", "tests/__init__.py",
        GUARD_FILES,                    # from scripts import guard_files
        GUARD_PATTERNS,                 # from scripts.guard_patterns import mask
        "scripts/guard_checkout.py",    # import guard_checkout, a bare sibling
    }


@pytest.mark.parametrize("changed, expected", [
    # a sibling module: every target whose module or tests import it, directly or transitively
    (["scripts/guard_checkout.py"], [GUARD, GUARD_FILES, GUARD_PATTERNS]),
    ([GUARD_PATTERNS], [GUARD, GUARD_FILES, GUARD_PATTERNS]),
    ([GUARD_FILES], [GUARD, GUARD_FILES]),
    ([GUARD], [GUARD]),
    (["tests/test_guard_patterns.py"], [GUARD_PATTERNS]),   # imports it by its bare name
    (["scripts/tool/sibling.py"], [HELPER]),                # a bare sibling in its own folder
    # nothing imports it, and a file that is not Python under scripts/ is no shared file
    (["scripts/guard_unused.py", "scripts/notes.txt"], []),
])
def test_a_change_to_a_script_module_picks_the_targets_that_import_it(script_repo, changed, expected):
    assert picked(script_repo, changed) == expected


# ---------------------------------------------------------------- workspace targets (df-mbi7)
# A workspace's tests put its scripts folders on sys.path and import them by bare name; its scripts
# import their siblings bare, or from the workspace root (pythonpath = .); `tests.x` is the workspace's
# own package; its conftest.py (and what that imports) is loaded for each of its tests.
WS = "workspaces/W"
ENGINE = "dataforge/plan.py"
WS_FILES = {
    "testloop.toml": "".join(
        f'[targets."{WS}/scripts/report/{name}.py"]\ntests = ["{WS}/tests/test_{name}.py"]\n\n'
        for name in ("lib", "rules")) + f'[targets."{ENGINE}"]\ntests = ["tests/test_plan.py"]\n',
    "dataforge/__init__.py": "",
    "dataforge/db.py": "",
    ENGINE: "",
    "tests/__init__.py": "",
    "tests/test_plan.py": "from dataforge import plan\n",
    f"{WS}/tests/__init__.py": "",
    f"{WS}/tests/conftest.py": "from dataforge.db import init_db\n",
    f"{WS}/tests/helper.py": "",
    f"{WS}/tests/test_lib.py": "from tests.helper import x\nfrom lib import f\nfrom sibling import s\n",
    f"{WS}/tests/test_rules.py": "from rules import g\n",
    f"{WS}/scripts/report/lib.py": "from rules import g\nfrom shared import k\n",
    f"{WS}/scripts/report/rules.py": "",
    f"{WS}/scripts/report/sibling.py": "",
    f"{WS}/scripts/report/unused.py": "",
    f"{WS}/shared.py": "",
}
LIB, RULES = f"{WS}/scripts/report/lib.py", f"{WS}/scripts/report/rules.py"


@pytest.fixture
def ws_repo(tmp_path):
    for rel, text in WS_FILES.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("changed, expected", [
    ([RULES], [LIB, RULES]),                        # lib imports rules by its bare name
    ([LIB], [LIB]),
    ([f"{WS}/tests/test_lib.py"], [LIB]),           # only a test of lib
    ([f"{WS}/tests/helper.py"], [LIB]),             # `from tests.helper`: the workspace's package, not the engine's
    ([f"{WS}/scripts/report/sibling.py"], [LIB]),   # a test of lib imports a sibling script bare
    ([f"{WS}/shared.py"], [LIB]),                   # a script imports from the workspace root
    ([f"{WS}/tests/conftest.py"], [LIB, RULES]),    # the workspace's conftest: its targets only, not the engine's
    (["dataforge/db.py"], [LIB, RULES]),            # what that conftest imports
    ([f"{WS}/scripts/report/unused.py"], []),
])
def test_a_change_in_a_workspace_picks_the_targets_that_reach_it(ws_repo, changed, expected):
    assert picked(ws_repo, changed) == expected


# ---------------------------------------------------------------- packing by measured cost (df-n8ac.39)

def item(module, shard=1, shards=1):
    return {"module": module, "shard": shard, "shards": shards}


def test_items_fill_a_job_up_to_the_cap_first_fit_heaviest_first():
    estimated = [(item("a.py"), 130), (item("b.py"), 90), (item("c.py"), 70), (item("d.py"), 60), (item("e.py"), 50)]
    assert affected.pack(estimated) == [
        {"job": 1, "items": [item("a.py"), item("c.py")]},           # 130 + 70; d (60) would pass 200
        {"job": 2, "items": [item("b.py"), item("d.py"), item("e.py")]},   # 90 + 60 + 50
    ]


def test_an_item_over_the_cap_runs_alone():
    estimated = [(item("big.py", 1, 2), 400), (item("big.py", 2, 2), 201), (item("small.py"), 10)]
    assert affected.pack(estimated) == [
        {"job": 1, "items": [item("big.py", 1, 2)]},
        {"job": 2, "items": [item("big.py", 2, 2)]},
        {"job": 3, "items": [item("small.py")]},
    ]


def test_the_estimate_is_the_only_cap_on_a_job():
    estimated = [(item(f"m{i:02}.py"), 10) for i in range(20)]
    assert affected.pack(estimated) == [{"job": 1, "items": [i for i, _ in estimated]}]


ESTIMATED = st.lists(
    st.tuples(st.sampled_from(["a.py", "b.py", "c/d.py"]), st.integers(1, 4), st.floats(0, 600)),
    unique_by=lambda t: (t[0], t[1]), max_size=40,
).map(lambda rows: [(item(m, shard, 4), seconds) for m, shard, seconds in rows])


@settings(max_examples=200, deadline=None, database=None)
@given(ESTIMATED, st.randoms(use_true_random=False))
def test_packing_puts_every_item_in_one_job_within_the_cap_whatever_the_order(estimated, rnd):
    jobs = affected.pack(estimated)
    packed = [i for job in jobs for i in job["items"]]
    assert sorted(map(json.dumps, packed)) == sorted(json.dumps(i) for i, _ in estimated)
    assert [job["job"] for job in jobs] == list(range(1, len(jobs) + 1))
    seconds = {json.dumps(i): s for i, s in estimated}
    for job in jobs:
        assert job["items"]
        load = sum(seconds[json.dumps(i)] for i in job["items"])
        assert len(job["items"]) == 1 or load <= affected.JOB_SECONDS
    shuffled = list(estimated)
    rnd.shuffle(shuffled)
    assert affected.pack(shuffled) == jobs


SPLIT_SRC = "def big(x):\n    a = x\n    b = a\n    return b\n\n\ndef small(x):\n    return x\n\n\ndef tiny():\n    pass\n"
MEASURED = {"elapsed": 120.0, "functions": {"x_big": 1.0, "x_small": 6.0, "x_tiny": 3.0}}


def test_a_shards_estimate_is_the_modules_elapsed_times_its_share_of_the_weights(repo):
    (repo / PLAN).write_text(SPLIT_SRC, encoding="utf-8")
    config = load_config(repo)
    assert affected.shard_seconds(config, PLAN, 2, MEASURED) == [120.0 * 6 / 10, 120.0 * 4 / 10]
    assert affected.shard_seconds(config, PLAN, 1, MEASURED) == [120.0]


@pytest.mark.parametrize("measured", [None, {}, {"functions": {"x_big": 1.0}}])
def test_a_module_without_a_measured_elapsed_is_estimated_at_a_fixed_time_per_shard(repo, measured):
    (repo / PLAN).write_text(SPLIT_SRC, encoding="utf-8")
    assert affected.shard_seconds(load_config(repo), PLAN, 3, measured) == [affected.UNMEASURED_SECONDS] * 3


def test_a_module_that_does_not_parse_splits_its_elapsed_evenly(repo):
    (repo / PLAN).write_text("def broken(:\n", encoding="utf-8")
    assert affected.shard_seconds(load_config(repo), PLAN, 2, MEASURED) == [60.0, 60.0]


def test_mutate_and_the_matrix_split_a_module_alike_from_the_same_costs_file(repo, monkeypatch, capsys):
    """The estimate of shard i is the measured seconds of exactly the functions mutate --shard i/n runs."""
    toml = repo / "testloop.toml"
    toml.write_text(toml.read_text(encoding="utf-8").replace(
        'tests = ["tests/test_plan*.py"]\n', 'tests = ["tests/test_plan*.py"]\nshards = 2\n'), encoding="utf-8")
    (repo / PLAN).write_text(SPLIT_SRC, encoding="utf-8")
    (repo / "costs.json").write_text(json.dumps({PLAN: MEASURED, APPLY: {"elapsed": 81.0, "functions": {}}}))
    config = load_config(repo)
    keys = [mutate_cmd._shard_keys(config, PLAN, (i, 2), load_costs(repo / "costs.json")) for i in (1, 2)]
    assert keys == [{"x_small"}, {"x_big", "x_tiny"}]   # by lines it would be x_big alone
    expected = [120.0 * sum(MEASURED["functions"][k] for k in shard) / 10 for shard in keys]
    assert affected.shard_seconds(config, PLAN, 2, MEASURED) == expected
    monkeypatch.chdir(repo)
    assert affected.main(["--matrix", "--costs", "costs.json"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == [   # APPLY 81 s + PLAN 1/2 72 s; PLAN 2/2 (48 s) would pass 200 s
        {"job": 1, "items": [item(APPLY), item(PLAN, 1, 2)]},
        {"job": 2, "items": [item(PLAN, 2, 2)]},
    ]
    assert "job 1 (~153 s)" in captured.err


def test_cli_matrix_with_a_bad_costs_file_warns_and_estimates_unmeasured(repo, monkeypatch, capsys):
    """A broken costs file in the CI cache must not fail every run until someone deletes it."""
    (repo / "costs.json").write_text("[]")
    monkeypatch.chdir(repo)
    assert affected.main(["--matrix", "--costs", "costs.json"]) == 0
    captured = capsys.readouterr()
    assert "costs file" in captured.err
    assert json.loads(captured.out) == [{"job": 1, "items": [item(APPLY), item(PLAN)]}]


def _result(config, module, shard, elapsed, functions, **extra):
    data = {"elapsed_s": elapsed, "function_seconds": functions, "complete": True, **extra}
    write_result(config.repo, "mutate", module, data, shard=shard)


def test_the_costs_file_sums_each_targets_shards_and_merges_their_functions(repo):
    toml = repo / "testloop.toml"
    toml.write_text(toml.read_text(encoding="utf-8").replace(
        'tests = ["tests/test_plan*.py"]\n', 'tests = ["tests/test_plan*.py"]\nshards = 2\n'), encoding="utf-8")
    config = load_config(repo)
    _result(config, PLAN, (1, 2), 70.25, {"x_a": 50.5})
    _result(config, PLAN, (2, 2), 30.0, {"x_b": 10.0, "x_c": 0.0})
    _result(config, PLAN, (1, 3), 999.0, {"x_a": 999.0})   # another shard count: not this target's
    assert costs.build(config) == {PLAN: {"elapsed": 100.25, "functions": {"x_a": 50.5, "x_b": 10.0, "x_c": 0.0}}}
    _result(config, APPLY, None, 12.0, {"x_f": 9.0})
    assert costs.build(config)[APPLY] == {"elapsed": 12.0, "functions": {"x_f": 9.0}}


@pytest.mark.parametrize("first", ["missing", "partial"])
def test_a_target_with_a_missing_or_partial_shard_has_no_elapsed_but_keeps_its_complete_functions(repo, first):
    """A shard that died left no result, and one the budget cut short measured the budget, not the
    shard: neither is a measure. The module's elapsed would be wrong, so it is left out and affected
    estimates the module as unmeasured; the functions of the complete shards still split it."""
    toml = repo / "testloop.toml"
    toml.write_text(toml.read_text(encoding="utf-8").replace(
        'tests = ["tests/test_plan*.py"]\n', 'tests = ["tests/test_plan*.py"]\nshards = 2\n'), encoding="utf-8")
    config = load_config(repo)
    if first == "partial":
        _result(config, PLAN, (1, 2), 900.0, {"x_a": 400.0}, complete=False)
    _result(config, PLAN, (2, 2), 30.0, {"x_b": 10.0})
    assert costs.build(config) == {PLAN: {"functions": {"x_b": 10.0}}}
    _result(config, APPLY, None, 900.0, {"x_f": 9.0}, complete=False)
    assert APPLY not in costs.build(config)


def test_costs_cli_prints_a_file_the_matrix_and_mutate_read(repo, monkeypatch, capsys):
    config = load_config(repo)
    _result(config, APPLY, None, 12.0, {"x_f": 9.0})
    monkeypatch.chdir(repo)
    assert costs.main([]) == 0
    captured = capsys.readouterr()
    (repo / "costs.json").write_text(captured.out, encoding="utf-8")
    assert load_costs(repo / "costs.json") == {APPLY: {"elapsed": 12.0, "functions": {"x_f": 9.0}}}
    assert "1 of 2 targets measured" in captured.err
