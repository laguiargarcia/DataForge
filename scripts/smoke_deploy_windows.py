"""Deploy path-guard smoke on native Windows (df-886.21, CI job `windows-smoke`, df-llmm).

Run with the Windows interpreter from the repo root: `python scripts/smoke_deploy_windows.py`
(with the package importable: installed, or PYTHONPATH=<repo root>). `--keep` leaves the temp dir.

Runs the deploy facade (dataforge.deploy.service: build/apply/rollback, the same calls the CLI and
the API make) with the Windows interpreter on NTFS. First proves the premises (Windows really does
rewrite these spellings), then checks that each guard holds: nothing lands outside the target, the
ledger is never written by a pair, .env / data/ / ignored files / active triggers survive prune.
Exit 0 only if every check passes; 2 if not run on Windows. Linux tests can't stand in for this:
POSIX never drops a trailing dot, never reads ':' as a stream and never ignores case, so a guard
that is wrong there still passes them (the code before PR #12 failed 19 of these checks here).
"""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import warnings
from pathlib import Path

from dataforge.deploy import service
from dataforge.deploy.models import Action, PairRule, PipelineModel
from dataforge.deploy.service import DeployServiceError

warnings.simplefilter("ignore")   # the root-skip / empty-source warnings are asserted by unit tests
RESULTS: list[tuple[str, bool, str]] = []
# Paths and globs several scenarios share (Sonar python:S1192).
WORKSPACE_YAML = "workspace.yaml"
SCRIPTS_GLOB = "scripts/**"
JOBS_GLOB = "jobs/**"
JOB_A = "jobs/a/job.yaml"          # S2: in sync between Dev and Prod
JOB_OLD = "jobs/old/job.yaml"      # S2: only in Prod, so prune deletes it
JOBS_A_YAML = "jobs/a.yaml"        # S3/S4: in sync between Dev and Prod


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))


def plant(root: Path, files: dict[str, bytes]) -> None:
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


