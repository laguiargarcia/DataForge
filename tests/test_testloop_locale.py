"""scripts/testloop/plugins/testloop_ascii_locale.py: the ASCII locale of the test loop's runs (df-ahua.26).

The tool runs pytest with PYTHONUTF8=0 (scripts/testloop/common.py, test_env): from Python 3.15
UTF-8 mode is on by default and ignores the locale (PEP 686, df-p0wm). This suite may run with it
on, so the session encoding is measured in a child interpreter started the way the tool starts one.
"""

import locale
import os
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1] / "scripts" / "testloop" / "plugins"
sys.path.insert(0, str(PLUGIN_DIR))

import testloop_ascii_locale as plugin  # the test loop tools are scripts, not an installed package

from scripts.testloop import common

_MEASURE = """
import codecs, locale
import testloop_ascii_locale as plugin
before = locale.setlocale(locale.LC_CTYPE)
plugin.pytest_configure()
try:
    encoding = codecs.lookup(locale.getpreferredencoding(False)).name
finally:
    plugin.pytest_unconfigure()
assert locale.setlocale(locale.LC_CTYPE) == before, "the plugin did not put the locale back"
print(encoding)
"""


def _session_encoding() -> str:
    """The default text encoding inside a testloop session, with UTF-8 mode off as the tool runs it."""
    env = {**os.environ, "PYTHONUTF8": "0", "PYTHONPATH": str(PLUGIN_DIR)}
    done = subprocess.run(
        [sys.executable, "-c", _MEASURE], env=env, capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


def test_the_session_encoding_is_not_utf8():
    assert _session_encoding() != "utf-8"


@pytest.mark.skipif(sys.platform == "win32", reason="Windows takes the encoding from the ANSI code page, not LC_CTYPE")
def test_the_session_encoding_is_ascii_where_testloop_runs():
    assert _session_encoding() == "ascii"


def test_the_tool_turns_utf8_mode_off_even_when_testloop_toml_turns_it_on():
    config = common.Config(repo=PLUGIN_DIR, env={"PYTHONUTF8": "1"})
    assert common.test_env(config, PYTHONUTF8="1")["PYTHONUTF8"] == "0"


def test_a_locale_that_stays_utf8_stops_the_run(monkeypatch):
    """UTF-8 mode ignores the locale: the run must stop instead of letting encoding mutants survive."""
    before = locale.setlocale(locale.LC_CTYPE)
    monkeypatch.setattr(locale, "getpreferredencoding", lambda do_setlocale=True: "utf-8")
    with pytest.raises(RuntimeError, match="stays UTF-8"):
        plugin.pytest_configure()
    assert locale.setlocale(locale.LC_CTYPE) == before
