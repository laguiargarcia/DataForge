from collections import defaultdict, deque
from .models import Task


def topological_levels(pipelines: list[Task]) -> list[list[Task]]:
    by_name = {p.name: p for p in pipelines}

    for p in pipelines:
        for edge in p.depends_on:
            if edge.task not in by_name:
                raise ValueError(f"Unknown dependency '{edge.task}' in pipeline '{p.name}'")

    in_degree = {p.name: 0 for p in pipelines}
    dependents: dict[str, list[str]] = defaultdict(list)

    for p in pipelines:
        for edge in p.depends_on:
            in_degree[p.name] += 1
            dependents[edge.task].append(p.name)

    queue = deque(name for name, deg in in_degree.items() if deg == 0)
    levels = []

    while queue:
        level_names = list(queue)
        queue.clear()
        levels.append([by_name[n] for n in level_names])

        for name in level_names:
            for dependent in dependents[name]:
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

    if sum(in_degree.values()) > 0:
        raise ValueError("cycle detected in pipeline dependency graph")

    return levels