def snapshot(root: Path) -> dict[str, str]:
    """Every file under root (real on-disk names), with its content hash."""
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            p = Path(dirpath, f)
            out[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def pipeline(rules: list[tuple[str, str]], prune: bool = True) -> PipelineModel:
    return PipelineModel(target="Prod", prune=prune,
                         pairs=[PairRule(source=s, target=t) for s, t in rules])


def fresh(base: Path, name: str) -> tuple[Path, Path, Path]:
    """A new <name>/{Dev,Prod,Outside}, with Prod holding a real ledger from one prior deploy."""
    root = base / name
    dev, prod, outside = root / "Dev", root / "Prod", root / "Outside"
    plant(outside, {"victim.txt": b"must survive"})
    plant(dev, {WORKSPACE_YAML: b"name: Dev\n", "scripts/seed.py": b"seed"})
    plant(prod, {WORKSPACE_YAML: b"name: Prod\n"})
    seed = pipeline([(SCRIPTS_GLOB, "scripts")], prune=False)
    service.apply(dev, prod, service.build(dev, seed, "seed", prod), None)
    return dev, prod, outside


def try_deploy(dev: Path, prod: Path, rules, prune=True):
    """build + apply; returns (plan or None, error kind or None)."""
    try:
        plan = service.build(dev, pipeline(rules, prune), "p", prod)
    except DeployServiceError as e:
        return None, e.kind
    except Exception as e:  # noqa: BLE001 -- a planner crash is a failure the smoke reports
        return None, f"CRASH in build: {type(e).__name__}"
    try:
        service.apply(dev, prod, plan, None)
    except Exception as e:  # noqa: BLE001 -- an apply crash is a failure the smoke reports
        return plan, f"CRASH in apply: {type(e).__name__}"
    return plan, None


def premises(base: Path) -> None:
    """Premises: Windows rewrites these spellings (why the guards exist)."""
    _dev, prod, outside = fresh(base, "premises")
    ledger = prod / "deploy" / ".ledger"
    check("ledger exists after a real deploy", ledger.is_dir())
    check("premise: 'deploy./.ledger' is the ledger", os.path.isdir(str(prod) + r"\deploy.\.ledger"))
    check("premise: 'deploy/.ledger ' is the ledger", os.path.isdir(str(prod) + "\\deploy\\.ledger "))
    check("premise: '.ledger::$INDEX_ALLOCATION' is the ledger",
          os.path.isdir(str(prod) + r"\deploy\.ledger::$INDEX_ALLOCATION"))
    check("premise: 'Deploy\\.LEDGER' is the ledger (NTFS ignores case)",
          os.path.samefile(str(prod) + r"\Deploy\.LEDGER", ledger))
    check("premise: '..\\Outside' from Prod is the sibling folder",
          os.path.samefile(str(prod) + r"\..\Outside", outside))


def s1_escaping_targets(base: Path) -> None:
    """S1: targets that would leave the workspace or reach the ledger."""
    dev, prod, outside = fresh(base, "s1")
    plant(dev, {"scripts/evil.py": b"evil"})
    for target in ["deploy./.ledger", "deploy/.ledger ", "deploy/.ledger::$INDEX_ALLOCATION",
                   "Deploy/.ledger", "DEPLOY/.LEDGER/x", "..\\Outside", "..\\..\\x",
                   "\\Outside", str(outside), "C:x", "scripts\\sub"]:
        before = snapshot(prod.parent)
        plan, err = try_deploy(dev, prod, [(SCRIPTS_GLOB, target)])
        after = snapshot(prod.parent)
        how = f"rejected ({err})" if err else f"plan writable={len(plan.writable())}"
        check(f"S1 target {target!r}: nothing written anywhere",
              before == after and not str(err or "").startswith("CRASH"), how)


def s2_root_target(base: Path) -> None:
    """S2: a root target never prunes .env / data/; rollback restores."""
    dev, prod, _outside = fresh(base, "s2")
    plant(dev, {JOB_A: b"a"})
    plant(prod, {".env": b"SECRET=1", "data/cleansed/tx/part-0.parquet": b"p",
                 JOB_A: b"a", JOB_OLD: b"old"})
    for root_spelling in ["", ".", "./"]:
        _plan, err = try_deploy(dev, prod, [(WORKSPACE_YAML, root_spelling), (JOBS_GLOB, "jobs")])
        check(f"S2 root {root_spelling!r}: .env and data/ survive",
              err is None and (prod / ".env").exists()
              and (prod / "data/cleansed/tx/part-0.parquet").exists(), str(err or ""))
    check("S2 the jobs base still prunes jobs/old", not (prod / JOB_OLD).exists())
    check("S2 in-sync jobs/a survives", (prod / JOB_A).exists())
    for _ in range(3):
        service.rollback(prod)
    check("S2 rollback restores jobs/old", (prod / JOB_OLD).exists())


def s3_case_aliases(base: Path) -> None:
    """S3: case aliases on NTFS."""
    dev, prod, _outside = fresh(base, "s3")
    plant(dev, {JOBS_A_YAML: b"a", "scripts/s.py": b"s", "triggers/t.yaml": b"name: t\n",
                "deploy/.deployignore": b"config/\n"})
    plant(prod, {JOBS_A_YAML: b"a", "config/secret.yaml": b"s",
                 "triggers/live.yaml": b"name: live\nenabled: true\n"})
    plan, err = try_deploy(dev, prod, [(JOBS_GLOB, "jobs"), ("nothing/**", "JOBS"),
                                       (SCRIPTS_GLOB, "scripts"), ("nothing2/**", "CONFIG"),
                                       ("triggers/**", "triggers"), ("nothing3/**", "TRIGGERS")])
    deletes = [i.target_path for i in (plan.items if plan else []) if i.action == Action.delete]
    check("S3 plan builds", err is None, str(err or ""))
    check("S3 no DELETE through a case alias", deletes == [], str(deletes))
    check("S3 in-sync jobs/a.yaml survives", (prod / JOBS_A_YAML).exists())
    check("S3 ignored config/secret.yaml survives", (prod / "config/secret.yaml").exists())
    check("S3 active trigger survives", (prod / "triggers/live.yaml").exists())


def s4_nested_bases(base: Path) -> None:
    """S4: nested bases apply without crashing."""
    dev, prod, _outside = fresh(base, "s4")
    plant(dev, {JOBS_A_YAML: b"a", "jobs/sub/k.yaml": b"k"})
    plant(prod, {"jobs/sub/old.yaml": b"old"})
    try:
        _plan, err = try_deploy(dev, prod, [(JOBS_GLOB, "jobs"), ("jobs/sub/**", "jobs/sub")])
        check("S4 nested bases apply cleanly", err is None and not (prod / "jobs/sub/old.yaml").exists())
    except Exception as e:  # noqa: BLE001 -- the smoke reports any crash as a failure
        check("S4 nested bases apply cleanly", False, f"{type(e).__name__}: {e}")


def s5_dirty_triggers(base: Path) -> None:
    """S5: dirty trigger files on NTFS (cp1252 locale) don't crash the planner."""
    dev, prod, _outside = fresh(base, "s5")
    plant(dev, {"triggers/a.yaml": b"name: a\n"})
    plant(prod, {"triggers/x.yaml": b"\xff\xfe", "triggers/y.yaml": b'a: !!int ""\n',
                 "triggers/z.yaml": b"a: " + b"[" * 350 + b"]" * 350 + b"\n",
                 "triggers/a.yaml": "name: a\nnote: ação\n".encode("utf-8")})
    try:
        _plan, err = try_deploy(dev, prod, [("triggers/**", "triggers")])
        check("S5 dirty triggers plan and apply", err is None)
    except Exception as e:  # noqa: BLE001 -- the smoke reports any crash as a failure
        check("S5 dirty triggers plan and apply", False, f"{type(e).__name__}: {e}")


def junction(link: Path, target: Path) -> bool:
    """An NTFS directory junction (no admin needed, unlike a symlink)."""
    link.parent.mkdir(parents=True, exist_ok=True)
    done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                          capture_output=True, check=False)
    return done.returncode == 0 and os.path.samefile(link, target)


