from collections import defaultdict, deque
from .models import Task


def _check_known_deps(pipelines: list[Task], by_name: dict[str, Task]) -> None:
    for p in pipelines:
        for edge in p.depends_on:
            if edge.task not in by_name:
                raise ValueError(f"Unknown dependency '{edge.task}' in pipeline '{p.name}'")


def _graph(pipelines: list[Task]) -> tuple[dict[str, int], dict[str, list[str]]]:
    """Each task's count of unfinished parents, and each task's dependents."""
    in_degree = {p.name: 0 for p in pipelines}
    dependents: dict[str, list[str]] = defaultdict(list)
    for p in pipelines:
        for edge in p.depends_on:
            in_degree[p.name] += 1
            dependents[edge.task].append(p.name)
    return in_degree, dependents


def _release(finished: list[str], in_degree: dict[str, int],
             dependents: dict[str, list[str]]) -> list[str]:
    """Count `finished` as done for their dependents; return the ones left with no parent."""
    ready = []
    for name in finished:
        for dependent in dependents[name]:
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                ready.append(dependent)
    return ready


def topological_levels(pipelines: list[Task]) -> list[list[Task]]:
    by_name = {p.name: p for p in pipelines}
    _check_known_deps(pipelines, by_name)
    in_degree, dependents = _graph(pipelines)

    queue = deque(name for name, deg in in_degree.items() if deg == 0)
    levels = []

    while queue:
        level_names = list(queue)
        queue.clear()
        levels.append([by_name[n] for n in level_names])

        queue.extend(_release(level_names, in_degree, dependents))

    if sum(in_degree.values()) > 0:
        raise ValueError("cycle detected in pipeline dependency graph")

    return levels
