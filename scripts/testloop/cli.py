"""Test loop CLI. Usage: cli.py <command> <module> [options]

Run from the repo root. Exit codes: 0 measured (and, for check and gate, passed);
1 check: tests failed, gate: score below min_score; 2 usage or tool error. Prints the result JSON path.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import ToolError, clear_result, load_config  # noqa: E402
import check_cmd  # noqa: E402
import gate_cmd  # noqa: E402
import map_cmd  # noqa: E402
import mutate_cmd  # noqa: E402

# command name -> (module with main(config, module, **options) -> Path | (Path, exit_code), option adder)
COMMANDS = {
    "map": (map_cmd, lambda parser: None),
    "check": (check_cmd, lambda parser: None),
    "mutate": (mutate_cmd, lambda parser: parser.add_argument(
        "--budget-seconds", type=float, default=mutate_cmd.DEFAULT_BUDGET_SECONDS,
        help="wall-clock budget for the whole run; exceeded = partial result, exit 2")),
    "gate": (gate_cmd, lambda parser: None),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="testloop")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, (_, add_options) in COMMANDS.items():
        p = sub.add_parser(name)
        p.add_argument("module", help="repo-relative path, e.g. dataforge/deploy/plan.py")
        add_options(p)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handler = COMMANDS[args.command][0]
    try:
        clear_result(Path.cwd(), args.command, args.module)
        config = load_config(Path.cwd())
        options = {k: v for k, v in vars(args).items() if k not in ("command", "module")}
        result = handler.main(config, args.module, **options)
    except ToolError as exc:
        print(f"testloop: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # any tool breakage is exit 2; exit 1 is reserved for check and gate failures
        print(f"testloop: unexpected {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    out, code = result if isinstance(result, tuple) else (result, 0)
    print(out)
    return code


if __name__ == "__main__":
    sys.exit(main())
