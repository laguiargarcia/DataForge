"""pytest plugin: load a script under its dotted name too, for tests that import it by its bare name
(df-mbi7).

mutmut keys each mutant by the module's dotted path (workspaces.GetFinance.scripts.report.x) and records
a trampoline hit under the name the test imported (x). The GetFinance tests put scripts/report on
sys.path and `import x`, so no hit matches a mutant and mutmut stops. With
`--bare-import workspaces/GetFinance/scripts/report/x.py` this plugin puts that folder on sys.path,
imports the file by its dotted name and registers the module as `x` too: the tests' own import then
finds the same module, whose functions record the key mutmut expects.
"""

import importlib
import sys
from pathlib import Path


def pytest_addoption(parser):
    parser.addoption("--bare-import", action="append", default=[], metavar="PATH",
                     help="a script that the tests import by its bare name (repo-relative path)")


def pytest_configure(config):
    for rel in config.getoption("--bare-import"):
        path = Path(rel)
        sys.path.insert(0, str(path.parent.resolve()))
        sys.modules[path.stem] = importlib.import_module(".".join(path.with_suffix("").parts))
