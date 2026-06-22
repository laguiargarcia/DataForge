"""Pure: builds the variable dict + str.format renderer for the message task."""
from datetime import datetime
from typing import Any


_STDERR_TAIL_LINES = 5


def _local_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.isoformat(timespec="seconds")


def _summarize_failure(result: dict[str, Any]) -> str:
    output = (result.get("output") or "").strip().splitlines()
    tail = "\n".join(output[-_STDERR_TAIL_LINES:])
    head = f"- {result['name']} (rc={result.get('rc')})"
    return f"{head}:\n  {tail}" if tail else head


def build_variables(*, workspace: str, job: str, trigger: str, run_id: str,
                    status: str, started_at: datetime, finished_at: datetime,
                    task_results: list[dict[str, Any]]) -> dict[str, Any]:
    failed = [r for r in task_results if r["status"] == "failed"]
    succeeded = [r for r in task_results if r["status"] == "success"]
    skipped = [r for r in task_results if r["status"] == "skipped"]
    duration = (finished_at - started_at).total_seconds()
    return {
        "workspace": workspace,
        "job": job,
        "trigger": trigger or "manual",
        "run_id": run_id,
        "status": status,
        "started_at": _local_iso(started_at),
        "finished_at": _local_iso(finished_at),
        "duration_seconds": duration,
        "failed_tasks": ", ".join(r["name"] for r in failed),
        "succeeded_tasks": ", ".join(r["name"] for r in succeeded),
        "skipped_tasks": ", ".join(r["name"] for r in skipped),
        "failed_tasks_summary": "\n".join(_summarize_failure(r) for r in failed),
    }


def render(template: str, variables: dict[str, Any]) -> str:
    return template.format(**variables)
