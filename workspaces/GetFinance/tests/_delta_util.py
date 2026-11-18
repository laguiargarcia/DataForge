# tests/_delta_util.py — the harness: where the code under test and the real data are, plus
# seed/read helpers for Delta tables in tmp_path.
import os
import subprocess
import sys
import polars as pl
from pathlib import Path

# The code under test is always this checkout's (the main one or a worktree): each checkout tests
# itself (ADR 0004, DataForge df-rt6w).
ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
FUNCTIONS = SCRIPTS / "functions" / "functions.py"


def exec_functions(ns, path=FUNCTIONS):
    """Run functions.py into `ns` and return it. The source is compiled under its own file name, so
    coverage counts the lines the tests run (an exec of the bare text reports them as <string>, and
    functions.py read as untested on SonarQube Cloud, DataForge df-mpih)."""
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), ns)
    return ns


def _main_checkout(root: Path) -> Path:
    """`root`'s own folder in the main checkout: the workspace is a folder of the DataForge repo, and
    a worktree has no data/ (gitignored) while the main checkout may."""
    try:
        common, top = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir", "--show-toplevel"],
            cwd=root, capture_output=True, text=True, check=True).stdout.splitlines()
    except (OSError, subprocess.CalledProcessError, ValueError):
        return root
    if not Path(common).is_dir():
        return root
    return Path(common).parent / root.resolve().relative_to(Path(top).resolve())


def real_data_dir(root: Path, environ) -> Path:
    """GETFINANCE_DATA when it is set: the data/ directory of the workspace that holds the real data
    (the dev clone on the Linux disk has none, DataForge df-aef7.1). Otherwise the main checkout's
    data/, which may not exist, and then the `integration` tests skip."""
    if environ.get("GETFINANCE_DATA"):
        return Path(environ["GETFINANCE_DATA"])
    return _main_checkout(root) / "data"


# data/ is gitignored, so a worktree and CI have none. Tests marked `integration` read the real data
# and never write it; without it they skip.
REAL_DATA = real_data_dir(ROOT, os.environ)


def seed(tmp_path, layer, table, df):
    df.write_delta(str(tmp_path / layer / table), mode="overwrite",
                   delta_write_options={"schema_mode": "overwrite"})


def read(tmp_path, layer, table):
    return pl.read_delta(str(tmp_path / layer / table))


def run_script(layer_dir, name, argv, tmp_path, overrides=None):
    script_path = SCRIPTS / layer_dir / f"{name}.py"
    source = "\n".join(
        line for line in script_path.read_text().splitlines()
        if not line.strip().startswith("exec(open(")
    )
    ns = {"__file__": str(script_path)}
    exec_functions(ns)
    ns["_connection_root"] = lambda lyr: tmp_path / lyr
    if overrides:
        ns.update(overrides)
    old_argv, sys.argv = sys.argv, ["script"] + argv
    try:
        exec(source, ns)
    except SystemExit:
        pass
    finally:
        sys.argv = old_argv
    return ns
