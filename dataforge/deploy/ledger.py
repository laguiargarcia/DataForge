import hashlib
import logging
import os
from pathlib import Path
from .models import DeployEntry

TEMP_SUFFIX = ".deploytmp"   # atomic_write's temp file: '.<name>.deploytmp' next to the file
_log = logging.getLogger(__name__)


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

    Crash safety (df-ahua.5): a blob name only ever holds its full content; the log is appended
    one line per entry and every rewrite (drop_last) swaps a whole new file in; a
    last line cut short by a dying append is left out of entries() and dropped by the next write.
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
        lines, clean = self._read()
        if not clean:   # never glue the new line onto a torn one (df-ahua.5.4)
            self._rewrite([raw for raw, _ in lines])
        self.root.mkdir(parents=True, exist_ok=True)
        with open(self.log, "ab") as f:
            f.write(entry.model_dump_json().encode("utf-8") + b"\n")

    def entries(self) -> list[DeployEntry]:
        return [entry for _, entry in self._read()[0]]

    def last(self) -> DeployEntry | None:
        es = self.entries()
        return es[-1] if es else None

    def drop_last(self) -> None:
        """Remove the last entry (LIFO). The log is rewritten whole, never truncated in place, so
        a crash keeps either the old or the new log (df-ahua.5.3)."""
        self._rewrite([raw for raw, _ in self._read()[0][:-1]])

    def _rewrite(self, raw_lines: list[bytes]) -> None:
        atomic_write(self.log, b"".join(line + b"\n" for line in raw_lines))

    def _read(self) -> tuple[list[tuple[bytes, DeployEntry]], bool]:
        """(raw line, entry) for every entry in the log, and whether the log ends clean (every
        line whole and newline-terminated). Only an append can cut a line short and it writes the
        last one, so a last line that does not parse is ignored, with a warning (df-ahua.5.4). A
        bad line before it is not a torn append: it raises."""
        if not self.log.exists():
            return [], True
        data = self.log.read_bytes()
        raw = [line.strip() for line in data.split(b"\n") if line.strip()]
        parsed: list[tuple[bytes, DeployEntry]] = []
        for n, line in enumerate(raw, start=1):
            try:
                parsed.append((line, DeployEntry.model_validate_json(line)))
            except ValueError:   # pydantic's ValidationError and UnicodeDecodeError both are
                if n < len(raw):
                    raise
                _log.warning("%s: ignoring its last line, cut short by a deploy that died while "
                             "writing it; the next deploy or rollback drops it", self.log)
                return parsed, False
        return parsed, data.endswith(b"\n") or not data


def _hex(sha: str) -> str:
    return sha.split(":", 1)[-1]
