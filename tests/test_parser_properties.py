"""Properties of dataforge/parser.py: job.yaml and trigger files written by the GUI/API read back
as the same model, stay compact (no key a reader would not miss) and keep their documented key order.
"""
import tempfile
from pathlib import Path

import pytest
import yaml
from hypothesis import example, given, strategies as st
from pydantic import ValidationError

from dataforge.models import Job, Task, TriggerModel
from dataforge.parser import (
    is_valid_job_name,
    load_job,
    load_trigger,
    load_triggers,
    save_job,
    save_trigger,
)

_NAME = st.from_regex(r"[A-Za-z0-9_-]{1,8}", fullmatch=True)
# Job and trigger names become file names: Windows cannot create its reserved device names (df-ahua.23.3).
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"{p}{n}" for p in ("COM", "LPT") for n in range(1, 10))}
_FILE_NAME = _NAME.filter(lambda s: s.upper() not in _RESERVED)
# Strings YAML would read back as something else unless the writer quotes them.
_TRICKY = st.sampled_from(["yes", "no", "null", "~", "1", "1.5", "2026-01-01", "a: b", "#x", "- x", "", " x "])
_TEXT = st.one_of(_TRICKY, st.text(alphabet="abcXYZ09 _-.:/éçã", max_size=10))
_SCALAR = st.one_of(st.none(), st.booleans(), st.integers(-1000, 1000), _TEXT)
_PARAM_VALUE = st.one_of(_SCALAR, st.lists(_TEXT, max_size=3))
_CONDITIONS = st.lists(st.sampled_from(["succeeded", "failed", "skipped"]), min_size=1, max_size=3, unique=True)
_RETRY = st.fixed_dictionaries({"attempts": st.integers(1, 5), "delay_seconds": st.integers(0, 60)})
_TYPES = ["python", "job", "error", "message"]


@st.composite
def _tasks(draw):
    """Any task the model accepts: inferable from script/job alone, or carrying an explicit type."""
    script = draw(st.one_of(st.none(), _TEXT))
    job = draw(st.one_of(st.none(), _NAME))
    inferable = bool(script) != bool(job)
    ttype = draw(st.one_of(st.none(), st.sampled_from(_TYPES)) if inferable else st.sampled_from(_TYPES))
    edges = draw(st.lists(st.one_of(_NAME, st.builds(lambda t, w: {"task": t, "when": w}, _NAME, _CONDITIONS)),
                          max_size=3))
    return Task(
        name=draw(_NAME), type=ttype, script=script, job=job,
        params=draw(st.dictionaries(_NAME, _PARAM_VALUE, max_size=3)),
        depends_on=edges,
        match=draw(st.sampled_from(["all", "any"])),
        retry=draw(_RETRY),
        active=draw(st.booleans()),
    )


_PARAMETER = st.fixed_dictionaries(
    {"name": _NAME, "type": st.sampled_from(["string", "int", "float", "bool", "date", "json"])},
    optional={"default": _SCALAR, "description": _TEXT},
)

_JOBS = st.builds(
    Job,
    name=_FILE_NAME,
    tasks=st.lists(_tasks(), max_size=4),
    retry=_RETRY,
    env_file=st.one_of(st.none(), _TEXT),
    schedule=st.one_of(st.none(), _TEXT),
    parameters=st.lists(_PARAMETER, max_size=2),
)

# One job that holds every optional key with a non-default value, and the tasks whose type
# only an explicit `type:` can carry (no script and no job, or both).
_FULL_JOB = Job(
    name="full", retry={"attempts": 1, "delay_seconds": 0}, env_file=".env", schedule="0 2 * * *",
    parameters=[{"name": "d", "type": "date", "default": "2026-01-01", "description": "dia"}],
    tasks=[
        Task(name="a", script="a.py", match="any", retry={"attempts": 1, "delay_seconds": 0},
             active=False, params={"k": "yes"}, depends_on=[{"task": "b", "when": "failed"}]),
        Task(name="b", job="other"),
        Task(name="c", type="job"),
        Task(name="d", type="python"),
        Task(name="e", type="message", script="m.py", job="other"),
    ],
)


def _saved(job: Job) -> tuple[Job, str]:
    """save_job into a fresh workspace (no jobs/ dir yet), then the job read back and the file text."""
    with tempfile.TemporaryDirectory() as d:
        ws = Path(d)
        save_job(ws, job)
        text = (ws / "jobs" / job.name / "job.yaml").read_bytes().decode("utf-8")
        return load_job(ws, job.name), text


