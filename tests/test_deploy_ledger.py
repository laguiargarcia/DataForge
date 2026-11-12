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
