"""scripts/shellwords.py: how the Bash hooks (session guard, bead check) read a command line into
simple commands (df-pabj)."""

import importlib.util
import shlex
from pathlib import Path

import pytest
from hypothesis import given, strategies as st

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "shellwords.py"
_spec = importlib.util.spec_from_file_location("shellwords", SCRIPT)
shellwords = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(shellwords)


@pytest.mark.parametrize(("command", "expected"), [
    ("a && b || c ; d | e & f", [["a"], ["b"], ["c"], ["d"], ["e"], ["f"]]),
    ("git log > out.txt 2>&1", [["git", "log"]]),
    ("git commit -m 'a -> b' && git pull", [["git", "commit", "-m", "a -> b"], ["git", "pull"]]),
    ("git commit -m 'x\ny' 2> e", [["git", "commit", "-m", "x\ny"]]),
    ("(cd x; git pull)", [["cd", "x"], ["git", "pull"]]),
    ("a \\\n b\nc", [["a", "b"], ["c"]]),
])
def test_split_commands(command, expected):
    assert shellwords.split_commands(command) == expected


def test_an_unreadable_line_raises_value_error():
    with pytest.raises(ValueError):
        shellwords.split_commands("git commit -m 'unbalanced")


# Excluded: a backslash-newline (read as a line continuation) and an argument made only of operator
# characters (shlex reads it as the operator; split_commands says why that is on the safe side).
@given(st.text(st.sampled_from("<>&|;()-'\" \t\nab") | st.characters())
       .filter(lambda a: "\\\n" not in a and not (a and set(a) <= set(shellwords.OPERATOR_CHARS))))
def test_any_quoted_argument_reaches_the_hooks_unchanged(arg):
    assert shellwords.split_commands(f"git commit -m {shlex.quote(arg)} ; git pull") == [
        ["git", "commit", "-m", arg], ["git", "pull"]]


@pytest.mark.parametrize(("words", "expected"), [
    (["FOO=1", "BAR=2", "git", "pull"], ["git", "pull"]),
    (["env", "-i", "A=b", "nohup", "git", "pull"], ["git", "pull"]),
    (["then", "!", "bd", "close"], ["bd", "close"]),
    (["git", "-c", "x=y", "pull"], ["git", "-c", "x=y", "pull"]),
    ([], []),
])
def test_strip_prefix(words, expected):
    assert shellwords.strip_prefix(words) == expected
