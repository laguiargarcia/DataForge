"""Rules of dataforge/deploy/ledger.py, the blob store and deploy log every rollback depends on
(docs/architecture.md, "Deploy ledger — crash-safety contract").

Each example works in its own temporary directory created inside the example, because a
test-scoped fixture would be shared between examples.
"""
import contextlib
import hashlib
import logging
import os
import tempfile
from pathlib import Path, PurePosixPath
from unittest import mock

import pytest
from hypothesis import example, given, settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from dataforge.deploy.ledger import TEMP_SUFFIX, Ledger, atomic_write, is_temp_name
from dataforge.deploy.models import Action, DeployEntry, EntryStatus, ItemChange, Kind

# Any text a field can hold: spaces, accents, newlines and other control characters included, since
# reasons and names come from user files and the log is split on b"\n".
_TEXT = st.text(max_size=8)
_IDS = st.sampled_from(["dpl_1", "dpl_2", "dpl_3"])   # few ids, so a reused id is common
_ITEM = st.builds(
    ItemChange, source_path=st.none() | _TEXT, target_path=_TEXT, kind=st.sampled_from(Kind),
    action=st.sampled_from(Action), reason=_TEXT,
    before_sha=st.none() | st.just("sha256:" + "a" * 64), after_sha=st.none() | _TEXT)
_ENTRY = st.builds(
    DeployEntry, id=_IDS, ts=_TEXT, source=_TEXT, target=_TEXT, pipeline=_TEXT,
    parent=st.none() | _IDS, items=st.lists(_ITEM, max_size=3), status=st.sampled_from(EntryStatus))


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _line(entry: DeployEntry) -> bytes:
    return entry.model_dump_json().encode("utf-8")


# --- the deploy log, as a state machine ------------------------------------------------------------

