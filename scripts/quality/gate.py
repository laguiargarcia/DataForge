"""Quality gate: lint, dead-code and import-contract findings must not grow.

Existing findings live in a baseline, counted per file and rule. `check` fails when a count rises
above the baseline. When a count falls, `check` lowers the baseline itself, so it only ever goes
down without anyone remembering to do it; a new file starts at zero. Raising it takes
`update --allow-increase`, and CI fails any raise against the base branch (`check --base-ref`).
A legitimate exception is a suppression comment with a reason, on the line itself:
`# noqa: PLC0415 -- lazy, keeps CLI startup fast`.

Usage:
  gate.py check                  full gate: ruff, vulture, import contracts, project rules
  gate.py check FILE...          fast gate for these files: ruff and project rules only
  gate.py check --full FILE...   full gate, judging only these files (others' work in progress is ignored)
  gate.py check --base-ref REF   also fail if the committed baseline rose against git REF
  gate.py check --stage          also `git add` the published baseline after lowering it (pre-commit)
  gate.py update                 lower the baseline where findings were fixed (check does it too)
  gate.py update --allow-increase   also accept new findings (on purpose only; CI flags it)

See docs/quality-gate.md.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
import sysconfig
import tokenize
from collections import defaultdict
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
BASELINE = HERE / "baseline.json"
# Findings in gitignored (private) files: kept out of the published baseline so that their paths
# never leave this machine.
LOCAL_BASELINE = HERE / "baseline.local.json"
REQUIREMENTS = HERE / "requirements.txt"
# Gitignored folders that still hold code worth gating: private workspaces and their tests.
PRIVATE_ROOTS = ("workspaces", "tests")
SKIP_PARTS = {"node_modules", ".venv", "venv", "__pycache__"}
TESTS = "tests/"
# Only compat may spell a SQLite URL (docs/subsystems/compat.md); this file names it to find it.
SQLITE_URL_HOMES = {"dataforge/compat.py", Path(__file__).resolve().relative_to(ROOT).as_posix()}
# Findings that only the full gate produces; the fast gate leaves their counts alone.
FULL_ONLY_PREFIXES = ("vulture:", "import-contract")

SUPPRESSION = re.compile(r"#\s*(?:noqa\b|ruff:\s*ignore\b|type:\s*ignore\b|pragma:\s*no\s*cover\b)(?P<rest>.*)", re.IGNORECASE)
REASON = re.compile(r"\s--\s*\S")
VULTURE_LINE = re.compile(r"^(?P<file>.+?):(?P<line>\d+): (?P<msg>.+)$")
# A git ref name, never an option: a leading "-" would make git read it as a flag (as in affected.py).
REF = re.compile(r"[A-Za-z0-9_.][A-Za-z0-9_./-]*")
STRING_TOKENS = {tokenize.STRING} | {getattr(tokenize, n) for n in ("FSTRING_MIDDLE", "TSTRING_MIDDLE") if hasattr(tokenize, n)}


@dataclass(frozen=True)
class Finding:
    file: str
    code: str
    line: int
    message: str


Counts = dict[str, dict[str, int]]


# ---------------------------------------------------------------------------- files


def _git_files(*args: str) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z", *args], cwd=ROOT, capture_output=True, check=True, encoding="utf-8"
    ).stdout
    return [p for p in out.split("\0") if p.endswith(".py") and not SKIP_PARTS & set(p.split("/")) and (ROOT / p).is_file()]


def public_files() -> list[str]:
    return sorted(set(_git_files("--cached", "--others", "--exclude-standard")))


def private_files() -> list[str]:
    roots = [r for r in PRIVATE_ROOTS if (ROOT / r).is_dir()]
    return sorted(set(_git_files("--others", "--ignored", "--exclude-standard", "--", *roots))) if roots else []


def _relative(path: str) -> str | None:
    p = Path(path)
    p = (p if p.is_absolute() else Path.cwd() / p).resolve()
    try:
        return p.relative_to(ROOT).as_posix()
    except ValueError:
        return None


# ---------------------------------------------------------------------------- tools


def _check_tool_versions() -> None:
    wrong = []
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        name, sep, wanted = line.split("#")[0].strip().partition("==")
        if not sep:
            continue
        try:
            have = metadata.version(name)
        except metadata.PackageNotFoundError:
            have = "missing"
        if have != wanted:
            wrong.append(f"{name} {have} (wanted {wanted})")
    if wrong:
        sys.exit(
            "quality gate: tool versions differ from scripts/quality/requirements.txt, so findings would not "
            f"match the baseline: {', '.join(wrong)}.\nInstall them: {sys.executable} -m pip install -r {REQUIREMENTS.relative_to(ROOT)}"
        )


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, check=False, encoding="utf-8")


def ruff(files: list[str]) -> list[Finding]:
    if not files:
        return []
    r = _run([sys.executable, "-m", "ruff", "check", "--force-exclude", "--exit-zero", "--output-format", "json", "--", *files])
    if r.returncode != 0:
        sys.exit(f"quality gate: ruff failed\n{r.stderr}")
    found = []
    for d in json.loads(r.stdout or "[]"):
        rel = _relative(d["filename"]) or d["filename"]
        found.append(Finding(rel, d.get("code") or "syntax-error", (d.get("location") or {}).get("row") or 0, d["message"]))
    return found


def _vulture_code(message: str) -> str:
    kind = message.split(" '")[0]
    if kind.startswith("invalid syntax"):
        return "vulture:parse-error"
    if kind.startswith("unreachable code"):
        return "vulture:unreachable-code"
    return "vulture:" + kind.replace(" ", "-")


def vulture(files: list[str]) -> list[Finding]:
    """Whole-program dead code over one group of files that see each other's names."""
    if not files:
        return []
    r = _run([sys.executable, "-m", "vulture", "--", *files])
    if r.returncode not in (0, 3):  # 3: dead code found
        sys.exit(f"quality gate: vulture failed\n{r.stdout}{r.stderr}")
    found = []
    for line in (r.stdout + r.stderr).splitlines():
        m = VULTURE_LINE.match(line.strip())
        if m:
            rel = _relative(m["file"]) or m["file"]
            found.append(Finding(rel, _vulture_code(m["msg"]), int(m["line"]), m["msg"]))
    return found


