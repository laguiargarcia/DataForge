import pytest
from dataforge.deploy.models import DeployEntry, ItemChange, Kind, Action
from dataforge.deploy.ledger import Ledger


def test_write_blob_is_content_addressed_and_dedups(tmp_path):
    led = Ledger(tmp_path)
    sha1 = led.write_blob(b"hello")
    sha2 = led.write_blob(b"hello")
    assert sha1 == sha2
    assert sha1.startswith("sha256:")
    objs = list((tmp_path / "deploy" / ".ledger" / "objects").rglob("*"))
    assert len([p for p in objs if p.is_file()]) == 1


def test_read_blob_roundtrip(tmp_path):
    led = Ledger(tmp_path)
    sha = led.write_blob(b"some bytes\n")
    assert led.has_blob(sha) is True
    assert led.read_blob(sha) == b"some bytes\n"


def test_append_entries_and_last(tmp_path):
    led = Ledger(tmp_path)
    assert led.last() is None
    e1 = DeployEntry(id="dpl_1", ts="t1", source="Dev", target="Prod", pipeline="p",
                     items=[ItemChange(source_path="a", target_path="a", kind=Kind.script, action=Action.add)])
    led.append(e1)
    e2 = DeployEntry(id="dpl_2", ts="t2", source="Dev", target="Prod", pipeline="p", items=[])
    led.append(e2)
    assert [e.id for e in led.entries()] == ["dpl_1", "dpl_2"]
    assert led.last().id == "dpl_2"


def test_drop_last(tmp_path):
    led = Ledger(tmp_path)
    led.append(DeployEntry(id="dpl_1", ts="t", source="D", target="P", pipeline="p", items=[]))
    led.append(DeployEntry(id="dpl_2", ts="t", source="D", target="P", pipeline="p", items=[]))
    led.drop_last()
    assert [e.id for e in led.entries()] == ["dpl_1"]
    led.drop_last()
    assert led.entries() == []
    led.drop_last()  # idempotent on empty
    assert led.last() is None


# --- crash safety (df-ahua.5) ---

def _blob_file(led: Ledger, sha: str):
    hexd = sha.split(":")[-1]
    return led.objects / hexd[:2] / hexd


def test_write_blob_rewrites_a_blob_a_crash_left_torn(tmp_path):
    # df-ahua.5.2: a half blob under its hash name must not be trusted, or rollback restores it.
    led = Ledger(tmp_path)
    sha = led.write_blob(b"full content\n")
    _blob_file(led, sha).write_bytes(b"full")            # what an old torn write left
    assert led.has_blob(sha) is False                    # rollback's pre-flight sees it
    assert led.write_blob(b"full content\n") == sha
    assert led.read_blob(sha) == b"full content\n"
    assert led.has_blob(sha) is True


def test_write_blob_keeps_a_good_blob_without_writing_it_again(tmp_path, monkeypatch):
    led = Ledger(tmp_path)
    sha = led.write_blob(b"x")
    monkeypatch.setattr("dataforge.deploy.ledger.atomic_write",
                        lambda *a: (_ for _ in ()).throw(AssertionError("rewrote a good blob")))
    assert led.write_blob(b"x") == sha


def test_write_blob_never_leaves_a_half_blob_under_its_name(tmp_path, monkeypatch):
    # The blob goes through a temp file: a write that fails half-way leaves no blob at all.
    import os
    led = Ledger(tmp_path)
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        led.write_blob(b"payload")
    assert [p for p in led.objects.rglob("*") if p.is_file()] == []


def test_has_blob_accepts_a_bare_hex_digest(tmp_path):
    led = Ledger(tmp_path)
    sha = led.write_blob(b"y")
    assert led.has_blob(sha.split(":")[1]) is True
    assert led.has_blob("sha256:" + "0" * 64) is False
