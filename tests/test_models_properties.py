"""Rules of the validators in dataforge/models.py: a dependency's `when` is a string or a list of
the four conditions, 'completed' meaning succeeded and failed, kept in first-seen order without
repeats; a depends_on entry is a task name or an edge; and a task without `type:` is python with
only `script:`, job with only `job:`, refused otherwise.

Not a mutation target: mutmut 3.8 leaves every function with a decorator other than a lone
@classmethod/@staticmethod alone, and the three functions here are pydantic validators."""
import pytest
from hypothesis import example, given, settings, strategies as st
from pydantic import ValidationError

from dataforge.models import DependencyEdge, Task

_CONDITION = st.sampled_from(["succeeded", "failed", "skipped", "completed"])
_MEANS = {"completed": ["succeeded", "failed"]}


@settings(max_examples=80)
@given(st.lists(_CONDITION, min_size=1, max_size=6) | _CONDITION)
@example(["completed", "failed", "succeeded", "skipped", "completed"])
@example("completed")
def test_when_lists_each_condition_once_in_first_seen_order(when):
    """`when` (a string or a list) becomes the list of conditions it names, 'completed' read as
    succeeded then failed, each once, in the order first named."""
    named = [when] if isinstance(when, str) else when
    meant = [c for name in named for c in _MEANS.get(name, [name])]
    expected = [c for i, c in enumerate(meant) if c not in meant[:i]]
    assert DependencyEdge(task="p", when=when).when == expected


@pytest.mark.parametrize("when", ["bogus", ["succeeded", "Failed"], 3, {"failed": True}])
def test_a_when_outside_the_four_conditions_is_refused(when):
    """A `when` that is not one of the four conditions, or not a string or list, is refused."""
    with pytest.raises(ValidationError, match="when"):
        DependencyEdge(task="p", when=when)


def test_depends_on_takes_names_edges_and_nothing_else():
    """depends_on entries may be a task name (an edge on success), a mapping or an edge; None means
    no dependencies; any other entry is refused."""
    edge = DependencyEdge(task="c", when=["failed"])
    t = Task(name="t", script="s.py", depends_on=["a", {"task": "b", "when": "skipped"}, edge])
    assert t.depends_on == [DependencyEdge(task="a", when=["succeeded"]),
                            DependencyEdge(task="b", when=["skipped"]), edge]
    assert Task(name="t", script="s.py", depends_on=None).depends_on == []
    with pytest.raises(ValidationError, match="depends_on entry"):
        Task(name="t", script="s.py", depends_on=[7])


@pytest.mark.parametrize(("fields", "inferred"), [
    ({"script": "s.py"}, "python"), ({"job": "child"}, "job"),
    ({"type": "message"}, "message"), ({"type": "error", "script": "s.py", "job": "j"}, "error"),
])
def test_a_task_type_is_given_or_inferred_from_script_or_job(fields, inferred):
    """A task's type is the declared one, or python with only `script:`, job with only `job:`."""
    assert Task(name="t", **fields).type == inferred


@pytest.mark.parametrize("fields", [{}, {"script": "s.py", "job": "child"}])
def test_a_task_with_neither_or_both_of_script_and_job_needs_a_type(fields):
    """Without `type:`, a task with both or neither of `script:` and `job:` is refused, naming it."""
    with pytest.raises(ValidationError, match="Task 'ambiguous'"):
        Task(name="ambiguous", **fields)
