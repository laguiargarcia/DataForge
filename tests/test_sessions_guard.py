"""scripts/sessions/guard.py and state.py: the PreToolUse guard keeps sessions in their own worktrees
(df-2f3j). Temp git repos only; no bd."""

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from tests.sessions_repo import (
    ME, OTHER_SESSION, SCRIPTS, add_worktree, dead_pid, git, make_repo, register,
)

import guard  # on sys.path through tests.sessions_repo
import state

NO_PROC = pytest.mark.skipif(sys.platform == "win32",
                             reason="liveness reads /proc; on Windows no session is ever presumed dead")


@pytest.fixture
def repo(tmp_path):
    return make_repo(tmp_path)


def edit(path, session=ME, cwd=None, agent=None, tool="Edit"):
    e = {"hook_event_name": "PreToolUse", "session_id": session, "cwd": str(cwd or path),
         "tool_name": tool, "tool_input": {"file_path": str(path)}}
    if agent:
        e["agent_id"] = agent
    return e


def bash(command, cwd, session=ME, agent=None):
    e = {"hook_event_name": "PreToolUse", "session_id": session, "cwd": str(cwd),
         "tool_name": "Bash", "tool_input": {"command": command}}
    if agent:
        e["agent_id"] = agent
    return e


def q(path):
    return shlex.quote(str(path))


def run(event, project, mode="warn"):
    return guard.run(event, project, mode)


def context(out):
    return out["hookSpecificOutput"]["additionalContext"]


# --------------------------------------------------------------------------- repository layout


def test_repo_layout_is_read_from_files_in_main_and_in_a_worktree(repo):
    wt = add_worktree(repo, "df-abc1")
    for start in (repo, wt):
        r = state.Repo(start)
        assert r.main == repo
        assert r.common == repo / ".git"
        assert r.worktrees() == {wt: "df-abc1"}
    assert state.Repo(repo).head_of(wt) == "feat/df-abc1"
    assert state.Repo(repo).head_of(None) == "main"


def test_a_separate_checkout_inside_the_main_one_is_a_nested_repo(repo):
    nested = repo / "workspaces" / "Private"
    nested.mkdir(parents=True)
    git(nested, "init", "-q")
    r = state.Repo(repo)
    assert r.nested_repo_of(nested / "scripts" / "x.py") == nested
    assert r.nested_repo_of(repo / "tracked.txt") is None


@pytest.mark.parametrize(("text", "prefix", "expected"), [
    ("df-0dql", "df", "df-0dql"),
    ("feat/df-0dql-session-coordination", "df", "df-0dql"),
    ("df-ahua.5-ledger", "df", "df-ahua.5"),
    ("test/df-ahua.23-mutation-wave2", None, "df-ahua.23"),
    ("main", "df", None),
    ("xdf-0dql", "df", None),
])
def test_the_issue_id_comes_from_the_worktree_or_branch_name(text, prefix, expected):
    assert state.bead_id_in(text, prefix) == expected


# --------------------------------------------------------------------------- processes


@NO_PROC
def test_a_process_is_alive_only_while_running_and_with_the_same_start_time():
    me = os.getpid()
    start = state.proc_start(me)
    assert state.pid_alive(me, start) is True
    assert state.pid_alive(me, str(int(start) + 1)) is False  # a recycled PID is a different process
    assert state.pid_alive(dead_pid()) is False
    assert state.pid_alive(None) is None


def test_the_claude_pid_comes_from_the_environment_first():
    assert state.find_claude_pid(env={"CLAUDE_PID": "4242"}) == 4242


@NO_PROC
def test_without_the_environment_it_is_the_nearest_ancestor_with_that_name():
    me = os.getpid()
    comm = Path(f"/proc/{me}/comm").read_text(encoding="utf-8").strip()
    assert state.find_claude_pid(start=me, names=frozenset({comm}), env={}) == me
    assert state.find_claude_pid(start=me, names=frozenset({"no-such-name"}), env={"CLAUDE_PID": "x"}) is None


def test_a_session_on_another_host_is_never_presumed_dead():
    assert state.entry_alive({"host": "elsewhere/linux", "pid": 1}) is True


# --------------------------------------------------------------------------- Edit / Write


def test_writing_in_a_worktree_nobody_holds_makes_it_mine(repo):
    wt = add_worktree(repo, "df-abc1")
    assert run(edit(wt / "a.py"), repo) is None
    entry = state.load_entries(state.Repo(repo))[ME]
    assert str(wt) in entry["worktrees"]
    assert entry["actor"] == "claude-aaaaaaaa"


def test_writing_in_my_own_worktree_is_silent(repo):
    wt = add_worktree(repo, "df-abc1")
    register(repo, ME, worktrees=[wt])
    assert run(edit(wt / "dataforge" / "x.py"), repo) is None


