import hashlib
import os
from pathlib import Path
from .models import DeployEntry

TEMP_SUFFIX = ".deploytmp"   # atomic_write's temp file: '.<name>.deploytmp' next to the file


def atomic_write(path: Path, data: bytes) -> None:
    """Write via a temp file + os.replace so a failed write never leaves a half-written file: the
    path holds either its old or its new content. A process killed before os.replace leaves the
    temp file behind (see is_temp_name)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + TEMP_SUFFIX)
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def is_temp_name(rel: str) -> bool:
    """True for the temp file atomic_write leaves behind when its process dies before os.replace."""
    name = rel.rsplit("/", 1)[-1]
    return name.startswith(".") and name.endswith(TEMP_SUFFIX)


def blob_sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


class Ledger:
    """Content-addressed blob store + append-only deploy log under <target>/deploy/.ledger/.

    Borrows git's idea (objects keyed by content hash + an append-only log of "commits")
    with no git dependency, no branches, no merge.
    """

    def __init__(self, target_dir: Path):
        self.root = target_dir / "deploy" / ".ledger"
        self.objects = self.root / "objects"
        self.log = self.root / "deploylog.jsonl"

    # --- content-addressed blobs ---
    def write_blob(self, data: bytes) -> str:
        """Store data under its hash. An existing blob is kept only when its content matches its
        name: one torn by an older, non-atomic write is rewritten (df-ahua.5.2)."""
        sha = blob_sha(data)
        if not self.has_blob(sha):
            atomic_write(self._blob_path(sha), data)
        return sha

    def has_blob(self, sha: str) -> bool:
        """True when the blob exists AND holds the content its name says."""
        path = self._blob_path(sha)
        return path.is_file() and blob_sha(path.read_bytes()) == "sha256:" + _hex(sha)

    def read_blob(self, sha: str) -> bytes:
        return self._blob_path(sha).read_bytes()

    def _blob_path(self, sha: str) -> Path:
        hexd = _hex(sha)
        return self.objects / hexd[:2] / hexd

    # --- append-only deploy log ---
    def append(self, entry: DeployEntry) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with open(self.log, "a", encoding="utf-8") as f:
            f.write(entry.model_dump_json() + "\n")

    def entries(self) -> list[DeployEntry]:
        if not self.log.exists():
            return []
        return [DeployEntry.model_validate_json(line)
                for line in self.log.read_text(encoding="utf-8").splitlines() if line.strip()]

    def last(self) -> DeployEntry | None:
        es = self.entries()
        return es[-1] if es else None

    def drop_last(self) -> None:
        es = self.entries()[:-1]
        self.root.mkdir(parents=True, exist_ok=True)
        with open(self.log, "w", encoding="utf-8") as f:
            for e in es:
                f.write(e.model_dump_json() + "\n")


def _hex(sha: str) -> str:
    return sha.split(":", 1)[-1]
