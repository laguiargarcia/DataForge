from hypothesis import example, given, strategies as st
from dataforge.models import Task
from dataforge.conditions import should_run


def _task(name, deps, match="all"):
    return Task(name=name, script="s.py", depends_on=deps, match=match)


def test_no_deps_runs():
    t = _task("t", [])
    assert should_run(t, parent_status={}) is True


def test_all_match_default_succeeded():
    t = _task("t", ["a", "b"])
    assert should_run(t, {"a": "success", "b": "success"}) is True
    assert should_run(t, {"a": "success", "b": "failed"}) is False


def test_match_any():
    t = _task("t", [{"task": "a", "when": "failed"},
                    {"task": "b", "when": "failed"}], match="any")
    assert should_run(t, {"a": "success", "b": "failed"}) is True
    assert should_run(t, {"a": "success", "b": "success"}) is False


def test_when_list_within_edge_or():
    t = _task("t", [{"task": "a", "when": ["failed", "skipped"]}])
    assert should_run(t, {"a": "failed"}) is True
    assert should_run(t, {"a": "skipped"}) is True
    assert should_run(t, {"a": "success"}) is False


def test_completed_shorthand():
    t = _task("t", [{"task": "a", "when": "completed"}])
    assert should_run(t, {"a": "success"}) is True
    assert should_run(t, {"a": "failed"}) is True
    assert should_run(t, {"a": "skipped"}) is False


def test_parent_skipped_blocks_default():
    t = _task("t", ["a"])
    assert should_run(t, {"a": "skipped"}) is False


# --- Properties (df-ahua.23): the edge rule over every combination of outcomes ---

# Outcome the engine records for a parent, and the `when` condition it satisfies (docs/subsystems/engine.md).
_OUTCOME_CONDITION = {"success": "succeeded", "failed": "failed", "skipped": "skipped"}
# A parent with no recorded outcome (absent from the map) or one that is not a final outcome.
_NO_OUTCOME = [None, "running", "pending", "succeeded", ""]


@st.composite
def _edges_with_expected(draw):
    """Edges built so each one's satisfaction is known from how its parent status was chosen."""
    n = draw(st.integers(min_value=1, max_value=4))
    deps, status, expected = [], {}, []
    for i in range(n):
        when = draw(st.lists(st.sampled_from(["succeeded", "failed", "skipped"]),
                             min_size=1, max_size=3, unique=True))
        name = f"p{i}"
        deps.append({"task": name, "when": when})
        inside = [o for o, c in _OUTCOME_CONDITION.items() if c in when]
        outside = [o for o, c in _OUTCOME_CONDITION.items() if c not in when] + _NO_OUTCOME
        satisfied = draw(st.booleans())
        chosen = draw(st.sampled_from(inside if satisfied else outside))
        if chosen is not None:
            status[name] = chosen
        expected.append(satisfied)
    return deps, status, expected


@given(_edges_with_expected(), st.sampled_from(["all", "any"]))
def test_match_combines_independently_satisfied_edges(case, match):
    """Each edge holds iff its parent's outcome is in its `when`; match=all needs every edge, any needs one."""
    deps, status, expected = case
    t = _task("t", deps, match=match)
    want = all(expected) if match == "all" else any(expected)
    assert should_run(t, status) is want


@given(st.sampled_from(_NO_OUTCOME), st.sampled_from(["all", "any"]))
@example(None, "all")
@example("running", "any")
def test_parent_without_final_outcome_never_satisfies_an_edge(parent, match):
    """Fail closed: a parent with no recorded or an unknown outcome blocks the edge even when `when`
    lists every condition."""
    t = _task("t", [{"task": "a", "when": ["succeeded", "failed", "skipped"]}], match=match)
    status = {} if parent is None else {"a": parent}
    assert should_run(t, status) is False