def test_writing_in_a_live_sessions_worktree_warns_and_names_it(repo):
    wt = add_worktree(repo, "df-abc1")
    register(repo, OTHER_SESSION, worktrees=[wt], bead="df-sess-bbbbbbbb")
    out = run(edit(wt / "a.py"), repo)
    text = context(out)
    assert ".worktrees/df-abc1" in text
    assert "claude-bbbbbbbb" in text
    assert "df-sess-bbbbbbbb" in text
    assert "permissionDecision" not in out["hookSpecificOutput"]
    assert out["systemMessage"].startswith("Session guard")


def test_block_mode_denies_the_same_call(repo):
    wt = add_worktree(repo, "df-abc1")
    register(repo, OTHER_SESSION, worktrees=[wt])
    out = run(edit(wt / "a.py"), repo, mode="block")
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "claude-bbbbbbbb" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_a_session_that_started_inside_a_worktree_holds_it(repo):
    wt = add_worktree(repo, "df-abc1")
    register(repo, OTHER_SESSION, cwd=wt)
    assert run(edit(wt / "a.py"), repo) is not None


@NO_PROC
def test_a_dead_sessions_worktree_can_be_taken_over(repo):
    wt = add_worktree(repo, "df-abc1")
    register(repo, OTHER_SESSION, worktrees=[wt], pid=dead_pid())
    assert run(edit(wt / "a.py"), repo) is None
    assert str(wt) in state.load_entries(state.Repo(repo))[ME]["worktrees"]


def test_sibling_subagents_do_not_write_in_each_others_worktree_but_the_parent_may(repo):
    wt = add_worktree(repo, "df-abc1")
    register(repo, ME, agents={"agent-1": {"live": True}, "agent-2": {"live": True}})
    assert run(edit(wt / "a.py", agent="agent-1"), repo) is None  # agent-1 takes it
    assert "subagent agent-1" in context(run(edit(wt / "b.py", agent="agent-2"), repo))
    assert run(edit(wt / "c.py"), repo) is None  # the orchestrating session itself


def test_a_finished_subagents_worktree_is_free_for_its_siblings(repo):
    wt = add_worktree(repo, "df-abc1")
    register(repo, ME, agents={"agent-1": {"live": False}})
    entry = state.load_entries(state.Repo(repo))[ME]
    entry["worktrees"] = {str(wt): {"agent": "agent-1", "at": state.now_iso()}}
    state.write_json(state.entry_path(state.Repo(repo), ME), entry)
    assert run(edit(wt / "a.py", agent="agent-2"), repo) is None


def test_tracked_and_new_unignored_files_of_the_main_checkout_warn(repo):
    for path in (repo / "tracked.txt", repo / "dataforge" / "new.py"):
        assert "main checkout" in context(run(edit(path), repo))


@pytest.mark.parametrize("rel", ["ignored/notes.md", "CLAUDE.md", ".claude/settings.json",
                                 ".beads/x", ".git/info/exclude"])
def test_gitignored_files_and_the_git_dir_of_the_main_checkout_are_local(repo, rel):
    assert run(edit(repo / rel), repo) is None


def test_a_nested_private_checkout_and_paths_outside_the_repo_are_not_guarded(repo, tmp_path):
    nested = repo / "workspaces" / "Private"
    nested.mkdir(parents=True)
    git(nested, "init", "-q")
    assert run(edit(nested / "scripts" / "a.py"), repo) is None
    assert run(edit(tmp_path / "scratch" / "notes.md"), repo) is None


def test_when_git_cannot_tell_whether_a_path_is_ignored_the_guard_allows(repo):
    out = guard.run(edit(repo / "tracked.txt"), repo, "warn", is_ignored=lambda _m, _p: None)
    assert out is None


def test_notebook_edits_are_checked_by_their_path(repo):
    e = edit(repo / "nb.ipynb", tool="NotebookEdit")
    e["tool_input"] = {"notebook_path": str(repo / "nb.ipynb")}
    assert run(e, repo) is not None


# --------------------------------------------------------------------------- Bash


@pytest.mark.parametrize("command", [
    "git pull", "git checkout -b x", "git switch main", "git reset --hard origin/main",
    "git stash", "git -c core.x=y commit -m x", "pytest -q", ".venv/bin/python -m pytest tests",
    "FOO=1 git pull --ff-only", "echo hi && git rebase origin/main", "cd . ; git merge x",
])
def test_working_tree_commands_in_the_main_checkout_warn(repo, command):
    assert "main checkout" in context(run(bash(command, repo), repo))


@pytest.mark.parametrize("command", [
    "git status", "git fetch origin", "git log --oneline", "git diff", "echo 'git pull'",
    "git push origin feat/x", "grep -r pytest .", "DF_GUARD_ALLOW=1 git pull",
    "cat <<'EOF'\ndon't\nEOF",
])
def test_reading_and_explicitly_allowed_commands_in_the_main_checkout_pass(repo, command):
    assert run(bash(command, repo), repo) is None


def test_commands_follow_cd_and_git_dash_c(repo, tmp_path):
    wt = add_worktree(repo, "df-abc1")
    assert run(bash(f"cd {q(wt)} && git pull && python -m pytest", repo), repo) is None
    assert run(bash(f"git -C {q(repo)} checkout main", wt), repo) is not None
    assert run(bash(f"cd {q(tmp_path)} && git pull", repo), repo) is None


