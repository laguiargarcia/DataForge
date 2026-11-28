"""Post one comment on the pull request with each CI job's result, edited on every run.

Run by the `report` job in .github/workflows/ci.yml. Each job saves its output under report/
as <job>.txt or <job>-<part>.txt; this script turns those files and every job's result into a
Markdown comment tagged with MARKER, and edits that comment instead of adding a new one.

The drawing comment (df-8x0m.23.38) is a second one, tagged with DRAWING_MARKER: the output of
scripts/arquitetura/diff.py, which the job's step `desenho` writes to report/desenho.md. It is
created, edited, and deleted when the PR no longer changes the drawing (an empty file); when that
step did not succeed (DESENHO, its outcome) the comment is left as it was. A PR from a fork gets a
read-only token: both texts go to the job summary only. The job summary always has the drawing
whole, since a comment over MAX_BODY is cut.

Environment: RESULTS (toJSON(needs)), PR, RUN_URL, SHA, GITHUB_REPOSITORY, GH_TOKEN, DESENHO,
HEAD_REPO (the PR head's owner/name), GITHUB_STEP_SUMMARY.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

MARKER = "<!-- ci-report -->"
DRAWING_MARKER = "<!-- ci-drawing -->"
REPORT_DIR = Path("report")
DRAWING_FILE = "desenho.md"
# GitHub refuses a comment body over 65536 characters.
MAX_BODY = 65000
PR_NUMBER = re.compile(r"[1-9]\d*")
REPO_NAME = re.compile(r"[\w.-]+/[\w.-]+")
ICONS = {"success": "✅", "failure": "❌", "cancelled": "⚠️", "skipped": "⏭️"}
# likec4 validate's outcome (job arquitetura): a timing line follows it, and the summary starts at
# its mark, without the log's timestamp and logger.
VALIDATE = re.compile(r"[✓✗] (?:Valid|Invalid) \(")
# The line that states an outcome: the quality gate, the test loop gate, pytest's summary, or VALIDATE.
STATUS = re.compile(rf"quality gate:|testloop: |\b\d+ (passed|failed|errors?)\b|{VALIDATE.pattern}")
TAIL_LINES = 60


def outputs(job: str, jobs: tuple[str, ...] = ()) -> list[Path]:
    """<job>.txt and <job>-<part>.txt, minus the files of another job named '<job>-...': job
    `tests` must not take `tests-windows.txt` as one of its parts."""
    others = [j for j in jobs if j.startswith(job + "-")]
    parts = [p for p in sorted(REPORT_DIR.glob(f"{job}-*.txt"))
             if not any(p.stem == o or p.stem.startswith(o + "-") for o in others)]
    return sorted(REPORT_DIR.glob(f"{job}.txt")) + parts


def summary(text: str) -> str:
    lines = [line.strip().strip("=").strip() for line in text.splitlines() if line.strip()]
    status = [line for line in lines if STATUS.search(line)]
    line = (status or lines or ["(no output)"])[-1]
    outcome = VALIDATE.search(line)
    return line[outcome.start() if outcome else 0:].replace("|", "\\|")


def part_name(job: str, path: Path) -> str:
    return path.stem.removeprefix(job).lstrip("-") or job


def build(results: dict, run_url: str, sha: str) -> str:
    rows, details = [], []
    for job, info in results.items():
        result = info.get("result", "unknown")
        files = outputs(job, tuple(results))
        texts = {part_name(job, f): f.read_text(encoding="utf-8", errors="replace") for f in files}
        line = "<br>".join(f"**{name}**: {summary(t)}" if len(texts) > 1 else summary(t) for name, t in texts.items())
        rows.append(f"| `{job}` | {ICONS.get(result, '❔')} {result} | {line} |")
        for name, text in texts.items():
            tail = "\n".join(text.rstrip().splitlines()[-TAIL_LINES:])
            details.append(f"<details><summary><code>{name}</code> output</summary>\n\n```text\n{tail}\n```\n</details>")
    bad = [job for job, info in results.items() if info.get("result") not in ("success", "skipped")]
    headline = "✅ All CI jobs passed" if not bad else f"❌ {len(bad)} CI job(s) did not pass: " + ", ".join(f"`{j}`" for j in bad)
    return "\n".join([
        MARKER,
        f"### {headline}",
        "",
        "| Job | Result | Summary |",
        "| --- | --- | --- |",
        *rows,
        "",
        *details,
        "",
        f"[Full run]({run_url}) · commit `{sha}`",
    ])


def drawing(results: dict) -> str | None:
    """The drawing comment's body, or None when the PR does not change the drawing."""
    path = REPORT_DIR / DRAWING_FILE
    text = path.read_text(encoding="utf-8").strip() if path.exists() else ""
    if not text:
        return None
    stale = results.get("arquitetura", {}).get("result") not in (None, "success")
    warning = ("> ⚠️ Job `arquitetura` did not pass: `arquitetura/modelo.json` may not be the export of the .c4.\n\n"
               if stale else "")
    return f"{DRAWING_MARKER}\n{warning}{text}\n"


