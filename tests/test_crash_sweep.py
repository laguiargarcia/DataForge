"""The crash sweep helper itself (tests/crash_sweep.py): each child dies exactly at the chosen
write point, before it, half-way through it or right after it."""
from pathlib import Path
from tests.crash_sweep import Step, WritePoints, crash_sweep

_POINTS = WritePoints(["pathlib.Path.write_bytes", "os.replace"],
                      torn=("pathlib.Path.write_bytes",), after=("os.replace",))
_LEFT = {   # what the disk holds after each crash of three_writes
    "k=1 before pathlib.Path.write_bytes": {},
    "k=1 torn pathlib.Path.write_bytes": {"a": b"aa"},
    "k=2 before os.replace": {"a": b"aaaa"},
    "k=2 after os.replace": {"b": b"aaaa"},
    "k=3 before pathlib.Path.write_bytes": {"b": b"aaaa"},
    "k=3 torn pathlib.Path.write_bytes": {"b": b"aaaa", "c": b"cc"},
}


def test_each_child_dies_at_its_step_and_leaves_what_was_written_up_to_there(tmp_path):
    def make_workdir(name: str) -> Path:
        (tmp_path / name).mkdir()
        return tmp_path / name

    left: dict[str, dict[str, bytes]] = {}

    def check_recovered(workdir: Path, step: Step) -> None:
        left[str(step)] = {p.name: p.read_bytes() for p in sorted(workdir.iterdir())}

    steps = crash_sweep("tests.crash_sweep_selftest_scenario:three_writes", make_workdir,
                        _POINTS, check_recovered)
    assert [str(s) for s in steps] == list(_LEFT)
    assert left == _LEFT
    assert {p.name: p.read_bytes() for p in (tmp_path / "dry").iterdir()} == {
        "b": b"aaaa", "c": b"cccc"}
