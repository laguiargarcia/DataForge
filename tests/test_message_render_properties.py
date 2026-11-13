"""Rules of dataforge/tasks/message/render.py, the template variables of a message task
(docs/subsystems/tasks-message.md, "Templating"): every documented variable is there, timestamps
are ISO 8601 with the local offset, task lists keep the results' order, and each failed task's
summary is its name, rc and the last five lines of its output."""
from datetime import datetime, timedelta, timezone

from hypothesis import example, given, settings, strategies as st

from dataforge.tasks.message.render import build_variables, render

_DOCUMENTED = {"workspace", "job", "trigger", "run_id", "status", "started_at", "finished_at",
               "duration_seconds", "failed_tasks", "succeeded_tasks", "skipped_tasks",
               "failed_tasks_summary"}
_T0 = datetime(2026, 5, 28, 6, 0, 0)
# Output lines without surrounding blanks or line-break characters, so splitting the joined output
# gives them back; the letter X is in the alphabet on purpose.
_LINE = st.from_regex(r"[A-Za-z0-9X]([A-Za-z0-9X =:.]{0,10}[A-Za-z0-9X])?", fullmatch=True)
_RESULT = st.fixed_dictionaries({
    "status": st.sampled_from(["success", "failed", "skipped"]),
    "rc": st.sampled_from([0, 1, 2, None]),
    "lines": st.lists(_LINE, max_size=8),
    "none_output": st.booleans(),
})


def _variables(results=(), trigger="cron", started=_T0, finished=_T0, run_id="r1"):
    return build_variables(workspace="ws", job="nightly", trigger=trigger, run_id=run_id,
                           status="failed", started_at=started, finished_at=finished,
                           task_results=list(results))


def _as_results(drawn: list[dict]) -> list[dict]:
    """Task results named t0, t1... in the drawn order; output None when drawn so and empty."""
    results = []
    for i, d in enumerate(drawn):
        output = None if d["none_output"] and not d["lines"] else "\n".join(d["lines"]) + "\n"
        results.append({"name": f"t{i}", "status": d["status"], "rc": d["rc"], "output": output,
                        "lines": d["lines"]})
    return results


def test_every_documented_variable_is_built_from_its_input():
    """build_variables returns exactly the documented variables, the scalars taken from the
    arguments as given and the duration in seconds between the two timestamps."""
    variables = _variables(finished=_T0 + timedelta(seconds=90))
    assert set(variables) == _DOCUMENTED
    assert (variables["workspace"], variables["job"], variables["trigger"], variables["run_id"],
            variables["status"]) == ("ws", "nightly", "cron", "r1", "failed")
    assert variables["duration_seconds"] == 90.0


def test_a_run_without_a_trigger_reads_manual():
    """An empty trigger renders as 'manual' (the documented default for runs no trigger started)."""
    assert _variables(trigger="")["trigger"] == "manual"


@settings(max_examples=60)
@given(when=st.datetimes(min_value=datetime(1990, 1, 1), max_value=datetime(2090, 1, 1)),
       offset_minutes=st.none() | st.integers(-12 * 60, 14 * 60))
@example(when=_T0, offset_minutes=None)
@example(when=_T0.replace(microsecond=999_999), offset_minutes=-180)
def test_timestamps_are_iso_8601_to_the_second_with_an_offset(when, offset_minutes):
    """started_at and finished_at are ISO 8601 to the second with a UTC offset, naive input
    included; an aware input keeps its instant. (Which instant a naive input names is df-ahua.23.12:
    render reads it as local time, the handler passes naive UTC.)"""
    if offset_minutes is not None:
        when = when.replace(tzinfo=timezone(timedelta(minutes=offset_minutes)))
    later = when + timedelta(seconds=1)
    variables = _variables(started=when, finished=later)
    for key, given_time in (("started_at", when), ("finished_at", later)):
        parsed = datetime.fromisoformat(variables[key])
        assert parsed.tzinfo is not None
        assert "." not in variables[key]
        if given_time.tzinfo is not None:
            assert parsed == given_time.replace(microsecond=0)


@settings(max_examples=80)
@given(st.lists(_RESULT, max_size=6))
@example([{"status": "failed", "rc": 1, "lines": [], "none_output": True},
          {"status": "failed", "rc": 2, "lines": ["X1", "a", "b", "c", "d", "e", "fX"],
           "none_output": False},
          {"status": "success", "rc": 0, "lines": ["ok"], "none_output": False},
          {"status": "skipped", "rc": None, "lines": [], "none_output": True},
          {"status": "success", "rc": 0, "lines": [], "none_output": False}])
def test_task_lists_and_failure_summaries_follow_the_results(drawn):
    """failed/succeeded/skipped_tasks list the names with that status in the results' order,
    joined by ', '. failed_tasks_summary has one block per failed task, joined by newlines:
    '- <name> (rc=<rc>)', then, when its output has lines, ':' and its last five lines, the first
    one indented by two spaces."""
    results = _as_results(drawn)
    variables = _variables(results)
    for key, status in (("failed_tasks", "failed"), ("succeeded_tasks", "success"),
                        ("skipped_tasks", "skipped")):
        assert variables[key] == ", ".join(r["name"] for r in results if r["status"] == status)
    blocks = []
    for r in results:
        if r["status"] != "failed":
            continue
        head = f"- {r['name']} (rc={r['rc']})"
        tail = r["lines"][-5:]
        blocks.append(head if not tail else head + ":\n  " + "\n".join(tail))
    assert variables["failed_tasks_summary"] == "\n".join(blocks)


def test_render_fills_every_variable_of_the_template():
    """render substitutes each {variable} of the template with its value from the variables."""
    variables = _variables([{"name": "a", "status": "failed", "rc": 1, "output": "boom"}])
    template = " | ".join("{" + key + "}" for key in sorted(_DOCUMENTED))
    assert render(template, variables) == " | ".join(str(variables[k]) for k in sorted(_DOCUMENTED))
