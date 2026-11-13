import pytest
from dataforge.deploy.models import DeployEntry, EntryStatus, ItemChange, Kind, Action
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

def _entry(eid: str, **kw) -> DeployEntry:
    return DeployEntry(id=eid, ts="t", source="D", target="P", pipeline="p", **kw)


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


def test_an_entry_from_before_the_status_field_reads_as_complete(tmp_path):
    led = Ledger(tmp_path)
    led.root.mkdir(parents=True)
    led.log.write_bytes(b'{"id":"dpl_old","ts":"t","source":"D","target":"P","pipeline":"p",'
                        b'"parent":null,"items":[]}\r\n')   # old format, CRLF as Windows wrote it
    assert [(e.id, e.status) for e in led.entries()] == [("dpl_old", EntryStatus.complete)]


def test_a_torn_last_line_is_ignored_with_a_warning(tmp_path, caplog):
    # df-ahua.5.4: what an append that died half-way leaves must not wedge every later read.
    led = Ledger(tmp_path)
    led.append(_entry("dpl_1"))
    whole = _entry("dpl_2").model_dump_json().encode()
    with open(led.log, "ab") as f:
        f.write(whole[: len(whole) // 2])
    with caplog.at_level("WARNING", logger="dataforge.deploy.ledger"):
        assert [e.id for e in led.entries()] == ["dpl_1"]
    assert "last line" in caplog.text


def test_a_last_line_torn_inside_a_utf8_character_is_ignored(tmp_path):
    led = Ledger(tmp_path)
    led.append(_entry("dpl_1"))
    with open(led.log, "ab") as f:
        f.write('{"id":"relatório'.encode("utf-8")[:-1])   # half of 'ó'
    assert [e.id for e in led.entries()] == ["dpl_1"]


def test_append_after_a_torn_line_drops_it_instead_of_gluing_onto_it(tmp_path):
    led = Ledger(tmp_path)
    led.append(_entry("dpl_1"))
    with open(led.log, "ab") as f:
        f.write(b'{"id":"dpl_tor')
    led.append(_entry("dpl_2"))
    assert [e.id for e in led.entries()] == ["dpl_1", "dpl_2"]
    assert led.log.read_bytes().count(b"\n") == 2
    assert b"dpl_tor" not in led.log.read_bytes()


def test_append_after_a_whole_last_line_without_newline_starts_a_new_line(tmp_path):
    led = Ledger(tmp_path)
    led.root.mkdir(parents=True)
    led.log.write_bytes(_entry("dpl_1").model_dump_json().encode())   # no trailing newline
    led.append(_entry("dpl_2"))
    assert [e.id for e in led.entries()] == ["dpl_1", "dpl_2"]


def test_a_corrupt_line_before_the_last_still_fails_loudly(tmp_path):
    # Only an append can cut a line, and it writes the last one: a bad middle line is not that.
    led = Ledger(tmp_path)
    led.root.mkdir(parents=True)
    led.log.write_bytes(b"\n".join([_entry("dpl_1").model_dump_json().encode(), b'{"id":"dpl_bad',
                                     _entry("dpl_2").model_dump_json().encode(), b""]))
    with pytest.raises(ValueError):
        led.entries()
    third = _entry("dpl_3")
    with pytest.raises(ValueError):
        led.append(third)   # and nothing is written over it


def test_drop_last_never_truncates_the_log_in_place(tmp_path, monkeypatch):
    # df-ahua.5.3: if the rewrite fails, the old log is still all there.
    import os
    led = Ledger(tmp_path)
    led.append(_entry("dpl_1"))
    led.append(_entry("dpl_2"))
    before = led.log.read_bytes()
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("killed")))
    with pytest.raises(OSError):
        led.drop_last()
    assert led.log.read_bytes() == before
    assert [p.name for p in led.root.iterdir()] == ["deploylog.jsonl"]   # no temp left either


def test_drop_last_keeps_earlier_lines_byte_for_byte(tmp_path):
    led = Ledger(tmp_path)
    old = b'{"id":"dpl_old","ts":"t","source":"D","target":"P","pipeline":"p","items":[]}'
    led.root.mkdir(parents=True)
    led.log.write_bytes(old + b"\n")
    led.append(_entry("dpl_2"))
    led.drop_last()
    assert led.log.read_bytes() == old + b"\n"


def test_drop_last_also_drops_a_torn_tail(tmp_path):
    led = Ledger(tmp_path)
    led.append(_entry("dpl_1"))
    led.append(_entry("dpl_2"))
    with open(led.log, "ab") as f:
        f.write(b'{"id":"dpl_3')
    led.drop_last()   # undoes dpl_2, the last entry there is
    assert [e.id for e in led.entries()] == ["dpl_1"]
    assert led.log.read_bytes().endswith(b"\n")


def test_replace_last_swaps_the_last_entry_for_its_new_version(tmp_path):
    led = Ledger(tmp_path)
    led.append(_entry("dpl_1"))
    led.append(_entry("dpl_2", status=EntryStatus.in_progress))
    led.replace_last(_entry("dpl_2", status=EntryStatus.complete))
    assert [(e.id, e.status) for e in led.entries()] == [
        ("dpl_1", EntryStatus.complete), ("dpl_2", EntryStatus.complete)]


@pytest.mark.parametrize("ids", [[], ["dpl_1"]])
def test_replace_last_refuses_an_entry_that_is_not_the_last(tmp_path, ids):
    led = Ledger(tmp_path)
    for eid in ids:
        led.append(_entry(eid))
    before = led.log.read_bytes() if led.log.exists() else None
    other = _entry("dpl_2")
    with pytest.raises(ValueError, match="dpl_2 is not the last entry"):
        led.replace_last(other)
    assert (led.log.read_bytes() if led.log.exists() else None) == before