def test_a_mutating_command_in_a_live_sessions_worktree_warns(repo):
    wt = add_worktree(repo, "df-abc1")
    register(repo, OTHER_SESSION, worktrees=[wt])
    assert "claude-bbbbbbbb" in context(run(bash("git commit -am x", wt), repo))
    assert run(bash("git status", wt), repo) is None


def test_creating_a_worktree_claims_its_path(repo):
    out = run(bash("git worktree add .worktrees/df-new1 -b feat/df-new1 origin/main", repo), repo)
    assert out is None
    held = state.load_entries(state.Repo(repo))[ME]["worktrees"]
    assert str((repo / ".worktrees" / "df-new1").resolve()) in held


def test_removing_a_live_sessions_worktree_warns(repo):
    wt = add_worktree(repo, "df-abc1")
    register(repo, OTHER_SESSION, worktrees=[wt])
    assert run(bash(f"git worktree remove --force {q(wt)}", repo), repo) is not None
    register(repo, OTHER_SESSION)
    assert run(bash(f"git worktree remove {q(wt)}", repo), repo) is None


@pytest.mark.parametrize(("command", "expected"), [
    ("a && b || c ; d | e & f", [["a"], ["b"], ["c"], ["d"], ["e"], ["f"]]),
    ("git log > out.txt 2>&1", [["git", "log", "2"]]),
    ("(cd x; git pull)", [["cd", "x"], ["git", "pull"]]),
    ("a \\\n b\nc", [["a", "b"], ["c"]]),
])
def test_split_commands(command, expected):
    assert guard.split_commands(command) == expected


@pytest.fixture(scope="module")
def shared_repo(tmp_path_factory):
    return make_repo(tmp_path_factory.mktemp("r"))


SHELL_WORDS = st.sampled_from(["cd", "git", "-C", "pull", "worktree", "add", "remove", "pytest",
                                "python", "-m", "&&", ";", "|", ">", "2>&1", "(", ")", "'", '"',
                                "..", "~", ".worktrees/x", "\x00", "DF_GUARD_ALLOW=1", "\n"])


@settings(max_examples=300, deadline=None)
@given(st.lists(SHELL_WORDS | st.text(max_size=12), max_size=12).map(" ".join))
def test_any_bash_command_gets_an_answer_without_raising(shared_repo, command):
    out = guard.run(bash(command, shared_repo), shared_repo, "warn", is_ignored=lambda _m, _p: False)
    assert out is None or "additionalContext" in out["hookSpecificOutput"]


# --------------------------------------------------------------------------- the hook process


def hook(stdin: str, project: Path, mode: str | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(project)}
    env.pop("DF_GUARD_MODE", None)
    if mode:
        env["DF_GUARD_MODE"] = mode
    return subprocess.run([sys.executable, "-S", str(SCRIPTS / "guard.py")], input=stdin,
                          capture_output=True, text=True, env=env, check=False)


def test_the_hook_prints_one_json_object_and_exits_zero(repo):
    r = hook(json.dumps(bash("git pull", repo)), repo)
    assert r.returncode == 0
    assert "main checkout" in json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]


@pytest.mark.parametrize("stdin", ["", "not json", "[]", '{"tool_name": "Edit", "tool_input": 3}',
                                   '{"tool_name": "Bash", "tool_input": {"command": "\\"unclosed"}}'])
def test_the_hook_fails_open_silently_on_odd_input(repo, stdin):
    r = hook(stdin, repo)
    assert (r.returncode, r.stdout) == (0, "")


def test_the_hook_fails_open_outside_a_git_repo(tmp_path):
    r = hook(json.dumps(bash("git pull", tmp_path)), tmp_path)
    assert (r.returncode, r.stdout) == (0, "")


def test_mode_off_skips_the_guard_and_an_unknown_mode_means_warn(repo):
    assert hook(json.dumps(bash("git pull", repo)), repo, mode="off").stdout == ""
    out = json.loads(hook(json.dumps(bash("git pull", repo)), repo, mode="loud").stdout)
    assert "permissionDecision" not in out["hookSpecificOutput"]


@pytest.mark.skipif(sys.platform == "win32", reason="the wrapper is a bash script; hooks run in WSL/Linux")
def test_the_shell_wrapper_passes_the_answer_through_and_ignores_unknown_commands(repo):
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(repo)}
    env.pop("DF_GUARD_MODE", None)
    r = subprocess.run([str(SCRIPTS / "hook.sh"), "guard"], input=json.dumps(bash("git pull", repo)),
                       capture_output=True, text=True, env=env, check=False)
    assert r.returncode == 0
    assert json.loads(r.stdout)["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    r = subprocess.run([str(SCRIPTS / "hook.sh"), "nonsense"], input="{}", capture_output=True,
                       text=True, env=env, check=False)
    assert (r.returncode, r.stdout) == (0, "")
