from pathlib import Path
import pytest
from dataforge.deploy.ignore import DeployIgnore
from dataforge.deploy.pairing import load_pipeline, resolve_pairs
from dataforge.deploy.plan import build_plan
from dataforge.deploy.ledger import Ledger
from dataforge.deploy.apply import apply_plan
from dataforge.deploy.rollback import rollback_last


def _mk(ws: Path, rel: str, content: str) -> Path:
    p = ws / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


def _plan(src: Path, tgt: Path, pipeline_yaml: str, name: str = "p"):
    _mk(src, f"deploy/{name}.yaml", pipeline_yaml)
    p = load_pipeline(src, name)
    return build_plan(src, tgt, p, name, DeployIgnore.load(src), resolve_pairs(src, p))


_SCRIPTS = "target: Prod\npairs:\n  - {source: 'scripts/**', target: 'scripts/'}\n"


def test_rollback_of_add_removes_file(tmp_path):
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "scripts/a.py", "new\n")
    led = Ledger(tgt)
    apply_plan(src, tgt, _plan(src, tgt, _SCRIPTS), led)
    assert (tgt / "scripts/a.py").exists()
    rollback_last(tgt, led)
    assert not (tgt / "scripts/a.py").exists()
    assert led.last() is None


def test_rollback_of_modify_restores_old_content(tmp_path):
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "scripts/a.py", "new\n")
    _mk(tgt, "scripts/a.py", "old\n")
    led = Ledger(tgt)
    apply_plan(src, tgt, _plan(src, tgt, _SCRIPTS), led)
    assert (tgt / "scripts/a.py").read_text() == "new\n"
    rollback_last(tgt, led)
    assert (tgt / "scripts/a.py").read_text() == "old\n"


def test_rollback_of_delete_restores_file(tmp_path):
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "scripts/a.py", "a\n")
    _mk(tgt, "scripts/old.py", "old\n")
    led = Ledger(tgt)
    apply_plan(src, tgt, _plan(src, tgt,
               "target: Prod\nprune: true\npairs:\n  - {source: 'scripts/**', target: 'scripts/'}\n"), led)
    assert not (tgt / "scripts/old.py").exists()
    rollback_last(tgt, led)
    assert (tgt / "scripts/old.py").read_text() == "old\n"


def test_rollback_of_add_prunes_emptied_dirs(tmp_path):
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "scripts/sub/a.py", "new\n")
    led = Ledger(tgt)
    apply_plan(src, tgt, _plan(src, tgt, _SCRIPTS), led)
    assert (tgt / "scripts/sub/a.py").exists()
    rollback_last(tgt, led)
    assert not (tgt / "scripts/sub/a.py").exists()
    assert not (tgt / "scripts/sub").exists()   # dir the ADD created, now emptied -> pruned
    assert not (tgt / "scripts").exists()         # and its now-empty parent
    assert tgt.exists()                           # but never the target root itself


def test_rollback_of_add_keeps_dir_with_remaining_file(tmp_path):
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "scripts/a.py", "new\n")
    _mk(tgt, "scripts/keep.txt", "keep\n")        # pre-existing, not part of the deploy
    led = Ledger(tgt)
    apply_plan(src, tgt, _plan(src, tgt, _SCRIPTS), led)
    rollback_last(tgt, led)
    assert not (tgt / "scripts/a.py").exists()
    assert (tgt / "scripts/keep.txt").read_text() == "keep\n"   # untouched
    assert (tgt / "scripts").is_dir()             # dir kept — it still holds a file


def test_rollback_empty_log_returns_none(tmp_path):
    assert rollback_last(tmp_path / "Prod", Ledger(tmp_path / "Prod")) is None


def test_rollback_aborts_if_snapshot_blob_missing(tmp_path):
    src, tgt = tmp_path / "Dev", tmp_path / "Prod"
    _mk(src, "scripts/a.py", "new\n")
    _mk(tgt, "scripts/a.py", "old\n")
    led = Ledger(tgt)
    apply_plan(src, tgt, _plan(src, tgt, _SCRIPTS), led)
    item = led.last().items[0]
    hexd = item.before_sha.split(":")[-1]
    (tgt / "deploy/.ledger/objects" / hexd[:2] / hexd).unlink()   # corrupt the snapshot
    with pytest.raises(FileNotFoundError):
        rollback_last(tgt, led)
    assert led.last() is not None                          # entry retained for retry
    assert (tgt / "scripts/a.py").read_text() == "new\n"   # target untouched (pre-flight aborted)
