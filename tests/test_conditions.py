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
