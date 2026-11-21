"""Delta reads of a long-lived process, run on threads that live as long as the process (df-5lk8).

On Windows (polars 2.1.0 with deltalake 1.6.6), a process whose first Delta ``collect`` ran on a
thread that then ends dies tens of reads later with an access violation in deltalake's
``_internal.pyd``. The API server and the app backends read on request threads, and anyio ends an
idle one after a few seconds: the prod server died that way 7 times on 2026-11-21. A read that
starts on a thread that lives (the main one or this pool) never crashed. So every Delta read of a
server goes through ``read``, whose work runs on this pool; the quality gate bans ``pl.scan_delta``
and ``pl.read_delta`` elsewhere in the package. Pipeline scripts run in their own short process
and read on their main thread, so they need none of this.
"""
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import polars as pl

WORKERS = 4

# ThreadPoolExecutor never ends an idle worker: its threads live until the interpreter exits.
_POOL = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="dataforge-delta")


def read(path: Path | str, build: Callable[[pl.LazyFrame], pl.LazyFrame] = lambda lf: lf) -> pl.DataFrame:
    """The table at ``path``, shaped by ``build`` (filters, head...), collected on the Delta pool.

    ``build`` runs on the pool too, so it may call ``collect_schema``; what it raises reaches the caller.
    """
    return _POOL.submit(lambda: build(pl.scan_delta(str(path))).collect()).result()
