"""scripts/sessions/lifecycle.py and board.py: session identity, the session bead, the reaper and the
derived board (df-oahj, df-abrb). Temp git repos; the bd tests use a throwaway `bd init` database
in tmp_path and skip where bd is not installed (CI)."""

import json
import os
import shlex
import subprocess
import sys

import pytest

from tests.sessions_repo import (
    HAS_BD, ME, OTHER_SESSION, add_worktree, bd_init, bd_json, bd_run, dead_pid, git, make_repo,
    register, write,
)

import board  # on sys.path through tests.sessions_repo
import lifecycle
import state

NO_PROC = pytest.mark.skipif(sys.platform == "win32",
                             reason="liveness reads /proc; on Windows no session is ever presumed dead")
NEEDS_BD = pytest.mark.skipif(not HAS_BD, reason="needs the bd CLI (not installed in CI)")


@pytest.fixture
def repo(tmp_path):
    return make_repo(tmp_path)


def start_event(cwd, session=ME, source="startup"):
    return {"hook_event_name": "SessionStart", "session_id": session, "cwd": str(cwd), "source": source,
            "transcript_path": f"/transcripts/{session}.jsonl"}


def start(repo, event, tmp_path, env=None):
    env_file = tmp_path / "claude-env.sh"
    full = {"CLAUDE_ENV_FILE": str(env_file), **(env or {})}
    text = lifecycle.on_start(state.Repo(repo), event, full, background=False, bd_exe=None)
    return text, env_file


def exported(env_file):
    """What a Bash command would see after sourcing the env file."""
    out = subprocess.run(["bash", "-c", f"source {shlex.quote(str(env_file))}; env"],
                         capture_output=True, text=True, check=True).stdout
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)


# --------------------------------------------------------------------------- SessionStart


@pytest.mark.skipif(sys.platform == "win32", reason="sources the env file with bash, as Claude Code does")
def test_start_registers_the_session_and_exports_its_identity(repo, tmp_path):
    (state.Repo(repo).registry).mkdir(parents=True)
    (state.Repo(repo).registry / state.PREFIX_CACHE).write_text("df\n", encoding="utf-8")
    text, env_file = start(repo, start_event(repo), tmp_path,
                           env={"CLAUDE_CODE_MESSAGING_SOCKET": "/run/x/123.sock"})
    assert text == ""  # nobody else is live: no context
    entry = state.load_entries(state.Repo(repo))[ME]
    assert entry["actor"] == "claude-aaaaaaaa"
    assert entry["bead"] == "df-sess-aaaaaaaa"
    assert entry["transcript_path"] == f"/transcripts/{ME}.jsonl"
    assert entry["messaging_socket"] == "/run/x/123.sock"
    assert entry["host"] == state.host_key()
    env = exported(env_file)
    assert env["BEADS_ACTOR"] == "claude-aaaaaaaa"
    assert env["DF_SESSION_BEAD"] == "df-sess-aaaaaaaa"
    assert env["CLAUDE_SESSION_ID"] == ME
    assert all(line.startswith("export ") for line in env_file.read_text(encoding="utf-8").splitlines())


def test_start_inside_a_worktree_claims_it_and_resume_keeps_the_start_time(repo, tmp_path):
    wt = add_worktree(repo, "df-abc1")
    start(repo, start_event(wt), tmp_path)
    first = state.load_entries(state.Repo(repo))[ME]
    assert str(wt) in first["worktrees"]
    start(repo, start_event(wt, source="resume"), tmp_path)
    again = state.load_entries(state.Repo(repo))[ME]
    assert again["started_at"] == first["started_at"]
    assert again["source"] == "resume"


def test_start_tells_about_other_live_sessions_in_a_few_lines(repo, tmp_path):
    wt = add_worktree(repo, "df-abc1")
    write(wt / "dataforge" / "engine.py", "changed\n")
    register(repo, OTHER_SESSION, worktrees=[wt])
    text, _ = start(repo, start_event(repo), tmp_path)
    lines = text.splitlines()
    assert len(lines) == 3
    assert ".worktrees/df-abc1: df-abc1, branch feat/df-abc1, session claude-bbbbbbbb" in lines[1]
    assert "dataforge/" in lines[1]
    assert "--owner" in lines[2]


@NO_PROC
def test_start_says_nothing_about_dead_sessions(repo, tmp_path):
    wt = add_worktree(repo, "df-abc1")
    register(repo, OTHER_SESSION, worktrees=[wt], pid=dead_pid())
    assert start(repo, start_event(repo), tmp_path)[0] == ""


# --------------------------------------------------------------------------- other hooks


def test_end_hands_the_entry_to_the_closer_and_unregisters(repo, tmp_path):
    start(repo, start_event(repo), tmp_path)
    r = state.Repo(repo)
    lifecycle.on_end(r, {"session_id": ME, "reason": "prompt_input_exit"}, background=False)
    assert ME not in state.load_entries(r)
    ending = state.read_json(r.registry / state.ENDING_DIR / f"{ME}.json")
    assert ending["end_reason"] == "prompt_input_exit"
    assert ending["ended_at"]


