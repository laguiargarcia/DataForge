import pytest
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