def _load_dict(data: dict) -> Job:
    """What load_job makes of a job.yaml holding *data*."""
    with tempfile.TemporaryDirectory() as d:
        ws = Path(d)
        (ws / "jobs" / "j").mkdir(parents=True)
        (ws / "jobs" / "j" / "job.yaml").write_bytes(yaml.safe_dump(data, allow_unicode=True).encode("utf-8"))
        return load_job(ws, "j")


@given(_JOBS)
@example(_FULL_JOB)
def test_saved_job_reads_back_as_the_same_job(job):
    """save_job then load_job is the identity: the GUI editor never changes a job by saving it."""
    reloaded, _ = _saved(job)
    assert reloaded == job


@given(_JOBS)
@example(_FULL_JOB)
def test_every_key_written_is_needed(job):
    """Compact job.yaml: dropping any one written key (besides name and tasks) changes what loads,
    so no default and no inferable `type:` is ever written."""
    _, text = _saved(job)
    data = yaml.safe_load(text)
    paths = [(k,) for k in data if k not in ("name", "tasks")]
    paths += [(i, k) for i, t in enumerate(data["tasks"]) for k in t if k != "name"]
    for path in paths:
        trimmed = yaml.safe_load(text)
        if len(path) == 1:
            del trimmed[path[0]]
        else:
            del trimmed["tasks"][path[0]][path[1]]
        try:
            changed = _load_dict(trimmed) != job
        except ValidationError:
            changed = True
        assert changed, f"key {path} is redundant in:\n{text}"


@given(_JOBS)
@example(_FULL_JOB)
def test_written_keys_keep_name_first_and_tasks_last(job):
    """Key order is the compact order, not alphabetical: `name:` opens the job and every task, `tasks:`
    closes the job (save_job's docstring: sort_keys=False is mandatory)."""
    _, text = _saved(job)
    data = yaml.safe_load(text)
    assert next(iter(data)) == "name"
    assert list(data)[-1] == "tasks"
    for t in data["tasks"]:
        assert next(iter(t)) == "name"


def test_non_ascii_is_written_literally_as_utf8():
    """A hand-read job.yaml shows 'relatório' as written, in UTF-8, not as an escape sequence."""
    job = Job(name="rel", tasks=[Task(name="t", script="relatório.py", params={"título": "Março"})])
    _, text = _saved(job)
    for word in ("relatório.py", "título", "Março"):
        assert word in text


def test_saving_an_existing_job_replaces_its_file(workspace_dir):
    """Saving a job again (an edit) overwrites jobs/<name>/job.yaml instead of failing on the existing dir."""
    save_job(workspace_dir, Job(name="j", tasks=[Task(name="old", script="a.py")]))
    save_job(workspace_dir, Job(name="j", tasks=[Task(name="new", script="b.py")]))
    assert [t.name for t in load_job(workspace_dir, "j").tasks] == ["new"]


_TRIGGERS = st.builds(
    TriggerModel, name=_FILE_NAME, cron=_TEXT, enabled=st.booleans(),
    jobs=st.lists(st.fixed_dictionaries({"workspace": _NAME, "job": _NAME,
                                         "params": st.dictionaries(_NAME, _SCALAR, max_size=2)}), max_size=3),
)


@given(_TRIGGERS)
@example(TriggerModel(name="diário", cron="0 2 * * *", jobs=[{"workspace": "ws", "job": "j", "params": {"x": "não"}}]))
def test_saved_trigger_reads_back_as_the_same_trigger(trigger):
    """save_trigger then load_trigger (and load_triggers) give back the trigger, non-ASCII included."""
    with tempfile.TemporaryDirectory() as d:
        ws = Path(d)
        save_trigger(ws, trigger)
        text = (ws / "triggers" / f"{trigger.name}.yaml").read_bytes().decode("utf-8")
        assert load_trigger(ws, trigger.name) == trigger
        assert load_triggers(ws) == [trigger]
    if trigger.name == "diário":
        assert "não" in text


@given(_NAME)
def test_job_names_from_the_safe_alphabet_are_valid(name):
    """Every name made of ASCII letters, digits, '-' and '_' is a valid job name."""
    assert is_valid_job_name(name) is True


_SAFE = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


@given(st.text(min_size=1, max_size=6).filter(lambda s: not set(s) <= _SAFE))
@example("../evil")
@example("a b")
def test_a_job_name_with_any_other_character_is_invalid(name):
    """The job name is a directory name: one character outside [A-Za-z0-9_-] makes it invalid."""
    assert is_valid_job_name(name) is False


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="df-ahua.23.1: '$' in re.match accepts a trailing newline")
def test_a_trailing_newline_makes_a_job_name_invalid():
    """'etl\\n' is not a safe directory name; compat.valid_name already refuses it (df-utq8)."""
    assert is_valid_job_name("etl\n") is False