def test_subagent_start_and_stop_mark_it_running_and_done(repo):
    register(repo, ME)
    r = state.Repo(repo)
    lifecycle.on_agent(r, {"session_id": ME, "agent_id": "a1", "agent_type": "Explore"}, live=True)
    assert state.agent_live(state.load_entries(r)[ME], "a1")
    lifecycle.on_agent(r, {"session_id": ME, "agent_id": "a1"}, live=False)
    assert not state.agent_live(state.load_entries(r)[ME], "a1")


def test_moving_into_a_free_worktree_claims_it_but_not_a_held_one(repo):
    free, held = add_worktree(repo, "df-abc1"), add_worktree(repo, "df-abc2")
    register(repo, ME)
    register(repo, OTHER_SESSION, worktrees=[held])
    r = state.Repo(repo)
    for wt in (free, held):
        lifecycle.on_cwd(r, {"session_id": ME, "old_cwd": str(repo), "new_cwd": str(wt)})
    mine = state.load_entries(r)[ME]["worktrees"]
    assert str(free) in mine
    assert str(held) not in mine


@pytest.mark.parametrize("cmd", ["start", "end", "cwd", "agent-start"])
def test_hooks_fail_open_on_odd_input(tmp_path, cmd):
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(tmp_path)}
    r = subprocess.run([sys.executable, "-S", lifecycle.__file__, cmd], input="not json",
                       capture_output=True, text=True, env=env, check=False)
    assert (r.returncode, r.stdout) == (0, "")


# --------------------------------------------------------------------------- board


def test_the_board_works_with_no_session_registered(repo):
    wt = add_worktree(repo, "df-abc1")
    write(wt / "new.py", "x\n")
    git(wt, "add", "new.py")
    git(wt, "commit", "-q", "-m", "work")
    write(wt / "wip.py", "y\n")
    b = board.build(repo, use_bd=False, use_pr=False)
    main_row, row = b["worktrees"]
    assert main_row["is_main"]
    assert main_row["owner"]["state"] == "shared"
    assert row["bead"] == "df-abc1"
    assert row["branch"] == "feat/df-abc1"
    assert row["committed"] == ["new.py"]
    assert row["uncommitted"] == ["wip.py"]
    assert row["history"] == {"ok": True, "ahead": 1}
    assert row["owner"]["state"] == "unknown"
    assert "df-abc1" in board.text(b)


def test_the_board_names_the_live_owner_with_its_pid(repo):
    wt = add_worktree(repo, "df-abc1")
    register(repo, OTHER_SESSION, worktrees=[wt], bead="df-sess-bbbbbbbb")
    row = board.build(repo, use_bd=False, use_pr=False)["worktrees"][1]
    owner = row["owner"]
    assert owner["state"] == "live"
    assert owner["sessions"][0]["pid"] == os.getpid()
    assert owner["sessions"][0]["actor"] == "claude-bbbbbbbb"
    assert "pid" in board.owner_line(owner)


def test_a_branch_from_rewritten_history_is_flagged(repo, tmp_path):
    # Unrelated history: what a worktree cut before a history rewrite looks like afterwards.
    wt = add_worktree(repo, "df-old1")
    git(wt, "checkout", "-q", "--orphan", "feat/old")
    git(wt, "commit", "-q", "-m", "old root", "--allow-empty")
    assert board.history_check(wt, "origin/main")["ok"] is False
    # Old PR merges the base does not carry.
    wt2 = add_worktree(repo, "df-old2")
    git(wt2, "checkout", "-q", "-b", "side")
    git(wt2, "commit", "-q", "--allow-empty", "-m", "side")
    git(wt2, "checkout", "-q", "feat/df-old2")
    git(wt2, "merge", "-q", "--no-ff", "side", "-m", "Merge pull request #1 from x/side")
    check = board.history_check(wt2, "origin/main")
    assert check["ok"] is False
    assert "1 PR merge" in check["why"]