class _Warnings(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.count = 0

    def emit(self, record):
        self.count += 1


class LedgerLog(RuleBasedStateMachine):
    """Any sequence of append / replace_last / drop_last / a dying append / reopen leaves a log
    that reads back as the list the model keeps. A torn last line is ignored with a warning, and
    every write leaves the log clean again."""

    def __init__(self):
        super().__init__()
        self._dir = tempfile.TemporaryDirectory()
        self.target = Path(self._dir.name)
        self.ledger = Ledger(self.target)
        self.model: list[DeployEntry] = []
        self.torn = False   # the log ends in a line a dying append cut short
        self.warnings = _Warnings()
        logging.getLogger("dataforge.deploy.ledger").addHandler(self.warnings)

    def teardown(self):
        logging.getLogger("dataforge.deploy.ledger").removeHandler(self.warnings)
        self._dir.cleanup()

    def _bytes(self) -> bytes | None:
        return self.ledger.log.read_bytes() if self.ledger.log.exists() else None

    @rule(entry=_ENTRY)
    def append(self, entry):
        before = self._bytes() or b""
        self.ledger.append(entry)
        after = self._bytes()
        if not self.torn:   # a clean log only grows: earlier lines stay byte for byte
            assert after == before + _line(entry) + b"\n"
        self.model.append(entry)
        self.torn = False

    @rule(status=st.sampled_from(EntryStatus), items=st.lists(_ITEM, max_size=3))
    def replace_last(self, status, items):
        if not self.model:   # nothing to replace: refused, like any entry that is not the last
            self.replace_last_refused(DeployEntry(id="dpl_1", ts="t", source="D", target="P",
                                                  pipeline="p"))
            return
        new = self.model[-1].model_copy(update={"status": status, "items": items})
        self.ledger.replace_last(new)
        self.model[-1] = new
        self.torn = False

    @rule(entry=_ENTRY)
    def replace_last_refused(self, entry):
        """An entry that is not the last one is refused and the log is left as it was."""
        if self.model and self.model[-1].id == entry.id:
            return
        before = self._bytes()
        with pytest.raises(ValueError, match="is not the last entry"):
            self.ledger.replace_last(entry)
        assert self._bytes() == before

    @rule()
    def drop_last(self):
        self.ledger.drop_last()
        self.model = self.model[:-1]
        self.torn = False

    @rule(entry=_ENTRY, cut=st.integers(min_value=0))
    def append_dies(self, entry, cut):
        """What an append killed half-way leaves: the first bytes of its line, no newline."""
        line = _line(entry)
        self.ledger.root.mkdir(parents=True, exist_ok=True)
        with open(self.ledger.log, "ab") as f:
            f.write(line[: 1 + cut % (len(line) - 1)])
        self.torn = True

    @rule()
    def crlf(self):
        """The log as an older build wrote it on Windows: CRLF line ends."""
        data = self._bytes()
        if data is not None:
            self.ledger.log.write_bytes(data.replace(b"\n", b"\r\n"))

    @rule()
    def reopen(self):
        self.ledger = Ledger(self.target)

    @invariant()
    def reads_back_as_the_model(self):
        warned = self.warnings.count
        assert self.ledger.entries() == self.model
        assert self.ledger.last() == (self.model[-1] if self.model else None)
        assert (self.warnings.count > warned) == self.torn   # warns for a torn line, only then

    @invariant()
    def a_write_leaves_the_log_whole(self):
        data = self._bytes()
        if data is not None and not self.torn:
            assert data == b"" or data.endswith(b"\n")
        if self.ledger.root.exists():   # no temp file of a rewrite is left next to the log
            assert not [p.name for p in self.ledger.root.iterdir() if is_temp_name(p.name)]


TestLedgerLog = LedgerLog.TestCase
TestLedgerLog.settings = settings(max_examples=60, stateful_step_count=20)


def test_a_dying_append_between_writes_is_dropped_and_the_rest_reads_back():
    """The nasty sequence, pinned: three entries, a torn append, an append over it, a replace, CRLF
    lines from an older build, then a drop."""
    m = LedgerLog()
    try:
        e = [DeployEntry(id=f"dpl_{n}", ts="t", source="Dev", target="Prod", pipeline="relatório v2",
                         items=[ItemChange(source_path="a b", target_path="a b", kind=Kind.script,
                                           action=Action.add, reason="new\nfile")]) for n in (1, 2, 3)]
        for x in e[:3]:
            m.append(x)
            m.reads_back_as_the_model()
        m.append_dies(e[0], 10)
        m.reads_back_as_the_model()
        m.append(e[1])
        m.replace_last(EntryStatus.in_progress, [])
        m.crlf()
        m.append(e[2])
        m.drop_last()
        m.reopen()
        m.reads_back_as_the_model()
        m.a_write_leaves_the_log_whole()
        assert [x.id for x in m.ledger.entries()] == ["dpl_1", "dpl_2", "dpl_3", "dpl_2"]
    finally:
        m.teardown()


def test_appending_to_a_log_with_crlf_lines_keeps_them_byte_for_byte(tmp_path):
    """append only adds its line at the end of a whole log: lines an older build wrote on Windows
    (CRLF) are not rewritten."""
    led = Ledger(tmp_path)
    old = _line(DeployEntry(id="dpl_old", ts="t", source="D", target="P", pipeline="p")) + b"\r\n"
    led.root.mkdir(parents=True)
    led.log.write_bytes(old)
    led.append(DeployEntry(id="dpl_new", ts="t", source="D", target="P", pipeline="p"))
    assert led.log.read_bytes().startswith(old)
    assert [e.id for e in led.entries()] == ["dpl_old", "dpl_new"]


def test_the_torn_line_warning_names_the_log_file(tmp_path, caplog):
    """Several targets each have a ledger: the warning says which log it dropped a line from."""
    led = Ledger(tmp_path)
    led.root.mkdir(parents=True)
    led.log.write_bytes(b'{"id":"dpl_1"')
    with caplog.at_level("WARNING", logger="dataforge.deploy.ledger"):
        assert led.entries() == []
    assert str(led.log) in caplog.text


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="df-ahua.23.7: a whole last line that does not validate is taken for a "
                          "torn append and dropped by the next write")
def test_a_whole_last_line_that_does_not_validate_is_never_dropped(tmp_path):
    """Only an append can cut a line short, and the line it cuts has no newline. A newline-terminated
    line that does not validate (a status another version wrote, a hand edit) is not a torn append:
    the next write must not delete that deploy's record."""
    led = Ledger(tmp_path)
    led.append(DeployEntry(id="dpl_1", ts="t", source="D", target="P", pipeline="p"))
    unknown = (b'{"id":"dpl_2","ts":"t","source":"D","target":"P","pipeline":"p","items":[],'
               b'"status":"rolled_back"}')
    with open(led.log, "ab") as f:
        f.write(unknown + b"\n")
    with contextlib.suppress(ValueError):   # refusing to write over it keeps the record too
        led.append(DeployEntry(id="dpl_3", ts="t", source="D", target="P", pipeline="p"))
    assert unknown in led.log.read_bytes()


# --- blobs -----------------------------------------------------------------------------------------

@given(st.binary(max_size=64))
@example(b"")
@example(b"line\r\n\x00\xff")
def test_a_blob_reads_back_under_the_sha256_of_its_content(data):
    """write_blob names the content by its sha256; the name finds it again, prefixed or bare."""
    with tempfile.TemporaryDirectory() as d:
        led = Ledger(Path(d))
        sha = led.write_blob(data)
        assert sha == _sha(data)
        assert led.read_blob(sha) == data
        assert led.has_blob(sha)
        assert led.has_blob(sha.split(":", 1)[1])
        assert Ledger(Path(d)).read_blob(sha) == data   # another Ledger on the same target finds it


