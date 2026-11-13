"""Who works where (df-oahj): the board of worktrees, derived from git, bd and the session registry.

    python3 scripts/sessions/board.py                 compact text, one block per worktree
    python3 scripts/sessions/board.py --json          the same as JSON
    python3 scripts/sessions/board.py --owner PATH    only: whose worktree holds PATH
    options: --no-bd (skip issue and past-owner lookups), --no-pr (skip gh), --base REF

Nothing here is written by hand. Per worktree: branch, files changed against the base (committed and
uncommitted), last commit time, the issue its name carries and that issue's state, the open PR, and
the owner: the live session attached (registry, PID check), else the last session bead that worked
there and when it ended, else "unknown". A worktree whose HEAD shares no history with the base, or
carries PR merges the base does not have, is flagged: its branch was cut from rewritten history and
must not be pushed as is. Works with no session registered.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import subprocess
import sys
from pathlib import Path

import state
from lifecycle import Bd, git

DEFAULT_BASE = "origin/main"
GH_TIMEOUT = 15
SHOW_FILES = 6


def worktree_list(repo: state.Repo) -> list[dict]:
    """`git worktree list --porcelain`: the main checkout first, then the linked worktrees."""
    out = git(repo.main, "worktree", "list", "--porcelain") or ""
    rows, cur = [], {}
    for line in [*out.splitlines(), ""]:
        if not line:
            if cur:
                rows.append(cur)
            cur = {}
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            cur["path"] = str(Path(value).resolve())
        elif key == "branch":
            cur["branch"] = value.removeprefix("refs/heads/")
        elif key == "HEAD":
            cur["head"] = value
        elif key in {"detached", "locked", "prunable", "bare"}:
            cur[key] = value or True
    return rows


def history_check(path: Path, base: str) -> dict:
    """Is HEAD built on the base's history? Tonight's case (df-1mdh): a branch from before a
    history rewrite has no merge base with origin/main and carries the old PR merges."""
    if git(path, "merge-base", "HEAD", base) is None:
        return {"ok": False, "why": f"no common history with {base}"}
    subjects = (git(path, "log", "--format=%s", f"{base}..HEAD") or "").splitlines()
    pr_merges = sum(1 for s in subjects if s.startswith("Merge pull request"))
    if pr_merges:
        return {"ok": False, "why": f"carries {pr_merges} PR merge(s) that {base} does not have"}
    return {"ok": True, "ahead": len(subjects)}


def inspect(path: Path, base: str) -> dict:
    """Git facts of one worktree. Four git calls, run in parallel by the caller across worktrees."""
    uncommitted = state_files(path)
    committed = (git(path, "diff", "--name-only", f"{base}...HEAD") or "").split()
    last = (git(path, "log", "-1", "--format=%cI") or "").strip() or None
    return {"uncommitted": uncommitted, "committed": committed, "last_commit": last,
            "history": history_check(path, base)}


def state_files(path: Path) -> list[str]:
    out = git(path, "status", "--porcelain=v1", "-z", "--untracked-files=normal")
    return [rec[3:] for rec in (out or "").split("\0") if len(rec) > 3]  # noqa: PLR2004 -- "XY " prefix


def open_prs(repo: state.Repo) -> dict[str, dict]:
    try:
        r = subprocess.run(["gh", "pr", "list", "--state", "open", "--limit", "100", "--json",
                            "number,url,headRefName,isDraft"], cwd=repo.main, capture_output=True,
                           text=True, encoding="utf-8", timeout=GH_TIMEOUT, check=False)
        rows = json.loads(r.stdout) if r.returncode == 0 else []
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}
    return {p["headRefName"]: {"number": p["number"], "url": p["url"], "draft": p.get("isDraft")}
            for p in rows}


def issues(bd: Bd, ids: list[str]) -> dict[str, dict]:
    if not ids:
        return {}
    rows = bd.json("show", *ids) or []
    return {i["id"]: {"title": i.get("title"), "status": i.get("status"), "assignee": i.get("assignee")}
            for i in rows if isinstance(i, dict)}


def past_sessions(bd: Bd) -> list[dict]:
    """Session beads (all states), newest first, reduced to what the owner answer needs."""
    rows = bd.json("list", "-t", "session", "--all", "-n", "0") or []
    out = []
    for b in rows:
        m = b.get("metadata") or {}
        wts = m.get("worktrees") or [w.get("path") for w in (m.get("end") or {}).get("worktrees", [])]
        out.append({"bead": b["id"], "status": b.get("status"), "session_id": m.get("session_id"),
                    "actor": m.get("actor"), "pid": m.get("pid"), "host": m.get("host"),
                    "started_at": m.get("started_at"), "ended_at": m.get("ended_at"),
                    "end_reason": m.get("end_reason"), "worktrees": [w for w in wts if w]})
    return sorted(out, key=lambda s: s.get("ended_at") or s.get("started_at") or "", reverse=True)


def live_sessions(worktree: Path, entries: dict[str, dict]) -> list[dict]:
    found = []
    for e in entries.values():
        held = e.get("worktrees", {}).get(str(worktree))
        inside = e.get("cwd") and state.is_within(Path(e["cwd"]), worktree)
        if (held or inside) and state.entry_alive(e):
            agent = (held or {}).get("agent") or None
            found.append({"session_id": e["session_id"], "actor": e.get("actor"), "bead": e.get("bead"),
                          "pid": e.get("pid"), "host": e.get("host"), "since": (held or {}).get("at"),
                          "agent": agent, "session_title": e.get("session_title"),
                          "messaging_socket": e.get("messaging_socket"),
                          "transcript_path": e.get("transcript_path")})
    return found


def owner(worktree: Path, is_main: bool, entries: dict[str, dict], past: list[dict] | None) -> dict:
    live = live_sessions(worktree, entries)
    if live:
        return {"state": "live", "sessions": live}
    if is_main:
        return {"state": "shared", "note": "the main checkout belongs to no session"}
    last = next((s for s in past or [] if str(worktree) in s["worktrees"] and s["status"] == "closed"), None)
    if last is None:
        last = next((s for s in past or [] if str(worktree) in s["worktrees"]), None)
    if last:
        return {"state": "past", "last": last}
    return {"state": "unknown", "note": "no session registered here" + ("" if past is not None else " (bd not read)")}


def build(project: Path, *, base: str = DEFAULT_BASE, use_bd: bool = True, use_pr: bool = True,
          only: Path | None = None) -> dict:
    repo = state.Repo(project)
    entries = state.load_entries(repo)
    prefix = repo.bead_prefix()
    rows = worktree_list(repo)
    for r in rows:
        r["is_main"] = Path(r["path"]) == repo.main
    if only is not None:
        target = repo.worktree_of(only) or repo.main
        rows = [r for r in rows if Path(r["path"]) == target]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        facts = list(pool.map(lambda r: inspect(Path(r["path"]), base), rows)) if only is None else [{}] * len(rows)
        prs = pool.submit(open_prs, repo) if use_pr and only is None else None
        bd = Bd(repo.main)
        ids = sorted({b for r in rows if (b := bead_of(r, prefix))})
        found_issues = pool.submit(issues, bd, ids) if use_bd and only is None else None
        past = pool.submit(past_sessions, bd) if use_bd else None
        prs, found_issues, past = (f.result() if f else None for f in (prs, found_issues, past))
    out = []
    for r, f in zip(rows, facts, strict=True):
        path, bead = Path(r["path"]), bead_of(r, prefix)
        out.append({**r, **f, "name": path.name, "bead": bead,
                    "issue": (found_issues or {}).get(bead) if bead else None,
                    "pr": (prs or {}).get(r.get("branch") or ""),
                    "owner": owner(path, r["is_main"], entries, past)})
    return {"generated_at": state.now_iso(), "main": str(repo.main), "base": base,
            "registered_sessions": len(entries), "worktrees": out}


def bead_of(row: dict, prefix: str | None) -> str | None:
    if row.get("is_main"):
        return None
    return state.bead_id_in(Path(row["path"]).name, prefix) or state.bead_id_in(row.get("branch") or "", prefix)


def owner_line(o: dict) -> str:
    if o["state"] == "live":
        parts = []
        for s in o["sessions"]:
            agent = f" subagent {s['agent']}" if s.get("agent") else ""
            reach = f", socket {s['messaging_socket']}" if s.get("messaging_socket") else ""
            title = f" \"{s['session_title']}\"" if s.get("session_title") else ""
            parts.append(f"live session {s['actor']}{title}{agent} (pid {s['pid']}, bead {s.get('bead')}{reach})")
        return "; ".join(parts)
    if o["state"] == "past":
        s = o["last"]
        when = f"ended {s['ended_at']} ({s.get('end_reason')})" if s.get("ended_at") else f"bead {s['status']}"
        return f"last: session {s['actor']} (bead {s['bead']}), {when}"
    return f"{o['state']}: {o.get('note', '')}"


def text(board: dict) -> str:
    lines = []
    for w in board["worktrees"]:
        name = "main checkout" if w["is_main"] else w["name"]
        issue = w.get("issue") or {}
        head = f"{name}  [{w.get('branch') or 'detached'}]"
        if w.get("bead"):
            head += f"  {w['bead']}" + (f" {issue.get('status')}/{issue.get('assignee') or '-'}" if issue else "")
        if w.get("pr"):
            head += f"  PR #{w['pr']['number']}"
        lines.append(head)
        lines.append(f"  owner: {owner_line(w['owner'])}")
        hist = w.get("history") or {}
        if hist and not hist.get("ok"):
            lines.append(f"  WARNING: {hist['why']}; do not push this branch as is")
        for kind in ("uncommitted", "committed"):
            files = w.get(kind) or []
            if files:
                more = f" +{len(files) - SHOW_FILES}" if len(files) > SHOW_FILES else ""
                lines.append(f"  {kind}: {', '.join(files[:SHOW_FILES])}{more}")
        if w.get("last_commit"):
            lines.append(f"  last commit: {w['last_commit']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--owner", metavar="PATH", help="only answer: whose worktree holds PATH")
    ap.add_argument("--no-bd", action="store_true")
    ap.add_argument("--no-pr", action="store_true")
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--project", default=".", help="any path inside the repository")
    a = ap.parse_args(argv)
    only = Path(a.owner).resolve() if a.owner else None
    board = build(Path(a.project), base=a.base, use_bd=not a.no_bd, use_pr=not a.no_pr, only=only)
    if a.json:
        sys.stdout.write(json.dumps(board, indent=1) + "\n")
    elif only is not None:
        for w in board["worktrees"]:
            name = "main checkout" if w["is_main"] else w["path"]
            sys.stdout.write(f"{name}: {owner_line(w['owner'])}\n")
    else:
        sys.stdout.write(text(board) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
