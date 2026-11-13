"""Test loop CLI. Usage: cli.py <command> <module> [options], or cli.py diff-cover [--compare-branch REF]

Run from the repo root. Exit codes: 0 measured (and, for check and gate, passed);
1 check: tests failed, gate: score below min_score, diff-cover: changed lines not covered;
2 usage or tool error. The module commands print the result JSON path.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import ToolError, clear_result, load_config
import check_cmd
import diffcover_cmd
import gate_cmd
import map_cmd
import mutate_cmd

# command name -> (module with main(config, module, **options) -> Path | (Path, exit_code), option adder)
COMMANDS = {
    "map": (map_cmd, lambda parser: None),
    "check": (check_cmd, lambda parser: None),
    "mutate": (mutate_cmd, lambda parser: parser.add_argument(
        "--budget-seconds", type=float, default=mutate_cmd.DEFAULT_BUDGET_SECONDS,
        help="wall-clock budget for the whole run; exceeded = partial result, exit 2")),
    "gate": (gate_cmd, lambda parser: None),
}
# command name -> (module with main(config, **options) -> exit code, option adder); no module, no result file
REPO_COMMANDS = {
    "diff-cover": (diffcover_cmd, lambda parser: parser.add_argument(
        "--compare-branch", default="origin/main", help="git ref the change is measured against")),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="testloop")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, (_, add_options) in COMMANDS.items():
        p = sub.add_parser(name)
        p.add_argument("module", help="repo-relative path, e.g. dataforge/deploy/plan.py")
        add_options(p)
    for name, (_, add_options) in REPO_COMMANDS.items():
        add_options(sub.add_parser(name))
    return parser


def _repo_command(args: argparse.Namespace) -> int:
    options = {k: v for k, v in vars(args).items() if k != "command"}
    return REPO_COMMANDS[args.command][0].main(load_config(Path.cwd()), **options)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command in REPO_COMMANDS:
            return _repo_command(args)
        handler = COMMANDS[args.command][0]
        clear_result(Path.cwd(), args.command, args.module)
        config = load_config(Path.cwd())
        options = {k: v for k, v in vars(args).items() if k not in ("command", "module")}
        result = handler.main(config, args.module, **options)
    except ToolError as exc:
        print(f"testloop: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # any tool breakage is exit 2; exit 1 is reserved for check, gate and diff-cover failures
        print(f"testloop: unexpected {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    out, code = result if isinstance(result, tuple) else (result, 0)
    print(out)
    return code


if __name__ == "__main__":
    sys.exit(main())
