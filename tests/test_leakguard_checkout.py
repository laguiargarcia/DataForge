"""Tests for scripts/leakguard_checkout.py: the main checkout, where the guard's gitignored files live."""

import pytest

from scripts import leakguard_checkout
from tests.leakguard_fixtures import _clean_env, _git, _init



@pytest.fixture
def clean_git(tmp_path, monkeypatch):
    """D5: no inherited GIT_* (a git hook exports them) and no repo above tmp_path."""
    _clean_env(monkeypatch)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    return tmp_path


def test_from_a_worktree_the_main_checkout_is_found(clean_git):
    """D1: commits are made from worktrees, where gitignored files (.leakguard-local,
    .leakguard-workspaces) do not exist."""
    main = _init(clean_git / "main")
    (main / "README.md").write_text("x\n", encoding="utf-8")
    _git("add", "-A", cwd=main)
    _git("commit", "-qm", "init", cwd=main)
    wt = clean_git / "wt"
    _git("worktree", "add", "-q", str(wt), cwd=main)
    assert leakguard_checkout._main_checkout(wt)[0].resolve() == main.resolve()
    assert leakguard_checkout._main_checkout(wt)[1] is None
    assert leakguard_checkout._main_checkout(main)[0].resolve() == main.resolve()


def test_a_worktree_whose_main_checkout_is_gone_is_an_error(clean_git):
    wt = clean_git / "wt"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: /nonexistent/main/.git/worktrees/wt\n", encoding="utf-8")
    root, error = leakguard_checkout._main_checkout(wt)
    assert root == wt
    assert error == ("this is a worktree and its main checkout could not be found, so "
                     ".leakguard-local and .leakguard-workspaces were not read")


def test_outside_a_git_repo_the_root_is_local_and_not_an_error(clean_git):
    assert leakguard_checkout._main_checkout(clean_git) == (clean_git, None)