def import_contracts() -> list[Finding]:
    lint_imports = Path(sysconfig.get_path("scripts")) / ("lint-imports.exe" if sys.platform == "win32" else "lint-imports")
    r = _run([str(lint_imports), "--no-cache"])
    if r.returncode == 0:
        return []
    report = (r.stdout + r.stderr).split("Broken contracts")[-1].strip()
    return [Finding("pyproject.toml", "import-contract", 0, "an import contract is broken:\n" + report)]


def project_rules(files: list[str]) -> list[Finding]:
    """DF001 suppression without a reason; DF002 SQLite URL spelled outside dataforge.compat."""
    found = []
    for rel in files:
        try:
            tokens = list(tokenize.generate_tokens(io.StringIO((ROOT / rel).read_text(encoding="utf-8")).readline))
        except (tokenize.TokenError, SyntaxError, UnicodeDecodeError):
            continue  # ruff reports the syntax error
        sqlite_allowed = rel in SQLITE_URL_HOMES or rel.startswith(TESTS)
        found += [f for tok in tokens if (f := _token_rule(rel, tok, sqlite_allowed)) is not None]
    return found


def _token_rule(rel: str, tok: tokenize.TokenInfo, sqlite_allowed: bool) -> Finding | None:
    """The project-rule finding one token breaks, if any."""
    if tok.type == tokenize.COMMENT:
        m = SUPPRESSION.search(tok.string)
        if m and not REASON.search(m["rest"]):
            return Finding(rel, "DF001", tok.start[0], "suppression without a reason: end it with ' -- why'")
    elif tok.type in STRING_TOKENS and not sqlite_allowed and "sqlite:///" in tok.string:
        return Finding(rel, "DF002", tok.start[0], "SQLite URL spelled inline: use dataforge.compat.db_url()")
    return None


def collect_full(public: list[str], private: list[str]) -> list[Finding]:
    every = public + private
    found = ruff(every) + project_rules(every) + import_contracts()
    # Tests count as callers, but their own findings are fixtures and mocks, not dead code.
    found += [f for f in vulture(public) if not f.file.startswith(TESTS)]
    workspaces = defaultdict(list)
    for rel in private:
        if rel.startswith("workspaces/"):
            workspaces[rel.split("/")[1]].append(rel)
    for group in workspaces.values():  # each private workspace sees only its own functions.py
        found += vulture(group)
    return found


