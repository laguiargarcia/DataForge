"""Which testloop.toml targets a change can affect, as a JSON list on stdout (df-gqke).

Usage (repo root): affected.py [--base-ref REF]

Without --base-ref every target is printed: push to main, the weekly run and manual runs mutate
everything. With it, the changed files are `git diff --name-only REF...HEAD`, and a target is
kept when one of them is
- the target's module, one of its test files, or a first-party module either of them imports,
  directly or through other first-party modules (imports inside functions included);
- shared by every target: testloop.toml, the test loop tools, the test package setup, the
  install, this workflow;
- something the import graph cannot see: a non-Python file, or a deleted Python file, under the
  first-party packages.
When unsure it keeps the target: a needless mutation run only costs minutes. The full run on
main still audits what a PR skipped. The reason for each choice goes to stderr.
"""

import argparse
import ast
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import ToolError, load_config, resolve_tests

# Packages whose imports are followed: everything else is third party.
FIRST_PARTY = ("dataforge", "tests")

# A change here can move every target's score.
SHARED = (
    "testloop.toml",
    "pyproject.toml",
    "tests/__init__.py",
    "tests/conftest.py",
    ".github/workflows/ci.yml",
)
SHARED_DIRS = ("scripts/testloop/",)


def _module_file(repo: Path, dotted: str) -> str | None:
    """Repo-relative file for a first-party dotted name, or None."""
    if dotted.split(".")[0] not in FIRST_PARTY:
        return None
    base = repo.joinpath(*dotted.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate.relative_to(repo).as_posix()
    return None


def _package_inits(rel: str) -> list[str]:
    """The __init__.py files Python runs before it can import `rel`."""
    parts = Path(rel).parts[:-1]
    return [Path(*parts[:i], "__init__.py").as_posix() for i in range(1, len(parts) + 1)]


def _imports(repo: Path, rel: str) -> set[str]:
    """First-party files that `rel` imports directly (every import statement in the file)."""
    try:
        tree = ast.parse((repo / rel).read_text(encoding="utf-8"), filename=rel)
    except (OSError, SyntaxError):
        return set()
    package = list(Path(rel).with_suffix("").parts)
    if Path(rel).name != "__init__.py":
        package = package[:-1]
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - node.level + 1]  # level 1 = this package
                prefix = ".".join([*base, node.module] if node.module else base)
            else:
                prefix = node.module or ""
            names.add(prefix)
            # `from pkg import mod` imports pkg/mod.py when mod is a module.
            names.update(f"{prefix}.{alias.name}" for alias in node.names)
    found = set()
    for name in names:
        # `import a.b.c` runs a, a.b and a.b.c.
        parts = name.split(".")
        for i in range(1, len(parts) + 1):
            if (path := _module_file(repo, ".".join(parts[:i]))) is not None:
                found.add(path)
    return found


def dependencies(repo: Path, roots: list[str]) -> set[str]:
    """`roots` plus every first-party file they import, transitively."""
    seen: set[str] = set()
    todo = list(roots)
    while todo:
        rel = todo.pop()
        if rel in seen:
            continue
        seen.add(rel)
        todo.extend(_package_inits(rel))
        todo.extend(_imports(repo, rel) - seen)
    return {rel for rel in seen if (repo / rel).is_file()}


def _invisible(repo: Path, rel: str) -> bool:
    """A change the import graph cannot see: data files, or Python files that are gone."""
    if rel.split("/")[0] not in FIRST_PARTY:
        return False
    return not rel.endswith(".py") or not (repo / rel).is_file()


def select(config, changed: list[str]) -> tuple[list[str], dict[str, str]]:
    """(targets to mutate, reason per target, or why it was skipped)."""
    repo = config.repo
    shared = [f for f in changed if f in SHARED or f.startswith(SHARED_DIRS) or _invisible(repo, f)]
    reasons: dict[str, str] = {}
    picked = []
    for module in config.targets:
        if shared:
            picked.append(module)
            reasons[module] = f"shared or unmapped file changed: {shared[0]}"
            continue
        deps = dependencies(repo, [module, *resolve_tests(config, module)])
        hits = sorted(deps.intersection(changed))
        if hits:
            picked.append(module)
            reasons[module] = f"depends on changed {hits[0]}"
        else:
            reasons[module] = "skipped: no changed file in its module, tests or their imports"
    return picked, reasons


def changed_files(repo: Path, base_ref: str) -> list[str]:
    proc = subprocess.run(
        ["git", "diff", "--name-only", f"{base_ref}...HEAD"],
        cwd=repo, capture_output=True, text=True, encoding="utf-8", check=False,
    )
    if proc.returncode != 0:
        raise ToolError(f"git diff against {base_ref} failed: {proc.stderr.strip()}")
    return [line for line in proc.stdout.splitlines() if line]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="affected")
    parser.add_argument("--base-ref", help="e.g. origin/main; omitted = every target")
    args = parser.parse_args(argv)
    try:
        config = load_config(Path.cwd())
        if args.base_ref:
            picked, reasons = select(config, changed_files(config.repo, args.base_ref))
        else:
            picked = list(config.targets)
            reasons = dict.fromkeys(picked, "no base ref: every target")
    except ToolError as exc:
        print(f"affected: {exc}", file=sys.stderr)
        return 2
    for module, reason in reasons.items():
        print(f"affected: {module}: {reason}", file=sys.stderr)
    print(json.dumps(picked))
    return 0


if __name__ == "__main__":
    sys.exit(main())
