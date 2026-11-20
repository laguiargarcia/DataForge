"""tests/_delta_util.py: where the `integration` tests find the real data (DataForge df-aef7.1).

GETFINANCE_DATA when it is set (a checkout on the Linux disk reads the Windows workspace's data/,
read-only); otherwise the checkout's own data/ (its main checkout's, from a worktree).
"""
import os
import subprocess

from tests._delta_util import real_data_dir


def test_the_variable_wins_over_the_checkouts_own_data(tmp_path):
    """A data/ left in the checkout (even empty) must not switch the variable off."""
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


def test_from_a_worktree_the_same_workspace_folder_of_the_main_checkout(tmp_path, monkeypatch):
    """The workspace is a folder of the DataForge repo (df-bsb2.2): from a worktree, its data/ is the
    one of the same folder in the main checkout, not data/ at the repo's root."""
    for name in [n for n in os.environ if n.startswith("GIT_")]:
        monkeypatch.delenv(name)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
    main = tmp_path / "main"
    (main / "workspaces" / "W").mkdir(parents=True)
    (main / "workspaces" / "W" / "x.py").write_text("x = 1\n", encoding="utf-8")
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "init"],
                 ["worktree", "add", "-q", str(tmp_path / "wt")]):
        subprocess.run([*git, *args], cwd=main, check=True, capture_output=True)
    found = real_data_dir(tmp_path / "wt" / "workspaces" / "W", {})
    assert found.resolve() == (main / "workspaces" / "W" / "data").resolve()
    assert real_data_dir(main / "workspaces" / "W", {}).resolve() == (main / "workspaces" / "W" / "data").resolve()