# ---------------------------------------------------------------------------- baseline


def count(findings: list[Finding]) -> Counts:
    out: Counts = defaultdict(lambda: defaultdict(int))
    for f in findings:
        out[f.file][f.code] += 1
    return {file: dict(codes) for file, codes in out.items()}


def load(path: Path) -> Counts:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def merged(*parts: Counts) -> Counts:
    out: Counts = {}
    for part in parts:
        for file, codes in part.items():
            out.setdefault(file, {}).update(codes)
    return out


def _pairs(counts: Counts) -> dict[tuple[str, str], int]:
    return {(file, code): n for file, codes in counts.items() for code, n in codes.items()}


def increases(old: Counts, new: Counts) -> list[tuple[str, str, int, int]]:
    before = _pairs(old)
    return sorted((f, c, before.get((f, c), 0), n) for (f, c), n in _pairs(new).items() if n > before.get((f, c), 0))


def decreases(old: Counts, new: Counts) -> list[tuple[str, str, int, int]]:
    after = _pairs(new)
    return sorted((f, c, n, after.get((f, c), 0)) for (f, c), n in _pairs(old).items() if after.get((f, c), 0) < n)


def write(path: Path, counts: Counts) -> None:
    data = {file: dict(sorted(codes.items())) for file, codes in sorted(counts.items()) if codes}
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _git_show(ref: str, rel: str) -> str | None:
    # Checked here as well as in argparse: check() can be called without main(), and the check
    # has to sit next to the git call for Sonar to see it (pythonsecurity:S8705, df-kza2).
    if not REF.fullmatch(ref):
        raise ValueError(f"not a git ref name: {ref!r}")
    r = _run(["git", "show", "--end-of-options", f"{ref}:{rel}"])
    return r.stdout if r.returncode == 0 else None


# ---------------------------------------------------------------------------- commands


def _report_rises(rises: list[tuple[str, str, int, int]], findings: list[Finding]) -> None:
    by_key = defaultdict(list)
    for f in findings:
        by_key[(f.file, f.code)].append(f)
    print("New findings (counted per file and rule; the lines below include the old ones):")
    for file, code, before, now in rises:
        print(f"\n  {file}  {code}: {before} -> {now}")
        for f in sorted(by_key[(file, code)], key=lambda f: f.line):
            print(f"    line {f.line}: {f.message}")
    print(
        "\nFix them. If one is a deliberate exception, suppress it on its line with a reason, e.g."
        "\n  # noqa: PLC0415 -- lazy import keeps CLI startup fast"
        "\nFor dead code that a framework calls, add the name to [tool.vulture] in pyproject.toml."
    )


def _only(counts: Counts, files: set[str], skip_full_only: bool = False) -> Counts:
    return {
        f: {c: n for c, n in counts.get(f, {}).items() if not (skip_full_only and c.startswith(FULL_ONLY_PREFIXES))}
        for f in files
    }


def _judge(paths: list[str], full: bool) -> tuple[list[Finding], Counts, list, list]:
    """Findings, their counts, and the counts that rose or fell against the baseline.

    No paths: the full gate over everything. Paths: only those files are judged; the fast gate
    runs ruff and project rules alone, `full` runs everything (dead code needs the whole program).
    """
    public, private = public_files(), private_files()
    baseline = merged(load(BASELINE), load(LOCAL_BASELINE))
    if not paths:
        findings = collect_full(public, private)
        current = count(findings)
        return findings, current, increases(baseline, current), decreases(baseline, current)
    scope = set(public) | set(private)
    files = {r for r in map(_relative, paths) if r in scope}
    if full:
        findings = collect_full(public, private)
        judged = files | {"pyproject.toml"}  # broken import contracts are reported there
        old, now = _only(baseline, judged), _only(count(findings), judged)
        return findings, now, increases(old, now), decreases(old, now)
    findings = ruff(sorted(files)) + project_rules(sorted(files))
    old, now = _only(baseline, files, skip_full_only=True), _only(count(findings), files)
    return findings, now, increases(old, now), decreases(old, now)