def cut(body: str) -> str:
    """`body` under GitHub's limit; the job summary has it whole."""
    return body if len(body) <= MAX_BODY else body[:MAX_BODY] + "\n\n… (cut: the job summary has it whole)\n"


def gh(*args: str, payload: str | None = None) -> str:
    return subprocess.run(["gh", *args], input=payload, capture_output=True, check=True, encoding="utf-8").stdout


def sync(repo: str, pr: str, comments: list[dict], marker: str, body: str | None) -> None:
    """Make the bot's comment that starts with `marker` hold `body`: edit the last one, or post it; None
    deletes them. Starts with, not holds: the CI report quotes job output, which may hold the other marker."""
    ours = [int(c["id"]) for c in comments if (c.get("body") or "").startswith(marker) and c["user"]["type"] == "Bot"]
    if body is None:
        for comment in ours:
            gh("api", "-X", "DELETE", f"repos/{repo}/issues/comments/{comment}")
        return
    payload = json.dumps({"body": body})
    if ours:
        gh("api", "-X", "PATCH", f"repos/{repo}/issues/comments/{ours[-1]}", "--input", "-", payload=payload)
    else:
        gh("api", "-X", "POST", f"repos/{repo}/issues/{pr}/comments", "--input", "-", payload=payload)


def _checked(pattern: re.Pattern, name: str) -> str:
    value = os.environ[name]
    if not pattern.fullmatch(value):
        raise SystemExit(f"{name} is not a {pattern.pattern!r}: {value!r}")
    return value


def main() -> None:
    repo, pr = _checked(REPO_NAME, "GITHUB_REPOSITORY"), _checked(PR_NUMBER, "PR")
    results = json.loads(os.environ["RESULTS"])
    body = build(results, os.environ["RUN_URL"], os.environ["SHA"][:7])
    drawn = os.environ.get("DESENHO") == "success"
    drawing_body = drawing(results) if drawn else None
    print(body)
    if drawing_body:
        print(drawing_body)
    fork = os.environ.get("HEAD_REPO") != repo  # a fork's PR: the token cannot comment
    # The summary has the drawing whole (a long comment is cut), and on a fork the CI report too.
    with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as summary:
        summary.write("\n\n".join(b for b in (body if fork else None, drawing_body) if b))
    if fork:
        return
    pages = json.loads(gh("api", "--paginate", "--slurp", f"repos/{repo}/issues/{pr}/comments"))
    comments = [c for page in pages for c in page]
    sync(repo, pr, comments, MARKER, body)
    if drawn:
        sync(repo, pr, comments, DRAWING_MARKER, drawing_body and cut(drawing_body))
    else:
        print("::warning::the drawing diff did not run: its PR comment is left as it was")


if __name__ == "__main__":
    main()
