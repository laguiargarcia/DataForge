"""Pure dependency-condition evaluation. No I/O. No DB."""
from .models import Task


_STATUS_TO_CONDITION = {
    "success": "succeeded",
    "failed": "failed",
    "skipped": "skipped",
}


def _edge_satisfied(edge_when: list[str], parent_status: str | None) -> bool:
    if parent_status is None:
        return False
    cond = _STATUS_TO_CONDITION.get(parent_status)
    if cond is None:
        return False
    return cond in edge_when


def should_run(task: Task, parent_status: dict[str, str]) -> bool:
    if not task.depends_on:
        return True
    results = [
        _edge_satisfied(edge.when, parent_status.get(edge.task))
        for edge in task.depends_on
    ]
    if task.match == "any":
        return any(results)
    return all(results)
