"""scripts/testloop/gate_cmd.py: the score it prints and the min_score it suggests (df-0kv9); the split
of a target into shards that mutate runs and gate sums (df-n8ac.32); the cache of mutmut's verdicts
that mutate --cache-dir keeps (df-n8ac.38, df-n8ac.44); the measured costs that split the shards (df-n8ac.39)."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "testloop"))

import gate_cmd  # the test loop tools are scripts, not an installed package
import mutate_cmd
from common import Config, ToolError, costs_or_empty, load_costs, mutation_score, result_path, write_result
from gate_cmd import four_places_down


@pytest.mark.parametrize("killed,total", [(29, 31), (121, 132), (2, 3), (1, 1), (0, 7), (93, 100)])
def test_a_suggested_min_score_never_exceeds_the_score_it_came_from(killed, total):
    score = killed / total
    shown = float(four_places_down(score))
    assert shown <= score
    assert score - shown < 1e-4


def test_29_of_31_is_shown_as_0_9354_not_0_9355():
    assert four_places_down(29 / 31) == "0.9354"


def _gate(tmp_path, capsys, score, min_score):
    """Run gate on a complete mutate result with this score; return its stderr."""
    (tmp_path / "mod.py").write_text("x = 1\n")
    write_result(tmp_path, "mutate", "mod.py", {
        "complete": True, "score": score, "killed": 0, "total": 0, "survived": [],
    })
    config = Config(repo=tmp_path, targets={"mod.py": {"min_score": min_score}})
    _, code = gate_cmd.main(config, "mod.py")
    assert code == 0
    return capsys.readouterr().err


@pytest.mark.parametrize("score,min_score", [(0.94562, 0.9456), (0.9456, 0.9456), (1.0, 1.0)])
def test_no_raise_hint_when_the_rounded_score_is_min_score(tmp_path, capsys, score, min_score):
    """df-ahua.25: a score just above min_score rounds down to it; suggesting it again is a no-op."""
    assert "raise min_score" not in _gate(tmp_path, capsys, score, min_score)


def test_raise_hint_names_the_rounded_down_score(tmp_path, capsys):
    err = _gate(tmp_path, capsys, 26 / 27, 0.9259)
    assert "raise min_score to 0.9629 " in err


# ---------------------------------------------------------------- shards (df-n8ac.32)

SHARDED_SRC = (
    "import os\n\n\n"
    "def big(x):\n    a = x + 1\n    b = a * 2\n    c = b - 3\n    return c\n\n\n"
    "class Box:\n    size = 1\n\n"
    "    def __init__(self, x):\n        self.x = x\n\n"
    "    async def fetch(self):\n        return self.x\n\n\n"
    "def small(x):\n    return x\n\n\n"
    "if os.name:\n    def hidden():\n        return 1\n"
)


def test_function_weights_key_top_level_functions_and_methods_like_mutmut():
    """Top-level functions are x_<name>, methods of top-level classes xǁ<Class>ǁ<name> (mutmut 3's
    make_mutant_key); a function under an `if` is not mutated by mutmut, so it is not a key."""
    assert mutate_cmd.function_weights(SHARDED_SRC) == {
        "x_big": 5, "xǁBoxǁ__init__": 2, "xǁBoxǁfetch": 2, "x_small": 2,
    }


@pytest.mark.parametrize("n", [1, 2, 3, 4, 7])
def test_every_function_is_in_exactly_one_shard(n):
    weights = mutate_cmd.function_weights(SHARDED_SRC)
    shards = mutate_cmd.partition(weights, n)
    assert len(shards) == n
    flat = [key for shard in shards for key in shard]
    assert sorted(flat) == sorted(weights)


def test_partition_is_deterministic_whatever_the_input_order():
    weights = {f"x_f{i}": (i * 7) % 5 + 1 for i in range(20)}
    reordered = dict(reversed(list(weights.items())))
    assert mutate_cmd.partition(weights, 4) == mutate_cmd.partition(reordered, 4)


def test_partition_balances_by_weight_heaviest_first():
    """LPT: the heaviest goes to the lightest shard, ties to the lowest index."""
    weights = {"x_a": 10, "x_b": 6, "x_c": 5, "x_d": 4, "x_e": 1}
    assert mutate_cmd.partition(weights, 2) == [["x_a", "x_d"], ["x_b", "x_c", "x_e"]]
    loads = [sum(weights[k] for k in shard) for shard in mutate_cmd.partition(weights, 2)]
    assert max(loads) - min(loads) <= max(weights.values())


def test_more_shards_than_functions_leaves_the_last_ones_empty():
    assert mutate_cmd.partition({"x_a": 3, "x_b": 2}, 4) == [["x_a"], ["x_b"], [], []]


def test_shard_globs_name_mutmuts_mutants_of_the_shard():
    assert mutate_cmd.shard_globs("dataforge/cron.py", ["x_parse", "xǁCronǁnext"]) == [
        "dataforge.cron.x_parse__mutmut_*", "dataforge.cron.xǁCronǁnext__mutmut_*",
    ]
    # mutmut drops __init__ from a package's dotted name (format_utils.get_mutant_name)
    assert mutate_cmd.shard_globs("dataforge/tasks/__init__.py", ["x_f"]) == ["dataforge.tasks.x_f__mutmut_*"]


@pytest.mark.parametrize("key, function", [
    ("dataforge.cron.x_parse__mutmut_3", "x_parse"),
    ("dataforge.cron.xǁCronǁ__init____mutmut_12", "xǁCronǁ__init__"),
    # a function whose own name holds _mutmut_ (df-0klw): the mutant suffix is the last one
    ("scripts.testloop.mutate_cmd.x__mutmut_version__mutmut_3", "x__mutmut_version"),
])
def test_function_of_a_mutant_key(key, function):
    assert mutate_cmd.function_of(key) == function


def test_a_slow_run_warns_to_raise_shards():
    assert mutate_cmd.slow_warning("m.py", 239.9) is None
    warning = mutate_cmd.slow_warning("m.py", 241)
    assert "shards" in warning
    assert "m.py" in warning


def test_shard_result_files_are_named_by_shard_and_one_of_one_is_the_plain_name(tmp_path):
    assert result_path(tmp_path, "mutate", "a/b.py", (1, 1)) == result_path(tmp_path, "mutate", "a/b.py")
    assert result_path(tmp_path, "mutate", "a/b.py", (2, 4)).name == "mutate-a_b.py.shard-2of4.json"


def _sharded_repo(tmp_path, min_score=0.75, shards=2):
    (tmp_path / "mod.py").write_text("x = 1\n")
    return Config(repo=tmp_path, targets={"mod.py": {"min_score": min_score, "shards": shards}})


def _shard_result(config, shard, killed, survived=(), not_tested=(), **extra):
    data = {
        "complete": True, "killed": killed, "tests": ["tests/t.py"],
        "survived": [{"id": s, "diff": ""} for s in survived], "not_tested": list(not_tested),
        "timeout": [], "total": killed + len(survived) + len(not_tested),
        "score": mutation_score(killed, len(survived), len(not_tested)), **extra,
    }
    write_result(config.repo, "mutate", "mod.py", data, shard=shard)


def test_gate_sums_the_shards_before_comparing(tmp_path, capsys):
    """Shard 1 alone (1/2) is below 0.75 and shard 2 alone is 1.0; together 4/5 = 0.8 passes."""
    config = _sharded_repo(tmp_path)
    _shard_result(config, (1, 2), killed=1, survived=["mod.x_a__mutmut_1"])
    _shard_result(config, (2, 2), killed=3)
    out, code = gate_cmd.main(config, "mod.py")
    assert code == 0
    data = json.loads(out.read_text())
    assert (data["score"], data["killed"], data["total"]) == (0.8, 4, 5)
    assert data["survived"] == ["mod.x_a__mutmut_1"]
    assert "score 0.8000" in capsys.readouterr().err


def test_gate_fails_when_the_summed_score_is_below_min_score(tmp_path, capsys):
    config = _sharded_repo(tmp_path, min_score=0.9)
    _shard_result(config, (1, 2), killed=1, survived=["mod.x_a__mutmut_1"], not_tested=["mod.x_a__mutmut_2"])
    _shard_result(config, (2, 2), killed=0, score=None)
    _, code = gate_cmd.main(config, "mod.py")
    assert code == 1
    assert "mod.x_a__mutmut_1" in capsys.readouterr().err


def test_a_shard_without_mutants_counts_zero(tmp_path):
    config = _sharded_repo(tmp_path, min_score=1.0, shards=3)
    _shard_result(config, (1, 3), killed=2)
    _shard_result(config, (2, 3), killed=0)
    _shard_result(config, (3, 3), killed=1)
    assert gate_cmd.main(config, "mod.py")[1] == 0


def test_gate_refuses_when_a_shard_is_missing(tmp_path):
    config = _sharded_repo(tmp_path)
    _shard_result(config, (1, 2), killed=1)
    with pytest.raises(ToolError, match="shard 2/2"):
        gate_cmd.main(config, "mod.py")


def test_gate_refuses_shards_of_another_count(tmp_path):
    """Results of a 3-way split do not stand in for the 2 shards the target asks for."""
    config = _sharded_repo(tmp_path)
    for i in (1, 2, 3):
        _shard_result(config, (i, 3), killed=1)
    with pytest.raises(ToolError, match="shard 1/2"):
        gate_cmd.main(config, "mod.py")


def test_gate_refuses_a_stale_shard(tmp_path):
    config = _sharded_repo(tmp_path)
    _shard_result(config, (1, 2), killed=1)
    (tmp_path / "mod.py").write_text("x = 2\n")
    _shard_result(config, (2, 2), killed=1)
    with pytest.raises(ToolError, match=r"shard 1/2.*stale"):
        gate_cmd.main(config, "mod.py")


def test_gate_refuses_a_partial_shard(tmp_path):
    config = _sharded_repo(tmp_path)
    _shard_result(config, (1, 2), killed=1)
    _shard_result(config, (2, 2), killed=1, complete=False)
    with pytest.raises(ToolError, match=r"shard 2/2.*partial"):
        gate_cmd.main(config, "mod.py")


def test_gate_refuses_a_file_whose_shard_field_disagrees_with_its_name(tmp_path):
    config = _sharded_repo(tmp_path)
    _shard_result(config, (1, 2), killed=1)
    _shard_result(config, (2, 2), killed=1)
    path = result_path(tmp_path, "mutate", "mod.py", (2, 2))
    path.write_text(path.read_text().replace('"shard": [\n    2,', '"shard": [\n    1,'))
    with pytest.raises(ToolError, match="shard 2/2"):
        gate_cmd.main(config, "mod.py")


@pytest.mark.parametrize("shards", [0, -1, "2", 1.5, True])
def test_an_invalid_shards_value_is_a_tool_error(tmp_path, shards):
    config = _sharded_repo(tmp_path, shards=shards)
    with pytest.raises(ToolError, match="shards"):
        gate_cmd.main(config, "mod.py")


def test_a_shard_of_a_module_that_does_not_parse_is_a_tool_error(tmp_path):
    config = _sharded_repo(tmp_path)
    (tmp_path / "mod.py").write_text("def broken(:\n", encoding="utf-8")
    with pytest.raises(ToolError, match="mod.py does not parse"):
        mutate_cmd._shard_keys(config, "mod.py", (1, 2))


def test_a_shard_count_other_than_the_targets_is_a_tool_error(tmp_path):
    """A 1/3 result of a target with shards = 2 would be a file the gate never reads (df-0klw)."""
    config = _sharded_repo(tmp_path, shards=2)
    with pytest.raises(ToolError, match="shards = 2"):
        mutate_cmd._shard_keys(config, "mod.py", (1, 3))


def test_the_whole_module_is_always_allowed(tmp_path):
    assert mutate_cmd._shard_keys(_sharded_repo(tmp_path, shards=2), "mod.py", (1, 1)) is None


def _tree(root):
    return {p.relative_to(root).as_posix(): p.read_text() for p in sorted(root.rglob("*")) if p.is_file()}


def _mutants_dir(root, tag):
    """A mutmut mutants/ with its state (stats, a .meta) and the files it rewrites every run."""
    for rel in ("mutmut-stats.json", "pkg/mod.py.meta", "pkg/mod.py", "pkg/mod.py.spans", "tests/test_mod.py"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(f"{tag} {rel}")
    return root


TESTS_HASHES = {"tests/test_mod.py": "sha256:t"}
TOOL_CONFIG = "[tool.mutmut]\ntimeout_multiplier = 3.0\n"


def test_the_cache_keeps_only_mutmuts_state(tmp_path):
    copy = tmp_path / "copy"
    _mutants_dir(copy / "mutants", "run")
    mutate_cmd.save_cache(copy, tmp_path / "cache", "pkg/mod.py", {"x_f": "sha256:1"}, TESTS_HASHES, TOOL_CONFIG)
    assert _tree(tmp_path / "cache") == {
        "mutants/mutmut-stats.json": "run mutmut-stats.json", "mutants/pkg/mod.py.meta": "run pkg/mod.py.meta",
        "mutants/testloop-function-text.json": TEXTS, "mutants/testloop-test-files.json": TESTS,
        "mutants/testloop-tool-config.json": CONFIG}


def test_a_new_cache_replaces_the_old_one_and_its_leftovers(tmp_path):
    cache = tmp_path / "cache"
    _mutants_dir(cache / "mutants", "old")
    (cache / "mutants" / "gone.py.meta").write_text("old")
    _mutants_dir(cache / "mutants.new", "cut short")
    copy = tmp_path / "copy"
    _mutants_dir(copy / "mutants", "new")
    mutate_cmd.save_cache(copy, cache, "pkg/mod.py", {"x_f": "sha256:1"}, TESTS_HASHES, TOOL_CONFIG)
    assert _tree(cache) == {
        "mutants/mutmut-stats.json": "new mutmut-stats.json", "mutants/pkg/mod.py.meta": "new pkg/mod.py.meta",
        "mutants/testloop-function-text.json": TEXTS, "mutants/testloop-test-files.json": TESTS,
        "mutants/testloop-tool-config.json": CONFIG}


def test_a_restored_cache_brings_back_the_state_and_an_empty_one_nothing(tmp_path):
    cache, copy = tmp_path / "cache", tmp_path / "copy"
    assert mutate_cmd.restore_cache(cache, copy, "pkg/mod.py", TOOL_CONFIG) is None
    assert not (copy / "mutants").exists()
    _mutants_dir(cache / "mutants", "cached")
    # no tool config recorded (a cache from before df-n8ac.48): nothing is restored
    assert mutate_cmd.restore_cache(cache, copy, "pkg/mod.py", TOOL_CONFIG) is None
    assert not (copy / "mutants").exists()
    (cache / "mutants" / "testloop-tool-config.json").write_text(CONFIG)
    # no texts and no test files recorded: nothing but killed verdicts can be reused
    assert mutate_cmd.restore_cache(cache, copy, "pkg/mod.py", TOOL_CONFIG) == mutate_cmd.Cached(texts={}, tests=None)
    assert _tree(copy) == {
        "mutants/mutmut-stats.json": "cached mutmut-stats.json", "mutants/pkg/mod.py.meta": "cached pkg/mod.py.meta"}


TEXTS = '{\n  "pkg/mod.py": {\n    "x_f": "sha256:1"\n  }\n}'
TESTS = '{\n  "pkg/mod.py": {\n    "tests/test_mod.py": "sha256:t"\n  }\n}'
CONFIG = '{\n  "pkg/mod.py": "[tool.mutmut]\\ntimeout_multiplier = 3.0\\n"\n}'


def test_a_restored_cache_returns_the_texts_and_tests_it_was_found_on_and_leaves_the_meta_as_it_was(tmp_path):
    cache, copy = tmp_path / "cache", tmp_path / "copy"
    mutate_cmd.save_cache(_mutants_dir(tmp_path / "run" / "mutants", "run").parent, cache, "pkg/mod.py",
                          {"x_f": "sha256:1"}, TESTS_HASHES, TOOL_CONFIG)
    # another module, or this one under another config: nothing is restored (df-n8ac.48)
    assert mutate_cmd.restore_cache(cache, copy, "other/mod.py", TOOL_CONFIG) is None
    assert mutate_cmd.restore_cache(cache, copy, "pkg/mod.py", TOOL_CONFIG.replace("3.0", "4.0")) is None
    assert not (copy / "mutants").exists()
    assert mutate_cmd.restore_cache(cache, copy, "pkg/mod.py", TOOL_CONFIG) == mutate_cmd.Cached(
        {"x_f": "sha256:1"}, TESTS_HASHES)
    assert (copy / "mutants" / "pkg" / "mod.py.meta").read_text() == "run pkg/mod.py.meta"
    assert not (copy / "mutants" / "testloop-function-text.json").exists()
    assert not (copy / "mutants" / "testloop-test-files.json").exists()


@pytest.mark.parametrize(("texts", "tests", "stats_kept"), [
    ({"x_f": "sha256:1"}, TESTS_HASHES, True),
    (None, None, True),                                   # nothing to compare against: as recorded
    ({"x_f": "sha256:2"}, TESTS_HASHES, False),           # a function changed (df-2dlq)
    ({"x_f": "sha256:1", "x_g": "sha256:3"}, TESTS_HASHES, False),   # a new function (df-2dlq)
    ({"x_f": "sha256:1"}, {"tests/test_mod.py": "sha256:u"}, False),  # a test file changed (df-g4qq)
])
def test_a_restored_cache_keeps_mutmuts_stats_only_while_no_function_or_test_file_changed(
        tmp_path, texts, tests, stats_kept):
    """mutmut, from restored stats, collects stats only for tests with a new name: they go when a
    function or a test file changed, so it collects them all; the verdicts in the .meta stay."""
    cache, copy = tmp_path / "cache", tmp_path / "copy"
    mutate_cmd.save_cache(_mutants_dir(tmp_path / "run" / "mutants", "run").parent, cache, "pkg/mod.py",
                          {"x_f": "sha256:1"}, TESTS_HASHES, TOOL_CONFIG)
    assert mutate_cmd.restore_cache(cache, copy, "pkg/mod.py", TOOL_CONFIG, texts, tests) == mutate_cmd.Cached(
        {"x_f": "sha256:1"}, TESTS_HASHES)
    assert (copy / "mutants" / "mutmut-stats.json").exists() == stats_kept
    assert (copy / "mutants" / "pkg" / "mod.py.meta").read_text() == "run pkg/mod.py.meta"


def test_test_hashes_name_each_test_file_with_the_hash_of_its_bytes(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("a")
    (tmp_path / "tests" / "test_b.py").write_text("b")
    hashes = mutate_cmd.test_hashes(tmp_path, ["tests/test_a.py", "tests/test_b.py"])
    assert sorted(hashes) == ["tests/test_a.py", "tests/test_b.py"]
    assert hashes["tests/test_a.py"] != hashes["tests/test_b.py"]
    (tmp_path / "tests" / "test_a.py").write_text("a changed")
    assert mutate_cmd.test_hashes(tmp_path, ["tests/test_a.py", "tests/test_b.py"])["tests/test_a.py"] \
        != hashes["tests/test_a.py"]


@pytest.mark.parametrize("status,same_text,same_tests,reused", [
    ("killed", True, False, True),       # a killed verdict stays through a changed test (df-n8ac.38)
    ("killed", False, True, False),      # a function whose text changed is always tested again
    ("survived", True, False, False),    # a changed test may kill a survivor
    ("survived", True, True, True),      # same function, same tests: the same verdict (df-n8ac.44)
    ("timeout", True, True, True),
    ("no tests", True, True, True),
    ("timeout", True, False, False),
    ("no tests", True, False, False),
    ("survived", False, True, False),
    ("not checked", True, True, False),  # no verdict to reuse
    ("suspicious", True, True, False),   # a status we do not know is never reused
])
def test_which_cached_verdicts_are_reused(status, same_text, same_tests, reused):
    assert mutate_cmd.reusable(status, same_text=same_text, same_tests=same_tests) is reused


def test_a_functions_text_hash_sees_comments_and_decorators_but_not_other_functions():
    src = "def f(x):\n    return x\n\n\nclass C:\n    @staticmethod\n    def m():\n        pass\n"
    base = mutate_cmd.function_texts(src)
    assert set(base) == {"x_f", "xǁCǁm"}
    commented = mutate_cmd.function_texts(src.replace("return x", "return x  # pragma: no mutate"))
    assert commented["x_f"] != base["x_f"]
    assert commented["xǁCǁm"] == base["xǁCǁm"]
    undecorated = mutate_cmd.function_texts(src.replace("    @staticmethod\n", ""))
    assert undecorated["xǁCǁm"] != base["xǁCǁm"]
    assert undecorated["x_f"] == base["x_f"]


# ---------------------------------------------------------------- measured costs (df-n8ac.39)

def test_cost_weights_are_the_measured_seconds_of_each_function():
    measured = {"elapsed": 30, "functions": {"x_big": 1.0, "xǁBoxǁ__init__": 8.0, "xǁBoxǁfetch": 0, "x_small": 3.0}}
    assert mutate_cmd.cost_weights(SHARDED_SRC, measured) == {
        "x_big": 1.0, "xǁBoxǁ__init__": 8.0, "xǁBoxǁfetch": 0, "x_small": 3.0}


def test_a_function_without_a_measure_weighs_its_lines_at_the_modules_median_seconds_per_line():
    """x_big (5 lines) is new since the measure; the measured rates are 4, 1 and 0.5 s/line: median 1."""
    measured = {"elapsed": 30, "functions": {"xǁBoxǁ__init__": 8.0, "xǁBoxǁfetch": 2.0, "x_small": 1.0}}
    weights = mutate_cmd.cost_weights(SHARDED_SRC, measured)
    assert weights["x_big"] == 5 * 1.0
    assert weights["xǁBoxǁ__init__"] == 8.0


@pytest.mark.parametrize("measured", [
    None, {}, {"elapsed": 12}, {"elapsed": 12, "functions": {"x_gone": 4.0}},
    # measured, but no second: every function would weigh 0 and land in shard 1
    {"elapsed": 12, "functions": {"x_big": 0, "x_small": 0.0}},
])
def test_a_module_without_measured_seconds_weighs_lines_as_without_costs(measured):
    assert mutate_cmd.cost_weights(SHARDED_SRC, measured) == mutate_cmd.function_weights(SHARDED_SRC)


def test_costs_move_a_function_to_another_shard(tmp_path):
    """By lines x_big (5) is a shard alone; measured, x_small is the slow one and stands alone."""
    config = _sharded_repo(tmp_path)
    (tmp_path / "mod.py").write_text(SHARDED_SRC)
    assert mutate_cmd._shard_keys(config, "mod.py", (1, 2)) == {"x_big"}
    costs = {"mod.py": {"elapsed": 40, "functions": {
        "x_big": 1.0, "xǁBoxǁ__init__": 1.0, "xǁBoxǁfetch": 1.0, "x_small": 9.0}}}
    assert mutate_cmd._shard_keys(config, "mod.py", (1, 2), costs) == {"x_small"}
    assert mutate_cmd._shard_keys(config, "mod.py", (2, 2), costs) == {"x_big", "xǁBoxǁ__init__", "xǁBoxǁfetch"}
    assert mutate_cmd._shard_keys(config, "mod.py", (1, 2), {"other.py": costs["mod.py"]}) == {"x_big"}


def _meta(copy, module, exit_codes, durations):
    path = copy / "mutants" / (module + ".meta")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "exit_code_by_key": exit_codes, "hash_by_function_name": {}, "type_check_error_by_key": {},
        "durations_by_key": durations, "estimated_durations_by_key": {}}))


def test_function_seconds_sum_the_durations_of_each_functions_mutants(tmp_path):
    """A function without mutants took 0 s; one with a mutant never run (exit code None: a partial run,
    or another shard's function) has no measure; a mutant with a verdict but no duration (no tests)
    adds nothing."""
    _meta(tmp_path, "pkg/mod.py", {
        "pkg.mod.x_f__mutmut_1": 1, "pkg.mod.x_f__mutmut_2": 0, "pkg.mod.x_f__mutmut_3": 33,
        "pkg.mod.xǁCǁm__mutmut_1": 1, "pkg.mod.xǁCǁm__mutmut_2": None,
        "pkg.mod.x_other__mutmut_1": 1,
    }, {"pkg.mod.x_f__mutmut_1": 0.25, "pkg.mod.x_f__mutmut_2": 1.5, "pkg.mod.xǁCǁm__mutmut_1": 2.0,
        "pkg.mod.x_other__mutmut_1": 7.0})
    assert mutate_cmd.function_seconds(tmp_path, "pkg/mod.py", {"x_f", "xǁCǁm", "x_empty"}) == {
        "x_empty": 0.0, "x_f": 1.75}


def test_function_seconds_without_a_meta_measure_nothing(tmp_path):
    assert mutate_cmd.function_seconds(tmp_path, "pkg/mod.py", {"x_f"}) == {}


def test_a_costs_file_loads_and_a_bad_one_is_a_tool_error(tmp_path):
    good = {"a.py": {"elapsed": 12.5, "functions": {"x_f": 3, "x_g": 0.5}}, "b.py": {"elapsed": 0, "functions": {}}}
    path = tmp_path / "costs.json"
    path.write_text(json.dumps(good))
    assert load_costs(path) == good
    for bad in ("[1]", '{"a.py": 3}', '{"a.py": {"elapsed": "slow"}}', '{"a.py": {"elapsed": -1}}',
                '{"a.py": {"functions": {"x_f": "1"}}}', '{"a.py": {"functions": [1]}}',
                '{"a.py": {"elapsed": true}}', "{not json"):
        path.write_text(bad)
        with pytest.raises(ToolError, match="costs"):
            load_costs(path)
    with pytest.raises(ToolError, match="costs"):
        load_costs(tmp_path / "missing.json")


def test_a_bad_costs_file_warns_and_measures_nothing(tmp_path, capsys):
    """A broken file in the CI cache must not stop every run until someone deletes it: the split and
    the packing fall back to lines and 60 s, and the next full run writes a good one."""
    path = tmp_path / "costs.json"
    path.write_text("[]")
    assert costs_or_empty(path) == {}
    assert "costs file" in capsys.readouterr().err
    path.write_text('{"a.py": {"elapsed": 1}}')
    assert costs_or_empty(path) == {"a.py": {"elapsed": 1}}


@pytest.mark.parametrize("value, line", [(3, "timeout_multiplier = 3.0"), (2.5, "timeout_multiplier = 2.5")])
def test_a_targets_timeout_multiplier_reaches_mutmut(tmp_path, value, line):
    """df-n8ac.42: a mutant that hangs (engine's run_id -> None deadlocks on the run lock) costs
    mutmut's whole timeout, (tests' seconds + 1) x 15 by default; a target can lower the factor."""
    config = Config(repo=tmp_path, targets={"mod.py": {"timeout_multiplier": value}})
    mutate_cmd._write_tool_config(config, "mod.py", ["tests/t.py"], tmp_path)
    assert line in (tmp_path / "pyproject.toml").read_text(encoding="utf-8").splitlines()


def test_without_a_timeout_multiplier_mutmut_keeps_its_default(tmp_path):
    mutate_cmd._write_tool_config(Config(repo=tmp_path, targets={"mod.py": {}}), "mod.py", ["tests/t.py"], tmp_path)
    assert "timeout_multiplier" not in (tmp_path / "pyproject.toml").read_text(encoding="utf-8")


@pytest.mark.parametrize("value", [0, -1, "3", True])
def test_a_bad_timeout_multiplier_is_a_tool_error(tmp_path, value):
    config = Config(repo=tmp_path, targets={"mod.py": {"timeout_multiplier": value}})
    with pytest.raises(ToolError, match="timeout_multiplier"):
        mutate_cmd._write_tool_config(config, "mod.py", ["tests/t.py"], tmp_path)