def test_owner_lookup_answers_for_one_path_only(repo, capsys):
    wt = add_worktree(repo, "df-abc1")
    add_worktree(repo, "df-abc2")
    register(repo, OTHER_SESSION, worktrees=[wt])
    assert board.main(["--project", str(repo), "--owner", str(wt / "some" / "file.py"), "--no-bd", "--no-pr"]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1
    assert "live session claude-bbbbbbbb" in out[0]


def test_the_json_form_carries_the_same_owner(repo, capsys):
    add_worktree(repo, "df-abc1")
    board.main(["--project", str(repo), "--json", "--no-bd", "--no-pr"])
    data = json.loads(capsys.readouterr().out)
    assert [w["owner"]["state"] for w in data["worktrees"]] == ["shared", "unknown"]


# --------------------------------------------------------------------------- with a throwaway bd


@pytest.fixture
def bdrepo(repo, monkeypatch):
    # Nothing may point bd at the real database, or attribute writes to a real session.
    for var in ("BEADS_DIR", "BEADS_DB", "BEADS_ACTOR", "CLAUDE_SESSION_ID", "DF_SESSION_BEAD"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CLAUDE_PID", str(os.getpid()))  # the "Claude process" is this test, alive
    bd_init(repo)
    return repo


@NEEDS_BD
def test_the_session_bead_is_created_live_out_of_ready_and_reopened_on_resume(bdrepo, tmp_path):
    lifecycle.on_start(state.Repo(bdrepo), start_event(bdrepo), {"CLAUDE_ENV_FILE": str(tmp_path / "e")},
                       background=False)
    lifecycle.bg_start(state.Repo(bdrepo), ME)
    bead = bd_json(bdrepo, "show", "df-sess-aaaaaaaa")[0]
    assert (bead["issue_type"], bead["status"], bead["created_by"]) == ("session", "live", "claude-aaaaaaaa")
    assert bead["metadata"]["transcript_path"] == f"/transcripts/{ME}.jsonl"
    assert bead["metadata"]["pid"] is not None
    assert "df-sess-aaaaaaaa" not in [i["id"] for i in bd_json(bdrepo, "ready")]
    bd_run(bdrepo, "close", "df-sess-aaaaaaaa", "-r", "ended: other")
    lifecycle.on_start(state.Repo(bdrepo), start_event(bdrepo, source="resume"),
                       {"CLAUDE_ENV_FILE": str(tmp_path / "e")}, background=False)
    lifecycle.bg_start(state.Repo(bdrepo), ME)
    again = bd_json(bdrepo, "show", "df-sess-aaaaaaaa")[0]
    assert again["status"] == "live"
    assert again["title"] == bead["title"]
    assert again["metadata"]["source"] == "resume"


@NEEDS_BD
def test_end_writes_a_structured_record_releases_claims_and_closes(bdrepo, tmp_path):
    wt = add_worktree(bdrepo, "df-abc1")
    r = state.Repo(bdrepo)
    lifecycle.on_start(r, start_event(wt), {"CLAUDE_ENV_FILE": str(tmp_path / "e")}, background=False)
    lifecycle.bg_start(r, ME)
    actor = "claude-aaaaaaaa"
    made = bd_run(bdrepo, "create", "found while working", "--silent", actor=actor)
    held = bd_run(bdrepo, "create", "still working", "--silent")
    bd_run(bdrepo, "update", held, "--claim", actor=actor)
    write(wt / "x.py", "x\n")
    git(wt, "add", "x.py")
    git(wt, "commit", "-q", "-m", "work")
    lifecycle.on_end(r, {"session_id": ME, "reason": "prompt_input_exit"}, background=False)
    lifecycle.bg_end(r, ME)
    bead = bd_json(bdrepo, "show", "df-sess-aaaaaaaa")[0]
    assert bead["status"] == "closed"
    end = bead["metadata"]["end"]
    assert bead["metadata"]["end_reason"] == "prompt_input_exit"
    assert end["beads_created"] == [made]
    assert end["claims_released"] == [held]
    assert end["worktrees"][0]["branch"] == "feat/df-abc1"
    assert len(end["worktrees"][0]["commits"]) == 1
    issue = bd_json(bdrepo, "show", held)[0]
    assert (issue["status"], issue.get("assignee")) == ("open", None)
    assert actor in issue["notes"]
    assert not (r.registry / state.ENDING_DIR / f"{ME}.json").exists()
    owner = board.build(bdrepo, use_pr=False)["worktrees"][1]["owner"]
    assert owner["state"] == "past"
    assert owner["last"]["actor"] == actor


@NEEDS_BD
@NO_PROC
def test_a_session_whose_process_died_is_reaped_and_its_claims_released(bdrepo, tmp_path):
    r = state.Repo(bdrepo)
    lifecycle.on_start(r, start_event(bdrepo, session=OTHER_SESSION), {"CLAUDE_ENV_FILE": str(tmp_path / "e")},
                       background=False)
    entry = state.load_entries(r)[OTHER_SESSION]
    entry.update({"pid": dead_pid(), "pid_start": None})
    state.write_json(state.entry_path(r, OTHER_SESSION), entry)
    lifecycle.bg_start(r, OTHER_SESSION)  # creates its bead with the dead PID
    claimed = bd_run(bdrepo, "create", "abandoned work", "--silent")
    bd_run(bdrepo, "update", claimed, "--claim", actor="claude-bbbbbbbb")
    lifecycle.on_start(r, start_event(bdrepo), {"CLAUDE_ENV_FILE": str(tmp_path / "e2")}, background=False)
    lifecycle.bg_start(r, ME)  # I start: the reaper runs
    dead = bd_json(bdrepo, "show", "df-sess-bbbbbbbb")[0]
    assert dead["status"] == "closed"
    assert dead["metadata"]["end_reason"] == "abandoned"
    assert dead["metadata"]["reaped_by"] == "claude-aaaaaaaa"
    issue = bd_json(bdrepo, "show", claimed)[0]
    assert (issue["status"], issue.get("assignee")) == ("open", None)
    assert OTHER_SESSION not in state.load_entries(r)
    assert bd_json(bdrepo, "show", "df-sess-aaaaaaaa")[0]["status"] == "live"
