from dataforge.models import Task, DependencyEdge


def test_bare_string_normalizes_to_succeeded_edge():
    t = Task(name="t", script="s.py", depends_on=["parent"])
    assert t.depends_on == [DependencyEdge(task="parent", when=["succeeded"])]


def test_explicit_when_single_string_becomes_list():
    t = Task(name="t", script="s.py", depends_on=[{"task": "p", "when": "failed"}])
    assert t.depends_on[0].when == ["failed"]


def test_when_list_preserved():
    t = Task(name="t", script="s.py",
             depends_on=[{"task": "p", "when": ["failed", "skipped"]}])
    assert t.depends_on[0].when == ["failed", "skipped"]


def test_completed_expands_to_succeeded_failed():
    t = Task(name="t", script="s.py",
             depends_on=[{"task": "p", "when": "completed"}])
    assert t.depends_on[0].when == ["succeeded", "failed"]


def test_match_default_all():
    t = Task(name="t", script="s.py")
    assert t.match == "all"


def test_match_explicit_any():
    t = Task(name="t", script="s.py", match="any")
    assert t.match == "any"


def test_invalid_when_value_rejected():
    import pytest
    with pytest.raises(ValueError, match="when"):
        Task(name="t", script="s.py", depends_on=[{"task": "p", "when": "bogus"}])