@given(st.binary(max_size=32), st.binary(max_size=32))
@example(b"a", b"a")
@example(b"a", b"b")
def test_equal_content_is_stored_once_and_different_content_apart(a, b):
    with tempfile.TemporaryDirectory() as d:
        led = Ledger(Path(d))
        sa, sb = led.write_blob(a), led.write_blob(b)
        assert (sa == sb) == (a == b)
        assert led.read_blob(sa) == a
        assert led.read_blob(sb) == b
        assert len([p for p in led.objects.rglob("*") if p.is_file()]) == len({a, b})


@given(st.binary(min_size=1, max_size=32), st.binary(max_size=32))
@example(b"full content\n", b"full")
@example(b"x", b"")
def test_a_blob_whose_bytes_do_not_match_its_name_is_not_trusted_and_is_rewritten(data, other):
    """A blob torn by an older non-atomic write fails has_blob (rollback's pre-flight) and the next
    write_blob of that content repairs it (df-ahua.5.2)."""
    if other == data:
        other = data + b"#"
    with tempfile.TemporaryDirectory() as d:
        led = Ledger(Path(d))
        sha = led.write_blob(data)
        next(p for p in led.objects.rglob("*") if p.is_file()).write_bytes(other)
        assert led.has_blob(sha) is False
        assert led.write_blob(data) == sha
        assert led.read_blob(sha) == data
        assert led.has_blob(sha)


def test_has_blob_is_false_for_a_blob_never_written(tmp_path):
    led = Ledger(tmp_path)
    assert led.has_blob(_sha(b"never")) is False
    led.write_blob(b"other")
    assert led.has_blob(_sha(b"never")) is False


# --- atomic_write and its temp file ---------------------------------------------------------------

_SEG = st.text(alphabet="abcxyz09_-. ", min_size=1, max_size=8).filter(
    lambda s: s.strip(" .") == s and s not in (".", ".."))


@given(st.lists(_SEG, max_size=3), _SEG)
@example([], "a.py")
@example(["scripts", "sub"], ".env")
def test_the_temp_file_of_a_write_that_dies_is_recognized_as_one(dirs, name):
    """The file atomic_write leaves when it dies before os.replace sits next to the target and
    is_temp_name knows it, so prune never deletes or deploys it as a definition (df-ahua.5.5)."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        rel = PurePosixPath(*dirs, name)
        seen = []

        def dies(src, dst):
            seen.append(Path(src))
            raise OSError("killed")

        with mock.patch.object(os, "replace", dies), pytest.raises(OSError, match="killed"):
            atomic_write(root.joinpath(*rel.parts), b"new")
        (tmp,) = seen
        assert tmp.parent == root.joinpath(*rel.parts).parent
        assert is_temp_name(tmp.relative_to(root).as_posix())
        assert not tmp.exists()   # the exception path still cleans it up


@given(st.lists(_SEG, max_size=3), _SEG)
@example([], ".env")
@example(["a"], "x" + TEMP_SUFFIX)          # the suffix without the leading dot
@example(["a"], ".x" + TEMP_SUFFIX + ".bak")
@example(["." + "x" + TEMP_SUFFIX], "b.py")  # a folder named like a temp file, not the file
@example(["a", "b"], ".deployignore")
def test_a_file_atomic_write_did_not_name_is_not_a_temp_file(dirs, name):
    """Only '.<name>.deploytmp' is a temp file: any other name, dotfiles included, is a real file
    that prune and plan must see."""
    if name.startswith(".") and name.endswith(TEMP_SUFFIX):
        name = name + "x"
    assert is_temp_name("/".join([*dirs, name])) is False


@given(st.lists(_SEG, max_size=3), _SEG)
def test_a_temp_name_is_recognized_at_any_depth(dirs, name):
    assert is_temp_name("/".join([*dirs, "." + name + TEMP_SUFFIX])) is True


@given(st.binary(max_size=16), st.binary(max_size=16))
def test_a_write_that_fails_leaves_the_old_content_and_no_temp(old, new):
    with tempfile.TemporaryDirectory() as d:
        path = Path(d, "scripts", "a.py")
        atomic_write(path, old)
        with mock.patch.object(os, "replace", side_effect=OSError("disk full")), \
                pytest.raises(OSError):
            atomic_write(path, new)
        assert path.read_bytes() == old
        assert [p.name for p in path.parent.iterdir()] == ["a.py"]
        atomic_write(path, new)
        assert path.read_bytes() == new
