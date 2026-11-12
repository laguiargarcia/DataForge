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
