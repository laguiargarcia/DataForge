"""scripts/testloop/plugins/testloop_ascii_locale.py: the ASCII locale of the test loop's runs (df-ahua.26)."""

import codecs
import locale
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "testloop" / "plugins"))

import testloop_ascii_locale as plugin  # the test loop tools are scripts, not an installed package


def _session_encoding() -> str:
    """The default text encoding inside a session, with the locale put back afterwards."""
    before = locale.setlocale(locale.LC_CTYPE)
    plugin.pytest_configure()
    try:
        encoding = codecs.lookup(locale.getpreferredencoding(False)).name
    finally:
        plugin.pytest_unconfigure()
    assert locale.setlocale(locale.LC_CTYPE) == before
    return encoding


def test_the_session_encoding_is_not_utf8():
    assert _session_encoding() != "utf-8"


@pytest.mark.skipif(sys.platform == "win32", reason="Windows takes the encoding from the ANSI code page, not LC_CTYPE")
def test_the_session_encoding_is_ascii_where_testloop_runs():
    assert _session_encoding() == "ascii"


def test_a_locale_that_stays_utf8_stops_the_run(monkeypatch):
    """UTF-8 mode ignores the locale: the run must stop instead of letting encoding mutants survive."""
    before = locale.setlocale(locale.LC_CTYPE)
    monkeypatch.setattr(locale, "getpreferredencoding", lambda do_setlocale=True: "utf-8")
    with pytest.raises(RuntimeError, match="stays UTF-8"):
        plugin.pytest_configure()
    assert locale.setlocale(locale.LC_CTYPE) == before
