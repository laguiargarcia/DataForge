"""scripts/testloop/check_cmd.py: rebuilding pytest node ids from junit classnames (df-ahua.26)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "testloop"))

from check_cmd import _nodeid  # the test loop tools are scripts, not an installed package

TESTS = ["tests/test_mod.py", "tests/test_mod_more.py"]


@pytest.mark.parametrize("classname,name,expected", [
    ("tests.test_mod", "test_a", "tests/test_mod.py::test_a"),
    ("tests.test_mod.TestX", "test_a", "tests/test_mod.py::TestX::test_a"),
    ("tests.test_mod_more", "test_a", "tests/test_mod_more.py::test_a"),
    # rootdir above the repo: the classname starts with a folder whose name has dots
    (".worktrees.df-ahua.23a.tests.test_mod", "test_a", "tests/test_mod.py::test_a"),
    (".worktrees.df-ahua.23a.tests.test_mod.TestX", "test_a", "tests/test_mod.py::TestX::test_a"),
])
def test_a_known_test_file_gives_its_node_id(tmp_path, classname, name, expected):
    assert _nodeid(tmp_path, classname, name, TESTS) == expected


def test_a_longer_file_name_is_not_taken_for_its_prefix(tmp_path):
    assert _nodeid(tmp_path, "tests.test_mod_more.TestX", "t", TESTS) == "tests/test_mod_more.py::TestX::t"


def test_an_unknown_classname_falls_back_to_the_file_on_disk(tmp_path):
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "test_o.py").write_text("")
    assert _nodeid(tmp_path, "other.test_o.TestY", "t", TESTS) == "other/test_o.py::TestY::t"
    assert _nodeid(tmp_path, "nowhere.test_z", "t", TESTS) == "nowhere::test_z::t"
