"""scripts/testloop/gate_cmd.py: the score it prints and the min_score it suggests (df-0kv9)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "testloop"))

from gate_cmd import four_places_down  # the test loop tools are scripts, not an installed package


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
    import gate_cmd
    from common import Config, write_result

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
