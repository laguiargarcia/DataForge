"""dataforge/yamlio.py: the same YAML with or without libyaml (df-xbn9).

A Python with no PyYAML wheel yet gets a PyYAML built without libyaml (Python 3.15 on Windows): its
C classes are missing, and yamlio falls back to the pure-Python safe loader and dumper. Both
branches run here, whichever PyYAML this machine has.
"""
import contextlib
import importlib
import io

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st

from dataforge import yamlio

# No control characters: the pure-Python dumper writes U+0085 (NEL) raw in a quoted string, and
# every reader, itself included, reads it back as a space (a PyYAML bug, df-jgnp). libyaml
# escapes it.
_TEXT = st.text(st.characters(blacklist_categories=("Cc", "Cs")), max_size=20)
_SCALARS = st.none() | st.booleans() | st.integers() | _TEXT
_DATA = st.recursive(
    _SCALARS,
    lambda inner: st.lists(inner, max_size=4) | st.dictionaries(_TEXT, inner, max_size=4),
    max_leaves=12,
)


_HAS_LIBYAML = hasattr(yaml, "CSafeLoader") and hasattr(yaml, "CSafeDumper")
_BRANCHES = pytest.mark.parametrize(
    "libyaml",
    [
        pytest.param(True, id="libyaml", marks=pytest.mark.skipif(not _HAS_LIBYAML, reason="PyYAML without libyaml")),
        pytest.param(False, id="pure-python"),
    ],
)


@contextlib.contextmanager
def _branch(libyaml: bool):
    saved = yamlio._LIBYAML
    yamlio._LIBYAML = libyaml
    try:
        yield
    finally:
        yamlio._LIBYAML = saved


@_BRANCHES
@given(data=_DATA)
def test_what_is_dumped_loads_back_as_the_same_data(libyaml, data):
    with _branch(libyaml):
        assert yamlio.load(yamlio.dump(data, allow_unicode=True, sort_keys=False)) == data


@pytest.mark.skipif(not _HAS_LIBYAML, reason="PyYAML without libyaml")
@given(data=_DATA)
def test_each_branch_reads_what_the_other_wrote(data):
    """A job saved on a Windows prod without libyaml opens on Linux, and the other way around. The
    text itself may differ in corner cases (a bare scalar ends with "..." in pure Python, an empty
    key is written as "? ''"), never the data."""
    for writer, reader in ((True, False), (False, True)):
        with _branch(writer):
            text = yamlio.dump(data, allow_unicode=True, sort_keys=False)
        with _branch(reader):
            assert yamlio.load(text) == data


@_BRANCHES
def test_a_python_object_tag_is_refused(libyaml):
    with _branch(libyaml), pytest.raises(yaml.constructor.ConstructorError):
        yamlio.load("!!python/object/apply:os.getcwd []")


@_BRANCHES
def test_dump_writes_into_a_stream(libyaml):
    out = io.StringIO()
    with _branch(libyaml):
        assert yamlio.dump({"name": "job"}, out) is None
    assert out.getvalue() == "name: job\n"


def test_without_the_c_classes_the_pure_python_branch_is_taken(monkeypatch):
    monkeypatch.delattr(yaml, "CSafeLoader", raising=False)
    monkeypatch.delattr(yaml, "CSafeDumper", raising=False)
    reloaded = importlib.reload(yamlio)
    try:
        assert reloaded._LIBYAML is False
        assert reloaded.load("a: 1") == {"a": 1}
    finally:
        monkeypatch.undo()
        importlib.reload(yamlio)
