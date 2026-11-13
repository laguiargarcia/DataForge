"""Rules of the env-file loader that the python and the message task types share
(`_load_env_file` in dataforge/tasks/python_handler.py and dataforge/tasks/message/handler.py; the
message doc says it is "exactly the same loader"): one KEY=VALUE per line, blanks around the key and
the value dropped, one pair of surrounding quotes dropped, and blank lines, comments (a commented-out
assignment included) and lines without '=' skipped wherever they are. The file is UTF-8."""
import tempfile
from pathlib import Path

import pytest
from hypothesis import example, given, settings, strategies as st

from dataforge.tasks.message.handler import _load_env_file as message_loader
from dataforge.tasks.python_handler import _load_env_file as python_loader

_LOADERS = pytest.mark.parametrize("load", [python_loader, message_loader], ids=["python", "message"])
_KEY = st.from_regex(r"[A-Z_][A-Z0-9_]{0,8}", fullmatch=True)
# Values neither start nor end with a blank or a quote, so the expected value is the drawn one; '=',
# '#', quotes inside and the letter X are in the alphabet on purpose.
_VALUE = st.from_regex(r"([A-Za-z0-9X=#:/.é]([A-Za-z0-9X=#:/. \"'é]{0,10}[A-Za-z0-9X=#:/.é])?)?",
                       fullmatch=True)
_STYLE = st.sampled_from(["{k}={v}", '{k}="{v}"', "{k}='{v}'", "  {k} = {v}  ", '{k} = "{v}"'])
_NOISE = st.sampled_from(["", "   ", "# a comment", "#{k}=commented out", "  # {k}=indented comment",
                          "no equals sign here"])


def _load_from(load, lines: list[str], newline: str) -> dict[str, str]:
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / ".env"
        path.write_bytes(newline.join(lines).encode("utf-8"))
        return load(path)


@_LOADERS
@settings(max_examples=80)
@given(data=st.data())
@example(data=None)
def test_the_env_file_yields_every_assignment_and_nothing_else(load, data):
    """Each KEY=VALUE line in any of the accepted spellings gives KEY -> VALUE, and the noise lines
    (blank, comment, commented-out assignment, no '=') add nothing nor stop the reading."""
    if data is None:   # every case at once: noise first, '=' in a value, both quotes, UTF-8, X
        lines = ["# header", "", "#TOKEN=old", "TOKEN=abc==", 'NAME="café"', "QUOTE='XmX'",
                 "  SPACED = a b  ", "no equals", "LAST=X"]
        expected = {"TOKEN": "abc==", "NAME": "café", "QUOTE": "XmX", "SPACED": "a b", "LAST": "X"}
        assert _load_from(load, lines, "\n") == expected
        return
    pairs = data.draw(st.lists(st.tuples(_KEY, _VALUE, _STYLE), unique_by=lambda t: t[0], max_size=6))
    noise = data.draw(st.lists(_NOISE, max_size=5))
    keys = [k for k, _, _ in pairs] or ["UNSET"]
    lines = [style.format(k=k, v=v) for k, v, style in pairs]
    lines += [line.format(k=data.draw(st.sampled_from(keys))) for line in noise]
    lines = data.draw(st.permutations(lines))
    newline = data.draw(st.sampled_from(["\n", "\r\n"]))
    assert _load_from(load, lines, newline) == {k: v for k, v, _ in pairs}
