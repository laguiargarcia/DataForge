"""Which testloop.toml targets a change can affect, as a JSON list on stdout (df-gqke).

Usage (repo root): affected.py [--base-ref REF] [--matrix [--costs FILE]]

With --matrix it prints the CI mutation matrix instead: a list of jobs {"job": j, "items": [...]},
each item one {"module", "shard", "shards"} per shard of each chosen target (`shards` in
testloop.toml, default 1; df-n8ac.32). The items are packed first-fit, heaviest first, into jobs of
at most JOB_SECONDS estimated mutate seconds and JOB_ITEMS items (df-n8ac.39); an item over the cap
runs alone. An item's estimate is its module's measured elapsed (FILE, from scripts/testloop/costs.py)
times its shard's share of the module's function weights, split as `mutate --costs FILE` splits
it; a module without a measure is UNMEASURED_SECONDS per shard.

Without --base-ref every target is printed: push to main, the weekly run and manual runs mutate
everything. With it, the changed files are `git diff --name-only REF...HEAD`, and a target is
kept when one of them is
- the target's module, one of its test files, or a first-party module either of them imports,
  directly or through other first-party modules (imports inside functions included);
- shared by every target: the test loop code that `mutate` runs, the test package setup, the
  install, the workflow's mutation jobs or top-level keys, and testloop.toml outside its
  [targets] tables (df-n8ac.40: the other test loop tools and the other CI jobs cannot move a
  score; their own tests run in the testloop-tools job);
- the target's own table in testloop.toml, when only target tables changed there (df-n8ac.34:
  raising one min_score mutated all 37 targets);
- something the import graph cannot see: a non-Python file, or a deleted Python file, under the
  first-party packages.
When unsure it keeps the target: a needless mutation run only costs minutes. The full run on
main still audits what a PR skipped. The reason for each choice goes to stderr.
"""

import argparse
import ast
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mutate_cmd
from common import ToolError, load_config, load_costs, resolve_tests, shard_count

# Packages whose imports are followed: everything else is third party.
FIRST_PARTY = ("dataforge", "tests")

# The test loop's config: compared table by table against the base (_config_changes).
CONFIG = "testloop.toml"
# A change here can move every target's score.
SHARED = (
    "pyproject.toml",
    "tests/__init__.py",
    "tests/conftest.py",
)
SHARED_DIRS = ("scripts/testloop/",)
# Test loop tools `mutate` never runs: a change to them cannot move a score.
NOT_MUTATE = tuple(f"scripts/testloop/{name}" for name in (
    "affected.py", "gate_cmd.py", "diffcover_cmd.py", "map_cmd.py", "check_cmd.py", "costs.py",
))
# The workflow: compared job by job against the base (_ci_changes).
CI = ".github/workflows/ci.yml"
# Its jobs that pick, mutate and gate the targets.
MUTATION_JOBS = ("mutation-targets", "mutation", "mutation-ok")
# Its top-level keys that no step sees.
CI_INERT_KEYS = ("name", "on", "concurrency")
TOP_KEY = re.compile(r"([A-Za-z_][\w-]*):")
JOB_KEY = re.compile(r" {2}([A-Za-z_][\w-]*):\s*(#.*)?$")

# Packing the matrix into jobs (df-n8ac.39): GitHub Free runs 20 jobs at a time, and each job pays
# about a minute of setup (checkout, uv sync) before its first mutate.
JOB_SECONDS = 150          # estimated mutate seconds that fill a job
UNMEASURED_SECONDS = 60    # the estimate of a shard of a module the costs file does not measure
JOB_ITEMS = 8              # items per job at most: the mutation job in ci.yml has this many cache slots

INIT = "__init__.py"
# A git ref name, never an option: a leading "-" would make git read it as a flag.
REF = re.compile(r"[A-Za-z0-9_.][A-Za-z0-9_./-]*")


def _module_file(repo: Path, dotted: str) -> str | None:
    """Repo-relative file for a first-party dotted name, or None."""
    if dotted.split(".")[0] not in FIRST_PARTY:
        return None
    base = repo.joinpath(*dotted.split("."))
    for candidate in (base.with_suffix(".py"), base / INIT):
        if candidate.is_file():
            return candidate.relative_to(repo).as_posix()
    return None


def _package_inits(rel: str) -> list[str]:
    """The __init__.py files Python runs before it can import `rel`."""
    parts = Path(rel).parts[:-1]
    return [Path(*parts[:i], INIT).as_posix() for i in range(1, len(parts) + 1)]


def _package_of(rel: str) -> list[str]:
    """Dotted parts of the package that `rel` belongs to (relative imports start here)."""
    parts = list(Path(rel).with_suffix("").parts)
    return parts if Path(rel).name == INIT else parts[:-1]


