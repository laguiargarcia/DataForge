import hashlib
from pathlib import Path
from .models import DeployEntry


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
        hexd = hashlib.sha256(data).hexdigest()
        path = self._blob_path_for_hex(hexd)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return "sha256:" + hexd

    def has_blob(self, sha: str) -> bool:
        return self._blob_path_for_hex(sha.split(":", 1)[-1]).exists()

    def read_blob(self, sha: str) -> bytes:
        return self._blob_path_for_hex(sha.split(":", 1)[-1]).read_bytes()

    def _blob_path_for_hex(self, hexd: str) -> Path:
        return self.objects / hexd[:2] / hexd

    # --- append-only deploy log ---
    def append(self, entry: DeployEntry) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with open(self.log, "a") as f:
            f.write(entry.model_dump_json() + "\n")

    def entries(self) -> list[DeployEntry]:
        if not self.log.exists():
            return []
        return [DeployEntry.model_validate_json(line)
                for line in self.log.read_text().splitlines() if line.strip()]

    def last(self) -> DeployEntry | None:
        es = self.entries()
        return es[-1] if es else None

    def drop_last(self) -> None:
        es = self.entries()[:-1]
        self.root.mkdir(parents=True, exist_ok=True)
        with open(self.log, "w") as f:
            for e in es:
                f.write(e.model_dump_json() + "\n")
