"""PreToolUse guard (df-2f3j): keep each session inside its own worktree.

Several Claude Code sessions share this repository. The guard looks at each Edit/Write and Bash
call and reports two kinds of trespass:

- a write to a file of the main checkout that git does not ignore (tracked, or new and not
  ignored), or to a file in a worktree that another live session works in;
- a Bash command that moves or changes a working tree (`git pull`, `checkout`, `switch`, `reset`,
  `merge`, `rebase`, `restore`, `clean`, `stash`, `commit`, `cherry-pick`, `revert`, `am`) or runs
  pytest, in the main checkout or in another live session's worktree; and `git worktree remove` of a
  worktree another live session works in.

Not reported: anything outside the repository (scratchpad, temp dirs, the memory directory, other
repos), gitignored files of the main checkout (docs/, CLAUDE.md, .claude/, .beads/...), a separate
git checkout nested in it (a private workspace with its own rules), the repo's git dir, and the
session's own worktrees. A worktree nobody alive holds becomes the session's on first use, and so
does the path of a `git worktree add`.

DF_GUARD_MODE: `warn` (default) lets the call through and tells Claude why it looks wrong; `block`
denies it; `off` skips the guard. A command carrying `DF_GUARD_ALLOW=1` passes (the owner asked for
it). The guard fails open: any error, missing file or odd input means allow, silently. It never
calls bd, and calls git at most once (`check-ignore`, only for a path in the main checkout).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import state

MODE_ENV = "DF_GUARD_MODE"
MODES = frozenset({"warn", "block", "off"})
ALLOW_RE = re.compile(r"(?:^|[\s;&|(])DF_GUARD_ALLOW=1(?:$|[\s;&|)])")
MUTATING_GIT = frozenset(
    {"pull", "checkout", "switch", "reset", "merge", "rebase", "restore", "clean", "stash", "commit",
     "cherry-pick", "revert", "am"}
)
FILE_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
WRAPPERS = frozenset({"env", "command", "exec", "nohup", "time", "nice", "sudo"})
GIT_OPTS_WITH_VALUE = frozenset({"-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"})
WORKTREE_ADD_OPTS_WITH_VALUE = frozenset({"-b", "-B", "--reason", "--orphan"})
ASSIGNMENT = re.compile(r"^[A-Za-z_]\w*=")
CHECK_IGNORE_TIMEOUT = 3


def git_check_ignore(main: Path, path: Path) -> bool | None:
    """True if git ignores `path` in the main checkout, False if not, None if git could not tell."""
    try:
        r = subprocess.run(["git", "-C", str(main), "check-ignore", "-q", "--", str(path)],
                           capture_output=True, timeout=CHECK_IGNORE_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return {0: True, 1: False}.get(r.returncode)


def split_commands(command: str) -> list[list[str]]:
    """Simple commands of a shell line, as word lists. Operators split; redirections are dropped."""
    text = command.replace("\\\n", " ").replace("\n", " ; ")
    lex = shlex.shlex(text, posix=True, punctuation_chars=";&|()<>")
    lex.whitespace_split = True
    out, cur, skip = [], [], False
    for tok in lex:
        if skip:
            skip = False
        elif "<" in tok or ">" in tok:
            skip = True  # the redirection target (shlex splits `2>&1` into `2`, `>&`, `1`)
        elif tok and set(tok) <= set(";&|()"):
            out.append(cur)
            cur = []
        else:
            cur.append(tok)
    out.append(cur)
    return [c for c in out if c]


def strip_prefix(words: list[str]) -> list[str]:
    """Drop leading VAR=value assignments and wrappers like `env` or `nohup`."""
    i = 0
    while i < len(words) and (ASSIGNMENT.match(words[i]) or words[i] in WRAPPERS
                              or (i and words[i - 1] == "env" and words[i].startswith("-"))):
        i += 1
    return words[i:]


def is_pytest(words: list[str]) -> bool:
    base = os.path.basename(words[0])
    if base in {"pytest", "py.test"}:
        return True
    if base.startswith("python") and "-m" in words:
        i = words.index("-m")
        return i + 1 < len(words) and words[i + 1] == "pytest"
    return False


def parse_git(words: list[str], cwd: Path) -> tuple[Path, str, list[str]]:
    """(directory git acts on, subcommand, its arguments) of a `git ...` word list."""
    where, i = cwd, 1
    while i < len(words) and words[i].startswith("-"):
        opt = words[i]
        if opt == "-C" and i + 1 < len(words):
            where = resolve(words[i + 1], where)
            i += 2
        elif opt.startswith("--work-tree="):
            where = resolve(opt.split("=", 1)[1], where)
            i += 1
        else:
            i += 2 if opt in GIT_OPTS_WITH_VALUE else 1
    sub = words[i] if i < len(words) else ""
    return where, sub, words[i + 1 :]


def first_positional(args: list[str], with_value: frozenset[str]) -> str | None:
    i = 0
    while i < len(args):
        if args[i] == "--":
            return args[i + 1] if i + 1 < len(args) else None
        if args[i].startswith("-"):
            i += 2 if args[i] in with_value else 1
            continue
        return args[i]
    return None


def resolve(path: str, cwd: Path) -> Path:
    """Absolute, normalized and with symlinks resolved, as the worktree roots are."""
    p = Path(os.path.expanduser(path))
    return Path(os.path.normpath(p if p.is_absolute() else cwd / p)).resolve()


class Guard:
    """One hook call's view: the repository, the registry, and this session's entry."""

    def __init__(self, repo: state.Repo, event: dict,
                 is_ignored: Callable[[Path, Path], bool | None] = git_check_ignore):
        self.repo = repo
        self.session = str(event.get("session_id") or "")
        self.agent = event.get("agent_id") or None
        self.cwd = Path(event.get("cwd") or repo.main)
        self.is_ignored = is_ignored
        self.entries = state.load_entries(repo)
        self.entry = self.entries.get(self.session)
        self.dirty = False

    def _mine(self) -> dict:
        if self.entry is None:  # a session that started before the hooks were on
            self.entry = state.new_entry(self.session, self.repo.bead_prefix(), state.find_claude_pid())
            self.entries[self.session] = self.entry
            self.dirty = True
        return self.entry

    def save(self) -> None:
        if self.dirty and self.session:
            state.write_json(state.entry_path(self.repo, self.session), self.entry)

    def _rel(self, path: Path) -> str:
        try:
            return path.relative_to(self.repo.main).as_posix()
        except ValueError:
            return path.as_posix()

    def worktree(self, wt: Path, what: str, *, claim: bool = True) -> str | None:
        """Foreign if another live session (or a running sibling subagent) holds it; else mine."""
        found = state.holders(wt, self.session, self.agent, self.entries)
        if found:
            h = found[0]
            who = f"subagent {h['agent']} of this session" if h["agent"] else f"live session {h['actor']}"
            bead = f" (session bead {h['bead']})" if h.get("bead") else ""
            return (f"{what} is in worktree {self._rel(wt)}, which {who}{bead} is working in. Leave it "
                    "to that session and coordinate through the issue bead.")
        if claim and state.claim_worktree(self._mine(), wt, self.agent):
            self.dirty = True
        return None

    def check_path(self, raw: str) -> str | None:
        path = resolve(raw, self.cwd)
        wt = self.repo.worktree_of(path)
        if wt is not None:
            return self.worktree(wt, self._rel(path))
        if not state.is_within(path, self.repo.main) or state.is_within(path, self.repo.common):
            return None
        if self.repo.nested_repo_of(path) is not None:
            return None
        if self.is_ignored(self.repo.main, path) is not False:
            return None
        return (f"{self._rel(path)} is a file of the main checkout, which every session shares (only "
                "gitignored files there are local). Make the change in your own worktree: "
                "git worktree add .worktrees/<issue> -b <branch> origin/main.")

    def check_dir(self, where: Path, action: str) -> str | None:
        wt = self.repo.worktree_of(where)
        if wt is not None:
            return self.worktree(wt, f"`{action}`")
        if not state.is_within(where, self.repo.main) or self.repo.nested_repo_of(where):
            return None
        return (f"`{action}` would run in the main checkout, which every session shares. Run it in "
                "your own worktree. If the owner asked for it here, prefix the command with "
                "DF_GUARD_ALLOW=1.")

    def check_git(self, words: list[str], cwd: Path) -> str | None:
        where, sub, args = parse_git(words, cwd)
        if sub == "worktree" and args:
            target = first_positional(args[1:], WORKTREE_ADD_OPTS_WITH_VALUE)
            if target is None:
                return None
            path = resolve(target, where)
            if args[0] == "add":
                if state.claim_worktree(self._mine(), path, self.agent):
                    self.dirty = True
                return None
            if args[0] in {"remove", "move"} and self.repo.worktree_of(path) == path:
                return self.worktree(path, f"`git worktree {args[0]}`", claim=False)
            return None
        if sub in MUTATING_GIT:
            return self.check_dir(where, f"git {sub}")
        return None

    def check_bash(self, command: str) -> list[str]:
        if ALLOW_RE.search(command):
            return []
        try:
            commands = split_commands(command)
        except ValueError:
            return []  # unbalanced quotes, a heredoc...: cannot tell, so allow
        cwd, found = self.cwd, []
        for raw in commands:
            words = strip_prefix(raw)
            try:
                cwd = self._one_command(words, cwd, found)
            except (ValueError, OSError):
                continue  # a path no filesystem can hold (NUL byte...): nothing to guard there
        return [f for f in found if f]

    def _one_command(self, words: list[str], cwd: Path, found: list) -> Path:
        """Check one simple command; returns the directory the next one runs in."""
        if not words:
            return cwd
        if words[0] in {"cd", "pushd"}:
            return resolve(words[1] if len(words) > 1 else "~", cwd)
        if os.path.basename(words[0]) == "git":
            found.append(self.check_git(words, cwd))
        elif is_pytest(words):
            found.append(self.check_dir(cwd, "pytest"))
        return cwd

    def evaluate(self, event: dict) -> list[str]:
        tool = event.get("tool_name")
        args = event.get("tool_input") or {}
        if tool in FILE_TOOLS:
            raw = args.get("file_path") or args.get("notebook_path")
            found = [self.check_path(raw)] if raw else []
        elif tool == "Bash":
            found = self.check_bash(str(args.get("command") or ""))
        else:
            found = []
        return [f for f in found if f]


def render(findings: list[str], mode: str) -> dict:
    text = "Session guard (df-2f3j): " + " ".join(findings)
    if mode == "block":
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": text + " Blocked: DF_GUARD_MODE=block."}}
    return {"systemMessage": text,
            "hookSpecificOutput": {"hookEventName": "PreToolUse",
                                   "additionalContext": text + " (Warn mode: the call went ahead.)"}}


def run(event: dict, project: Path, mode: str,
        is_ignored: Callable[[Path, Path], bool | None] = git_check_ignore) -> dict | None:
    """The hook's JSON answer for one event, or None to stay silent."""
    if mode == "off" or event.get("hook_event_name", "PreToolUse") != "PreToolUse":
        return None
    guard = Guard(state.Repo(project), event, is_ignored)
    findings = guard.evaluate(event)
    guard.save()
    return render(findings, mode) if findings else None


def main() -> int:
    try:
        event = json.loads(sys.stdin.read() or "{}")
        mode = os.environ.get(MODE_ENV, "warn").strip().lower()
        project = Path(os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or ".")
        out = run(event, project, mode if mode in MODES else "warn")
        if out:
            sys.stdout.write(json.dumps(out) + "\n")
    except Exception:  # noqa: BLE001 -- fail open: a guard bug must never stall a session
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
