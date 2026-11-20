"""Mirror a workspace of a release onto prod, leaving prod's own files alone (df-bsb2.2.6.2).

    python3 scripts/prod/mirror_workspace.py <release workspace dir> <prod workspace dir> <backup dir>

scripts/prod/deploy.sh runs it after update-prod installed the release, with the workspace from the
same commit (git archive), so prod's engine and workspace come from one commit.

- Every file of the release is copied over prod's copy; a file the last mirror wrote and the release
  no longer has is deleted. The list of what the mirror wrote is MANIFEST in the prod workspace; a
  file prod has and the manifest does not name (data, settings, anything made there) is never
  touched. Without a manifest nothing is deleted.
- LOCAL paths are prod's own even if a release carried one: never written, never deleted.
- While the prod workspace is still a git checkout (a .git in it) nothing is mirrored: the switch
  from that checkout is done by hand once (df-bsb2.2.6.3).
- Any failure puts back every file it changed (from the backup dir) and exits 1, so the caller can
  roll back the engine too.

Exit codes: 0 mirrored or nothing to do, 1 failed (prod's workspace is as it was), 2 bad arguments.
Standard library only: it runs on the WSL system Python, outside any venv.
"""
import shutil
import sys
from pathlib import Path, PurePosixPath

MANIFEST = ".mirrored-files"
LOCAL_DIRS = ("data", "report", "triggers", "deploy/.ledger", ".venv", ".git")
LOCAL_FILES = (".env", "dataforge.db", "config/investments.yaml", MANIFEST)
CACHE_DIRS = ("__pycache__", ".pytest_cache", "node_modules")


def is_local(rel: str) -> bool:
    """True for a path (relative, '/'-separated) that belongs to prod and never to a release."""
    parts = PurePosixPath(rel).parts
    if rel in LOCAL_FILES or any(part in CACHE_DIRS for part in parts):
        return True
    return any(rel == d or rel.startswith(d + "/") for d in LOCAL_DIRS)


def release_files(src: Path) -> list[str]:
    return sorted(p.relative_to(src).as_posix() for p in src.rglob("*") if p.is_file())


def read_manifest(dst: Path) -> set[str]:
    path = dst / MANIFEST
    if not path.is_file():
        return set()
    return {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


class Mirror:
    """One mirror run; remembers each change so undo() can put prod back as it was."""

    def __init__(self, src: Path, dst: Path, backup: Path):
        self.src, self.dst, self.backup = src, dst, backup
        self.saved: list[str] = []     # files that existed in dst, copied to backup before the change
        self.created: list[str] = []   # files that did not exist in dst

    def save(self, rel: str) -> None:
        target = self.dst / rel
        if target.exists():
            (self.backup / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, self.backup / rel)
            self.saved.append(rel)
        else:
            self.created.append(rel)

    def write(self, rel: str) -> bool:
        source, target = self.src / rel, self.dst / rel
        if target.is_file() and target.read_bytes() == source.read_bytes():
            return False
        self.save(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        return True

    def delete(self, rel: str) -> bool:
        target = self.dst / rel
        if not target.is_file():
            return False
        self.save(rel)
        target.unlink()
        parent = target.parent
        while parent != self.dst and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent
        return True

    def undo(self) -> None:
        for rel in self.created:
            (self.dst / rel).unlink(missing_ok=True)
        for rel in self.saved:
            (self.dst / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.backup / rel, self.dst / rel)


def mirror(src: Path, dst: Path, backup: Path) -> int:
    if not src.is_dir():
        print(f"mirror: the release has no {src.name} workspace, nothing mirrored")
        return 0
    if (dst / ".git").exists():
        print(f"mirror: {dst} is still a git checkout, not mirrored (switch it by hand: df-bsb2.2.6.3)")
        return 0
    files = release_files(src)
    carried = [rel for rel in files if is_local(rel) and not set(PurePosixPath(rel).parts) & set(CACHE_DIRS)]
    for rel in carried:
        print(f"mirror: {rel} is prod's own, the release's copy is ignored", file=sys.stderr)
    files = [rel for rel in files if not is_local(rel)]
    gone = sorted(read_manifest(dst) - set(files))
    run = Mirror(src, dst, backup)
    try:
        # Sonar S8707 (path traversal via command-line argument) is a false positive: the three paths
        # are the operator's, built by deploy.sh from its own work folder and prod.env, and copying
        # into the prod folder they name is this script's whole job.
        dst.mkdir(parents=True, exist_ok=True)   # NOSONAR
        written = sum(run.write(rel) for rel in files)
        deleted = sum(run.delete(rel) for rel in gone if not is_local(rel))
        manifest = dst / MANIFEST
        run.save(MANIFEST)
        manifest.write_text("".join(f"{rel}\n" for rel in files), encoding="utf-8")
    except OSError as exc:
        print(f"mirror: FAILED ({exc}); putting {dst} back as it was", file=sys.stderr)
        run.undo()
        return 1
    print(f"mirror: {dst}: {written} written, {deleted} deleted, {len(files) - written} unchanged")
    return 0


def main(argv: list[str]) -> int:
    try:
        src, dst, backup = (Path(a) for a in argv)
    except ValueError:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    return mirror(src, dst, backup)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
