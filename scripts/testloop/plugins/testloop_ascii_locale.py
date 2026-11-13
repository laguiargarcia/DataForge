"""pytest plugin: run the tests with an ASCII locale encoding (df-ahua.26).

Under Linux's UTF-8 locale, open() without `encoding=` reads and writes UTF-8, so a mutant that
turns `encoding="utf-8"` into None, or drops it, behaves exactly like the original and survives
for good. With LC_CTYPE set to "C" for the test session, the default text encoding is ASCII: such
a mutant raises on the first non-ASCII byte a test reads or writes, and dies (Windows' cp1252
catches the same mutants, by misreading the text instead of raising).

Only LC_CTYPE changes, at runtime, inside the pytest session. The interpreter's stdout and file
system encodings are fixed at startup and stay UTF-8. Setting LC_ALL=C in the environment instead
breaks mutmut, which runs pytest in its own process and reads the sources and writes its mutant
files with the locale's encoding. The previous locale comes back when the session ends.

Python's UTF-8 mode ignores the locale (the default from Python 3.15, PEP 686), so the tool runs
the tests with PYTHONUTF8=0, and this plugin stops the run if the encoding is still UTF-8.
"""

import codecs
import locale

_saved: list[str] = []


def pytest_configure():
    _saved.append(locale.setlocale(locale.LC_CTYPE))
    locale.setlocale(locale.LC_CTYPE, "C")
    encoding = locale.getpreferredencoding(False)
    # ASCII on Linux and macOS; Windows takes it from the ANSI code page (cp1252), which also serves.
    if codecs.lookup(encoding).name == "utf-8":
        pytest_unconfigure()
        raise RuntimeError(  # pytest exits 3 (internal error): every testloop command reads it as a tool error
            "testloop: the default encoding stays UTF-8 under the C locale (UTF-8 mode on? run with "
            "PYTHONUTF8=0); encoding= mutants would survive unseen"
        )


def pytest_unconfigure():
    if _saved:
        locale.setlocale(locale.LC_CTYPE, _saved.pop())
