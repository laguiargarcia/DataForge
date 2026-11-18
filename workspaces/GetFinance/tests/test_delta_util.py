"""tests/_delta_util.py: where the `integration` tests find the real data (DataForge df-aef7.1).

GETFINANCE_DATA when it is set (the dev clone on the Linux disk reads the Windows workspace's data/,
read-only); otherwise the checkout's own data/ (its main checkout's, from a worktree).
"""
from tests._delta_util import real_data_dir


def test_the_variable_wins_over_the_checkouts_own_data(tmp_path):
    """A data/ left in the dev clone (even empty) must not switch the variable off."""
    (tmp_path / "data").mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    assert real_data_dir(tmp_path, {"GETFINANCE_DATA": str(elsewhere)}) == elsewhere


def test_without_the_variable_the_checkouts_own_data(tmp_path):
    (tmp_path / "data").mkdir()
    assert real_data_dir(tmp_path, {}) == tmp_path / "data"


def test_without_data_the_variable_names_it(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    assert real_data_dir(tmp_path, {"GETFINANCE_DATA": str(elsewhere)}) == elsewhere


def test_without_data_or_variable_the_missing_data_dir_makes_integration_tests_skip(tmp_path):
    assert real_data_dir(tmp_path, {}) == tmp_path / "data"
    assert real_data_dir(tmp_path, {"GETFINANCE_DATA": ""}) == tmp_path / "data"
