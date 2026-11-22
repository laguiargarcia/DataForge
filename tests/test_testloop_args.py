"""scripts/testloop/common.py pytest_args: what a target changes in the pytest arguments (df-mbi7), and the
plugin its `bare_import` turns on."""

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "testloop"))

from common import Config, pytest_args  # the test loop tools are scripts, not an installed package

PLUGINS = Path(__file__).resolve().parents[1] / "scripts" / "testloop" / "plugins"


def test_a_targets_check_args_replace_the_global_ones_and_bare_import_adds_its_plugin(tmp_path):
    config = Config(repo=tmp_path, pytest_args=["-x"], check_args=["--g"], targets={
        "a/b/mod.py": {"check_args": []}, "a/c.py": {"bare_import": True}, "a/d.py": {}})
    plugin = ["-p", "testloop_ascii_locale"]
    assert pytest_args(config, "a/b/mod.py") == ["-x", *plugin]
    assert pytest_args(config, "a/c.py") == ["-x", *plugin, "-p", "testloop_bare_imports", "--bare-import", "a/c.py", "--g"]
    assert pytest_args(config, "a/d.py") == pytest_args(config) == ["-x", *plugin, "--g"]


def test_bare_import_plugin_registers_the_dotted_module_under_its_bare_name(tmp_path):
    (tmp_path / "pkg" / "sub").mkdir(parents=True)
    (tmp_path / "pkg" / "sub" / "mod.py").write_text("X = 1\n")
    (tmp_path / "test_it.py").write_text("import mod\n\ndef test_it():\n    assert mod.__name__ == 'pkg.sub.mod'\n")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-p", "testloop_bare_imports",
         "--bare-import", "pkg/sub/mod.py", "test_it.py"],
        cwd=tmp_path, env={**os.environ, "PYTHONPATH": str(PLUGINS)}, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stdout