def _lower(falls: list[tuple[str, str, int, int]], stage: bool) -> None:
    """Write the fallen counts into whichever baseline holds them; never raises a count."""
    for path in (BASELINE, LOCAL_BASELINE):
        data = load(path)
        hits = [(f, c, now) for f, c, _before, now in falls if c in data.get(f, {}) and now < data[f][c]]
        if not hits:
            continue
        for file, code, now in hits:
            if now:
                data[file][code] = now
            else:
                del data[file][code]
        write(path, data)
        if stage and path == BASELINE:
            _run(["git", "add", str(path)])
    print("baseline lowered:")
    for file, code, before, now in falls:
        print(f"  {file}  {code}: {before} -> {now}")


def _baseline_raised(base_ref: str) -> bool:
    old = _git_show(base_ref, BASELINE.relative_to(ROOT).as_posix())
    if old is None:
        print(f"\n(no baseline at {base_ref}; skipping the raise check)")
        return False
    raised = increases(json.loads(old), load(BASELINE))
    if raised:
        print(f"\nThe committed baseline was raised against {base_ref}. It only goes down:")
        for file, code, before, now in raised:
            print(f"  {file}  {code}: {before} -> {now}")
    return bool(raised)


def check(paths: list[str], base_ref: str | None, full: bool, stage: bool = False) -> int:
    _check_tool_versions()
    findings, current, rises, falls = _judge(paths, full)
    if falls:
        _lower(falls, stage)
    if rises:
        _report_rises(rises, findings)
    raised = bool(base_ref) and _baseline_raised(base_ref)
    if rises or raised:
        why = [f"{len(rises)} file/rule counts above the baseline"] * bool(rises) + ["baseline raised"] * raised
        print(f"\nquality gate: failed ({', '.join(why)})")
        return 1
    print(f"quality gate: ok ({sum(sum(c.values()) for c in current.values())} findings, none new)")
    return 0


def update(allow_increase: bool) -> int:
    _check_tool_versions()
    public, private = public_files(), private_files()
    findings = collect_full(public, private)
    broken = [f for f in findings if f.code == "import-contract"]
    if broken:
        print(broken[0].message)
        print("\nImport contracts cannot be baselined: fix the import, or add an ignore_imports entry with a reason.")
        return 1
    current = count(findings)
    baseline = merged(load(BASELINE), load(LOCAL_BASELINE))
    rises = increases(baseline, current)
    accepted: Counts = current
    if not allow_increase:
        # Lower what fell; a count above the baseline keeps its old value, so check still fails on it.
        old = _pairs(baseline)
        accepted = defaultdict(dict)
        for (file, code), n in _pairs(current).items():
            if min(n, old.get((file, code), 0)):
                accepted[file][code] = min(n, old[(file, code)])
    shared = set(public)
    write(BASELINE, {f: c for f, c in accepted.items() if f in shared})
    write(LOCAL_BASELINE, {f: c for f, c in accepted.items() if f not in shared})
    print(f"baseline updated: {sum(sum(c.values()) for c in accepted.values())} findings")
    if rises and not allow_increase:
        print(f"{len(rises)} file/rule counts are above the baseline and were not accepted; check reports them.")
    return 0


def _ref_name(value: str) -> str:
    if not REF.fullmatch(value):
        raise argparse.ArgumentTypeError(f"not a git ref name: {value!r}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p_check = sub.add_parser("check", help="fail on findings above the baseline")
    p_check.add_argument("files", nargs="*", help="limit to these files (fast: ruff and project rules)")
    p_check.add_argument("--full", action="store_true", help="with FILE...: run the full gate, judge only these files")
    p_check.add_argument("--base-ref", type=_ref_name, help="also fail if the committed baseline rose against this git ref")
    p_check.add_argument("--stage", action="store_true", help="git add the published baseline after lowering it")
    p_update = sub.add_parser("update", help="rewrite the baseline from the current findings")
    p_update.add_argument("--allow-increase", action="store_true", help="accept counts above the baseline")
    args = parser.parse_args()
    if args.command == "check":
        return check(args.files, args.base_ref, args.full, args.stage)
    return update(args.allow_increase)


if __name__ == "__main__":
    sys.exit(main())