def _from_names(node: ast.ImportFrom, package: list[str]) -> set[str]:
    """Dotted names a `from ... import ...` can load: the module, and each name as a submodule."""
    if node.level:
        base = package[: len(package) - node.level + 1]  # level 1 = this package
        prefix = ".".join([*base, node.module] if node.module else base)
    else:
        prefix = node.module or ""
    return {prefix, *(f"{prefix}.{alias.name}" for alias in node.names)}


def _imported_names(tree: ast.AST, package: list[str]) -> set[str]:
    """Every dotted name the file imports, wherever the import statement is."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names |= _from_names(node, package)
    return names


def _imports(repo: Path, rel: str) -> set[str]:
    """First-party files that `rel` imports directly."""
    try:
        tree = ast.parse((repo / rel).read_text(encoding="utf-8"), filename=rel)
    except (OSError, SyntaxError):
        return set()
    found = set()
    for name in _imported_names(tree, _package_of(rel)):
        # `import a.b.c` runs a, a.b and a.b.c.
        parts = name.split(".")
        found.update(
            path for i in range(1, len(parts) + 1)
            if (path := _module_file(repo, ".".join(parts[:i]))) is not None
        )
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


def _config_changes(head_text: str, base_text: str | None) -> set[str] | None:
    """Targets whose table in testloop.toml differs from the base version, or None when every
    target must run: no readable base, or a key outside [targets] changed (it applies to all)."""
    if base_text is None:
        return None
    try:
        head, base = tomllib.loads(head_text), tomllib.loads(base_text)
    except tomllib.TOMLDecodeError:
        return None
    head_targets, base_targets = head.pop("targets", {}), base.pop("targets", {})
    if head != base:
        return None
    return {module for module, table in head_targets.items() if base_targets.get(module) != table}


def _blocks(text: str, pattern: re.Pattern) -> dict[str, str]:
    """Each key the pattern finds at the start of a line, with every line up to the next one."""
    blocks: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        if match := pattern.match(line):
            current = match.group(1)
            blocks[current] = []
        if current is not None:
            blocks[current].append(line)
    return {key: "\n".join(lines) for key, lines in blocks.items()}


def _score_parts(text: str) -> dict[str, str]:
    """The parts of ci.yml that can move a score: top-level keys a step sees, and the mutation jobs."""
    top = _blocks(text, TOP_KEY)
    jobs = _blocks(top.pop("jobs", ""), JOB_KEY)
    parts = {key: block for key, block in top.items() if key not in CI_INERT_KEYS}
    parts.update((f"jobs.{job}", jobs[job]) for job in MUTATION_JOBS if job in jobs)
    return parts


def _ci_changes(head_text: str | None, base_text: str | None) -> str | None:
    """The first part of ci.yml that changed and can move a score, or None. No base or no head
    (added or deleted): every part."""
    if base_text is None or head_text is None:
        return "added, deleted, or no base to compare"
    head, base = _score_parts(head_text), _score_parts(base_text)
    return next((key for key in sorted(head.keys() | base.keys()) if head.get(key) != base.get(key)), None)


def _shared(repo: Path, rel: str) -> bool:
    if rel in SHARED or _invisible(repo, rel):
        return True
    return rel.startswith(SHARED_DIRS) and rel not in NOT_MUTATE


def select(config, changed: list[str], base_toml: str | None = None,
           base_ci: str | None = None) -> tuple[list[str], dict[str, str]]:
    """(targets to mutate, reason per target, or why it was skipped). `base_toml` and `base_ci` are
    testloop.toml and ci.yml as the base ref has them; without one, a change to that file runs
    every target."""
    repo = config.repo
    shared = [f for f in changed if _shared(repo, f)]
    head_ci = (repo / CI).read_text(encoding="utf-8") if (repo / CI).is_file() else None
    if CI in changed and (part := _ci_changes(head_ci, base_ci)):
        shared.insert(0, f"{CI} ({part})")
    retuned: set[str] = set()
    if CONFIG in changed:
        retuned = _config_changes((repo / CONFIG).read_text(encoding="utf-8"), base_toml)
        if retuned is None:
            shared.insert(0, f"{CONFIG} (outside its target tables, or no base to compare)")
            retuned = set()
    reasons: dict[str, str] = {}
    picked = []
    for module in config.targets:
        if shared:
            picked.append(module)
            reasons[module] = f"shared or unmapped file changed: {shared[0]}"
            continue
        if module in retuned:
            picked.append(module)
            reasons[module] = f"its table in {CONFIG} changed"
            continue
        deps = dependencies(repo, [module, *resolve_tests(config, module)])
        hits = sorted(deps.intersection(changed))
        if hits:
            picked.append(module)
            reasons[module] = f"depends on changed {hits[0]}"
        else:
            reasons[module] = "skipped: no changed file in its module, tests or their imports"
    return picked, reasons


def shard_seconds(config, module: str, n: int, measured: dict | None) -> list[float]:
    """The estimated mutate seconds of each of the module's n shards: its measured elapsed times
    the shard's share of mutate_cmd.cost_weights, partitioned as `mutate --shard I/N --costs` does."""
    if not measured or "elapsed" not in measured:
        return [UNMEASURED_SECONDS] * n
    elapsed = measured["elapsed"]
    if n == 1:
        return [elapsed]
    try:
        weights = mutate_cmd.cost_weights((config.repo / module).read_text(encoding="utf-8"), measured)
    except (OSError, SyntaxError):
        return [elapsed / n] * n
    total = sum(weights.values())
    shards = mutate_cmd.partition(weights, n)
    return [elapsed * sum(weights[key] for key in shard) / total if total else elapsed / n for shard in shards]


