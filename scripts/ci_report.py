"""Post one comment on the pull request with each CI job's result, edited on every run.

Run by the `report` job in .github/workflows/ci.yml. Each job saves its output under report/
as <job>.txt or <job>-<part>.txt; this script turns those files and every job's result into a
Markdown comment tagged with MARKER, and edits that comment instead of adding a new one.

Environment: RESULTS (toJSON(needs)), PR, RUN_URL, SHA, GITHUB_REPOSITORY, GH_TOKEN.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

MARKER = "<!-- ci-report -->"
REPORT_DIR = Path("report")
ICONS = {"success": "✅", "failure": "❌", "cancelled": "⚠️", "skipped": "⏭️"}
# The line that states an outcome: the quality gate, the test loop gate, or pytest's summary.
STATUS = re.compile(r"quality gate:|testloop: |\b\d+ (passed|failed|errors?)\b")
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
    return (status or lines or ["(no output)"])[-1].replace("|", "\\|")


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


def gh(*args: str, payload: str | None = None) -> str:
    return subprocess.run(["gh", *args], input=payload, capture_output=True, check=True, encoding="utf-8").stdout


def main() -> None:
    repo, pr = os.environ["GITHUB_REPOSITORY"], os.environ["PR"]
    body = build(json.loads(os.environ["RESULTS"]), os.environ["RUN_URL"], os.environ["SHA"][:7])
    pages = json.loads(gh("api", "--paginate", "--slurp", f"repos/{repo}/issues/{pr}/comments"))
    ours = [c for page in pages for c in page if MARKER in (c.get("body") or "") and c["user"]["type"] == "Bot"]
    payload = json.dumps({"body": body})
    if ours:
        gh("api", "-X", "PATCH", f"repos/{repo}/issues/comments/{ours[-1]['id']}", "--input", "-", payload=payload)
    else:
        gh("api", "-X", "POST", f"repos/{repo}/issues/{pr}/comments", "--input", "-", payload=payload)
    print(body)


if __name__ == "__main__":
    main()
