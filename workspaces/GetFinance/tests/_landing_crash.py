"""Crash sweep of landing2raw's partition writes (df-ahua.10): the minimum local equivalent of
DataForge's tests/crash_sweep.py, which lives in the other repo.

A process can die at any moment (power loss, kill, out of memory); then no `finally` or `except`
runs, so the kill is a real os._exit in a child process:

    python -m tests._landing_crash <workspace> <k> <how>

runs the fixed SCENARIO below against the fake Pluggy and dies at the k-th write point, where a
write point is a call to one of POINTS (os.replace is the step that publishes a partition written to
its temp file, df-ahua.10.5). `how`:
- before: dies before the call;
- after:  the call completes, then it dies;
- torn:   (write_parquet only) the file is written, cut to half its size, then it dies: the state a
          write that died half way leaves on disk.
k = 0 is the dry run: nothing dies, and the child prints one REACHED line per write point it saw.
"""
import os
import sys
from pathlib import Path

import polars as pl

from tests import _fake_pluggy as fp

CRASH_CODE = 113
POINTS = (("pathlib", "Path", "mkdir"), ("polars", "DataFrame", "write_parquet"), ("os", None, "replace"))
TORN = "polars.DataFrame.write_parquet"
REPLACE = "os.replace"
DIED = "landing-crash-died-at "
REACHED = "landing-crash-reached "
TODAY = fp.DEFAULT_NOW.date().isoformat()
ITEMS = "item-a,item-b"


def scenario():
    """Two items, both account types, two transaction pages: all four entities are written."""
    fake = fp.FakePluggy()
    fake.accounts["item-a"] = {"results": [fp.account("acc-a1", "BANK"), fp.account("acc-a2", "CREDIT")]}
    fake.accounts["item-b"] = {"results": [fp.account("acc-b1", "BANK", item_id="item-b")]}
    fake.transactions["acc-a1"] = [{"results": [fp.txn("t-a1-1")]}, {"results": [fp.txn("t-a1-2")]}]
    fake.transactions["acc-a2"] = [{"results": [fp.txn("t-a2-1", 30.0)]}]
    fake.transactions["acc-b1"] = [{"results": [fp.txn("t-b1-1")]}]
    fake.bills["acc-a2"] = fp.offset_pages([fp.bill("b-a2-1"), fp.bill("b-a2-2")], 500)
    fake.investments["item-a"] = fp.offset_pages([fp.investment("i-a-1")], 500)
    fake.investments["item-b"] = fp.offset_pages([], 500)
    return fake


def start_state(ws):
    """The workspace before the run: a full older partition, and a partition of today for accounts
    and transactions from an earlier run today (the same-day rerun case, df-fs58)."""
    raw = fp.make_workspace(ws)
    for entity, rows in (("accounts", [fp.account("acc-old")]), ("transactions", [fp.txn("t-old")]),
                         ("bills", [fp.bill("b-old")]), ("investments", [fp.investment("i-old")])):
        fp.write_partition(raw, entity, "2030-01-14", [{**r, "ingested_at": "2030-01-14"} for r in rows])
    for entity, rows in (("accounts", [fp.account("acc-earlier")]), ("transactions", [fp.txn("t-earlier")])):
        fp.write_partition(raw, entity, TODAY, [{**r, "ingested_at": TODAY} for r in rows])
    return raw


def _die(k, label):
    os.write(1, f"\n{DIED}{k} {label}\n".encode())   # os._exit flushes nothing, so no print
    os._exit(CRASH_CODE)


def _count_points(k, how, seen):
    for module, cls, name in POINTS:
        owner = sys.modules[module] if cls is None else getattr(sys.modules[module], cls)
        original = getattr(owner, name)
        label = ".".join(part for part in (module, cls, name) if part)

        def counted(*args, _original=original, _label=label, **kwargs):
            seen.append(_label)
            if len(seen) != k:
                return _original(*args, **kwargs)
            if how in ("after", "torn"):
                _original(*args, **kwargs)
            if how == "torn":
                path = args[1]
                os.truncate(path, os.path.getsize(path) // 2)
            return _die(k, _label)

        setattr(owner, name, counted)


def child(ws, k, how):
    ws = Path(ws)
    os.environ.update(fp.FAKE_ENV)
    os.environ["ITEM_IDS"] = ITEMS
    fp.install(setattr, scenario(), ws)   # network blocked, writes confined to ws
    seen = []
    _count_points(int(k), how, seen)
    fp.run_landing(ws)
    print("".join(f"\n{REACHED}{label}" for label in seen))


def read_ids(path):
    return sorted(pl.read_parquet(path)["id"].to_list())


if __name__ == "__main__":
    child(*sys.argv[1:])