def pack(estimated: list[tuple[dict, float]]) -> list[dict]:
    """First-fit decreasing: heaviest item first (ties by module and shard), each into the first job
    it fits without passing JOB_SECONDS or JOB_ITEMS, else a new job. Every item lands in one job."""
    jobs: list[dict] = []
    for item, seconds in sorted(estimated, key=lambda pair: (-pair[1], pair[0]["module"], pair[0]["shard"])):
        job = next((j for j in jobs if j["load"] + seconds <= JOB_SECONDS and len(j["items"]) < JOB_ITEMS), None)
        if job is None:
            job = {"load": 0.0, "items": []}
            jobs.append(job)
        job["load"] += seconds
        job["items"].append(item)
    return [{"job": j, "items": job["items"]} for j, job in enumerate(jobs, 1)]


def matrix(config, picked: list[str], costs: dict[str, dict] | None = None) -> list[dict]:
    """The CI mutation jobs: every shard of each picked target, packed by estimated seconds."""
    estimated = []
    for module in picked:
        n = shard_count(config, module)
        seconds = shard_seconds(config, module, n, (costs or {}).get(module))
        estimated.extend(({"module": module, "shard": i, "shards": n}, seconds[i - 1]) for i in range(1, n + 1))
    jobs = pack(estimated)
    seconds_of = {(item["module"], item["shard"]): seconds for item, seconds in estimated}
    for job in jobs:
        total = sum(seconds_of[i["module"], i["shard"]] for i in job["items"])
        names = ", ".join(f"{i['module']} {i['shard']}/{i['shards']}" for i in job["items"])
        print(f"affected: job {job['job']} (~{total:.0f} s): {names}", file=sys.stderr)
    return jobs


def changed_files(repo: Path, base_ref: str) -> list[str]:
    if not REF.fullmatch(base_ref):
        raise ToolError(f"not a git ref name: {base_ref!r}")
    proc = subprocess.run(
        ["git", "diff", "--name-only", "--end-of-options", f"{base_ref}...HEAD"],
        cwd=repo, capture_output=True, text=True, encoding="utf-8", check=False,
    )
    if proc.returncode != 0:
        raise ToolError(f"git diff against {base_ref} failed: {proc.stderr.strip()}")
    return [line for line in proc.stdout.splitlines() if line]


def base_file(repo: Path, base_ref: str, rel: str) -> str | None:
    """`rel` as the merge base of `base_ref` and HEAD has it (what `git diff REF...HEAD` compares
    against), or None when it did not exist there or git cannot say."""
    if not REF.fullmatch(base_ref):
        raise ToolError(f"not a git ref name: {base_ref!r}")
    base = subprocess.run(
        ["git", "merge-base", "--end-of-options", base_ref, "HEAD"],
        cwd=repo, capture_output=True, text=True, encoding="utf-8", check=False,
    )
    if base.returncode != 0:
        return None
    shown = subprocess.run(
        ["git", "show", f"{base.stdout.strip()}:{rel}"],
        cwd=repo, capture_output=True, text=True, encoding="utf-8", check=False,
    )
    return shown.stdout if shown.returncode == 0 else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="affected")
    parser.add_argument("--base-ref", help="e.g. origin/main; omitted = every target")
    parser.add_argument("--matrix", action="store_true", help="print the CI jobs: every shard of each target, packed")
    parser.add_argument("--costs", type=Path, metavar="FILE",
                        help="with --matrix: estimate each shard from these measured costs (costs.py)")
    args = parser.parse_args(argv)
    try:
        config = load_config(Path.cwd())
        if args.base_ref:
            changed = changed_files(config.repo, args.base_ref)
            base_toml = base_file(config.repo, args.base_ref, CONFIG) if CONFIG in changed else None
            base_ci = base_file(config.repo, args.base_ref, CI) if CI in changed else None
            picked, reasons = select(config, changed, base_toml, base_ci)
        else:
            picked = list(config.targets)
            reasons = dict.fromkeys(picked, "no base ref: every target")
        out = matrix(config, picked, load_costs(args.costs) if args.costs else None) if args.matrix else picked
    except ToolError as exc:
        print(f"affected: {exc}", file=sys.stderr)
        return 2
    for module, reason in reasons.items():
        print(f"affected: {module}: {reason}", file=sys.stderr)
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
