import pytest
from hypothesis import example, given, settings, strategies as st
from dataforge.dag import topological_levels
from dataforge.models import Task, RetryConfig


def make_task(name, depends_on=None):
    return Task(
        name=name,
        script="dummy.py",
        depends_on=depends_on or [],
        retry=RetryConfig(attempts=1, delay_seconds=0),
    )


def test_cycle_raises():
    t1 = make_task("t1", depends_on=["t2"])
    t2 = make_task("t2", depends_on=["t1"])
    with pytest.raises(ValueError, match="cycle detected"):
        topological_levels([t1, t2])


def test_unknown_dependency_raises():
    t1 = make_task("t1", depends_on=["nonexistent"])
    with pytest.raises(ValueError, match="Unknown dependency"):
        topological_levels([t1])


def test_linear_dag():
    t1 = make_task("t1")
    t2 = make_task("t2", depends_on=["t1"])
    levels = topological_levels([t1, t2])
    assert len(levels) == 2
    assert levels[0] == [t1]
    assert levels[1] == [t2]


def test_parallel_dag():
    t1 = make_task("t1")
    t2 = make_task("t2")
    levels = topological_levels([t1, t2])
    assert len(levels) == 1
    assert set(t.name for t in levels[0]) == {"t1", "t2"}


def test_empty_tasks():
    levels = topological_levels([])
    assert levels == []


def test_task_depending_on_itself_is_a_cycle():
    """A self-loop leaves exactly one unresolved in-degree, the smallest cycle there is."""
    t1 = make_task("t1", depends_on=["t1"])
    with pytest.raises(ValueError, match="cycle detected"):
        topological_levels([t1])


@st.composite
def dags(draw):
    """Tasks t0..tn-1 where ti depends only on lower indices (so the graph is acyclic), in a
    shuffled order, plus each task's expected level: 0 without parents, else 1 + the deepest
    parent's level (the longest path from a root)."""
    n = draw(st.integers(min_value=1, max_value=8))
    parents = [draw(st.sets(st.integers(0, i - 1), max_size=i)) if i else set() for i in range(n)]
    depth: list[int] = []
    for ps in parents:
        depth.append(1 + max(depth[p] for p in ps) if ps else 0)
    tasks = [make_task(f"t{i}", depends_on=[f"t{p}" for p in sorted(ps)]) for i, ps in enumerate(parents)]
    return draw(st.permutations(tasks)), {f"t{i}": d for i, d in enumerate(depth)}


@settings(deadline=None)
@example(([make_task("a"), make_task("b", ["a"]), make_task("c", ["a", "b"])], {"a": 0, "b": 1, "c": 2}))
@given(dags())
def test_each_task_lands_one_level_after_its_deepest_dependency(case):
    """Every task appears exactly once, at the level right after the deepest of its parents.
    The pinned example has a task with two parents on different levels: counting a task's
    parents wrong would release it as soon as the first one finished."""
    tasks, expected = case
    levels = topological_levels(tasks)
    placed = {t.name: i for i, level in enumerate(levels) for t in level}
    assert sorted(t.name for level in levels for t in level) == sorted(expected)
    assert placed == expected
