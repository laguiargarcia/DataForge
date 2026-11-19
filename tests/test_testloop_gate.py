"""scripts/testloop/gate_cmd.py: the score it prints and the min_score it suggests (df-0kv9); the split
of a target into shards that mutate runs and gate sums (df-n8ac.32)."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "testloop"))

import gate_cmd  # the test loop tools are scripts, not an installed package
import mutate_cmd
from common import Config, ToolError, mutation_score, result_path, write_result
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