def s6_junctions(base: Path) -> None:
    """S6 (df-m1dk): a Prod folder that is a junction to Outside is refused, never written or
    pruned through. The spelling of the path is fine, so only resolving the folder catches it."""
    dev, prod, outside = fresh(base, "s6")
    plant(dev, {"tools/a.py": b"a", "lib/sub/b.py": b"b"})
    check("S6 premise: Prod/tools is a junction to Outside", junction(prod / "tools", outside))
    check("S6 premise: Prod/lib/sub is a junction to Outside", junction(prod / "lib" / "sub", outside))
    for name, rules in [("base junction, prune", [("tools/**", "tools")]),
                        ("nested junction", [("lib/**", "lib")])]:
        before = snapshot(outside)
        _plan, err = try_deploy(dev, prod, rules)
        check(f"S6 {name}: refused, Outside untouched", err == "invalid" and snapshot(outside) == before,
              str(err))


def outside_untouched(base: Path) -> None:
    """Outside sentinels: untouched in every scenario."""
    for scen in ["premises", "s1", "s2", "s3", "s4", "s5", "s6"]:
        victim = base / scen / "Outside" / "victim.txt"
        check(f"{scen}: Outside/victim.txt untouched",
              victim.is_file() and victim.read_bytes() == b"must survive")


def main(base: Path) -> int:
    print(f"python {sys.version.split()[0]} on {sys.platform}; base {base}")
    if sys.platform != "win32":
        print("this smoke must run with the Windows interpreter")
        return 2

    premises(base)
    s1_escaping_targets(base)
    s2_root_target(base)
    s3_case_aliases(base)
    s4_nested_bases(base)
    s5_dirty_triggers(base)
    s6_junctions(base)
    outside_untouched(base)

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    base = Path(tempfile.mkdtemp(prefix="df-deploy-smoke-"))
    try:
        code = main(base)
    finally:
        if "--keep" not in sys.argv:
            shutil.rmtree(base, ignore_errors=True)
    sys.exit(code)
