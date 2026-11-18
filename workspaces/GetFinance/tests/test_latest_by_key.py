"""Properties of latest_by_key, the raw -> cleansed rule shared by the four raw2cleansed scripts.

latest_by_key(frames, keys, order_by) combines the raw partitions (diagonal_relaxed) and keeps one row
per key, the one with the greatest order value (ADR 0004 "High-risk rules here", df-ahua.15). The
transactions script then accumulates that result into cleansed with upsert_delta_table, so the
accumulation properties go through that function too.

Every frame is generated: keys are "k0".."k5", the order column holds a few made-up dates (both the
day-granular and the 32-character timestamp form that coexist in raw), and `v` tags each input row
with its position in input order so a kept row can be traced back to the row it came from.

Two edge cases of the pure functions are pinned, not fixed: a NULL order value wins the dedup
(df-ahua.15.1) and the MERGE inserts a NULL-key row again on every run (df-ahua.15.2). The fix is
upstream: raw2cleansed validates the raw before latest_by_key and refuses NULL ids and NULL
ingested_at (df-ahua.3). tests/test_validation.py proves the scripts refuse that input.

The functions are loaded from _delta_util.SCRIPTS, this checkout's scripts.
"""
import tempfile
from pathlib import Path

import polars as pl
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tests import _delta_util

SCRIPTS = _delta_util.SCRIPTS
RAW2CLEANSED = ["raw2cleansed", "raw2cleansed_accounts", "raw2cleansed_bills",
                "raw2cleansed_investments"]

KEY, ORDER, TAG = "id", "ingested_at", "v"
KEYS = [f"k{i}" for i in range(6)]
ORDERS = ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-02T08:00:00.000000+00:00"]
SCHEMA = {KEY: pl.Utf8, ORDER: pl.Utf8, TAG: pl.Int64}

PURE = settings(max_examples=150, deadline=None, database=None)
DELTA = settings(max_examples=15, deadline=None, database=None,
                 suppress_health_check=[HealthCheck.too_slow])


def _load_functions():
    path = SCRIPTS / "functions" / "functions.py"
    ns = {"__file__": str(path)}
    _delta_util.exec_functions(ns)
    return ns


F = _load_functions()


def latest_by_key(frames):
    return F["latest_by_key"](frames, [KEY], ORDER)


# --- strategies and helpers ------------------------------------------------------------------

def partitions(null_keys=False, null_orders=False, max_rows=30):
    """A list of partitions, each a list of (key, order) pairs."""
    key = st.sampled_from(KEYS + [None] if null_keys else KEYS)
    order = st.sampled_from(ORDERS + [None] if null_orders else ORDERS)
    part = st.lists(st.tuples(key, order), max_size=max_rows)
    return st.lists(part, min_size=1, max_size=5)


def to_frames(parts, first_tag=0):
    """One DataFrame per partition; `v` numbers the rows in input order across partitions."""
    frames, tag = [], first_tag
    for part in parts:
        rows = [(k, o, tag + i) for i, (k, o) in enumerate(part)]
        tag += len(part)
        frames.append(pl.DataFrame(rows, schema=SCHEMA, orient="row"))
    return frames


def rows(df):
    return sorted(df.select(KEY, ORDER, TAG).rows(), key=lambda r: r[2])


def input_rows(parts):
    return rows(pl.concat(to_frames(parts)))


def first_max_per_key(in_rows):
    """Reference model for non-null orders: per key, the first row in input order among the rows
    that hold the key's greatest order value."""
    best = {}
    for k, o, v in in_rows:  # in input order, so a later tie never replaces an earlier row
        if k not in best or o > best[k][1]:
            best[k] = (k, o, v)
    return sorted(best.values(), key=lambda r: r[2])


# --- the dedup rule --------------------------------------------------------------------------

# Rule: the output has exactly one row per distinct key, and it holds that key's greatest order.
@PURE
@given(parts=partitions())
def test_one_row_per_key_holding_its_greatest_order(parts):
    ins = input_rows(parts)
    out = latest_by_key(to_frames(parts))
    assert sorted(out[KEY].to_list()) == sorted({k for k, _, _ in ins})
    greatest = {}
    for k, o, _ in ins:
        greatest[k] = max(o, greatest.get(k, o))
    assert all(o == greatest[k] for k, o, _ in rows(out))


# Rule: no input key goes missing (null included), and every output row is an input row, unchanged.
@PURE
@given(parts=partitions(null_keys=True, null_orders=True))
def test_no_key_lost_and_every_output_row_is_an_input_row(parts):
    ins = input_rows(parts)
    out = rows(latest_by_key(to_frames(parts)))
    assert {k for k, _, _ in out} == {k for k, _, _ in ins}
    assert set(out) <= set(ins)


# Rule: applying the rule to its own output changes nothing.
@PURE
@given(parts=partitions(null_keys=True, null_orders=True))
def test_idempotent(parts):
    once = latest_by_key(to_frames(parts))
    assert latest_by_key([once]).equals(once)


@st.composite
def distinct_pairs_shuffled(draw):
    pairs = draw(st.lists(st.tuples(st.sampled_from(KEYS), st.sampled_from(ORDERS)),
                          min_size=1, max_size=24, unique=True))
    shuffled = draw(st.permutations(pairs))
    cuts = sorted(draw(st.lists(st.integers(0, len(pairs)), max_size=4)))
    bounds = [0, *cuts, len(pairs)]
    return pairs, [shuffled[a:b] for a, b in zip(bounds, bounds[1:], strict=False)]


# Rule: when (key, order) pairs are distinct, partition boundaries and row order do not change the result.
@PURE
@given(case=distinct_pairs_shuffled())
def test_independent_of_partitioning_and_row_order_when_pairs_are_distinct(case):
    pairs, reshuffled = case
    a = latest_by_key(to_frames([pairs])).select(KEY, ORDER).rows()
    b = latest_by_key(to_frames(reshuffled)).select(KEY, ORDER).rows()
    assert sorted(a) == sorted(b)


# Rule: on a tie, the first row in input order wins (earliest partition, then row order), every run.
@PURE
@given(parts=partitions(max_rows=40))
def test_tie_goes_to_the_first_row_in_input_order(parts):
    frames = to_frames(parts)
    out = latest_by_key(frames)
    assert rows(out) == first_max_per_key(input_rows(parts))
    assert latest_by_key(frames).equals(out)


# Pinned (df-ahua.15.1): a NULL order value sorts first and beats every real one. raw2cleansed never
# lets it reach latest_by_key: test_validation.py::test_transactions_script_refuses_a_null_ingested_at.
def test_null_order_beats_a_real_one_so_raw_validation_refuses_it():
    out = rows(latest_by_key(to_frames([[("k0", None), ("k0", "2026-01-01")]])))
    assert out == [("k0", None, 0)]


# Rule: NULL keys and NULL orders never change the kept row of a key whose rows are all valid.
@PURE
@given(parts=partitions(null_keys=True, null_orders=True))
def test_null_rows_never_change_other_keys(parts):
    tainted = {k for part in parts for k, o in part if k is None or o is None}
    full = to_frames(parts)
    clean_only = [f.filter(pl.col(KEY).is_not_null() & ~pl.col(KEY).is_in(list(tainted - {None})))
                  for f in full]
    kept = [r for r in rows(latest_by_key(full)) if r[0] is not None and r[0] not in tainted]
    assert kept == rows(latest_by_key(clean_only))


# Rule (current behaviour, pinned): rows with a NULL key form one group, so exactly one of them survives.
@PURE
@given(parts=partitions(null_keys=True))
def test_null_keys_form_one_group(parts):
    any_null = any(k is None for part in parts for k, _ in part)
    out = latest_by_key(to_frames(parts))
    assert out[KEY].null_count() == (1 if any_null else 0)


@st.composite
def mixed_schema_partitions(draw):
    """Partitions whose extra columns differ: `s` present or not, `note` all-null (Null dtype) or
    text, `n` absent / Int32 / Int64. Values derive from `v`, so each can be checked at the output."""
    parts = draw(partitions(max_rows=12))
    frames, layouts = [], []
    for frame in to_frames(parts):
        has_s = draw(st.booleans())
        note = draw(st.sampled_from(["absent", "null", "text"]))
        n = draw(st.sampled_from(["absent", pl.Int32, pl.Int64]))
        cols = []
        if has_s:
            cols.append(pl.format("s{}", pl.col(TAG)).alias("s"))
        if note == "null":
            cols.append(pl.lit(None, dtype=pl.Null).alias("note"))
        elif note == "text":
            cols.append(pl.format("n{}", pl.col(TAG)).alias("note"))
        if n != "absent":
            cols.append((pl.col(TAG) * 10).cast(n).alias("n"))
        frames.append(frame.with_columns(cols))
        layouts.append((frame[TAG].to_list(), has_s, note, n))
    return parts, frames, layouts


# Rule: partitions with different column sets, an all-null column, or Int32 vs Int64 combine
# without losing rows, columns or values (a column a partition lacked reads as null for its rows).
@PURE
@given(case=mixed_schema_partitions())
def test_partitions_with_different_schemas_combine_without_loss(case):
    parts, frames, layouts = case
    out = latest_by_key(frames)
    assert set(out.columns) == set().union(*(f.columns for f in frames))
    assert out.height == len({k for part in parts for k, _ in part})
    if "n" in out.columns:
        assert out.schema["n"] == (pl.Int64 if any(lay[3] == pl.Int64 for lay in layouts) else pl.Int32)
    layout_of = {v: lay for lay in layouts for v in lay[0]}
    for r in out.to_dicts():
        _, has_s, note, n = layout_of[r[TAG]]
        if "s" in out.columns:
            assert r["s"] == (f"s{r[TAG]}" if has_s else None)
        if "note" in out.columns:
            assert r["note"] == (f"n{r[TAG]}" if note == "text" else None)
        if "n" in out.columns:
            assert r["n"] == (r[TAG] * 10 if n != "absent" else None)


# Rule: when no partition has the key or the order column, the rule fails loudly instead of
# inventing a null column (accepted change of df-ahua.15: before, bills/investments wrote garbage).
@pytest.mark.parametrize("missing", [KEY, ORDER])
def test_missing_key_or_order_column_fails_loudly(missing):
    frames = [f.drop(missing) for f in to_frames([[("k0", "2026-01-01")], [("k1", "2026-01-02")]])]
    with pytest.raises(pl.exceptions.ColumnNotFoundError):
        latest_by_key(frames)


# Through the scripts that used to invent the column: since df-ahua.3 their raw validation refuses
# it first (a missing column reads as all-null), and no cleansed table is written.
@pytest.mark.parametrize("entity,script", [("bills", "raw2cleansed_bills"),
                                           ("investments", "raw2cleansed_investments")])
@pytest.mark.parametrize("missing", [KEY, ORDER])
def test_script_fails_loudly_when_no_partition_has_the_column(tmp_path, entity, script, missing):
    for i, frame in enumerate(to_frames([[("k0", "2026-01-01")], [("k1", "2026-01-02")]])):
        part = tmp_path / "raw" / entity / f"ingestion_date=2026-01-0{i + 1}"
        part.mkdir(parents=True)
        frame.drop(missing).with_columns(pl.col(TAG).cast(pl.Utf8)).write_parquet(part / "data.parquet")
    with pytest.raises(Exception, match=f"raw.{entity}: .*{missing} is null") as exc:
        _delta_util.run_script("raw2cleansed", script, ["--source", "raw", "--target", "cleansed"],
                               tmp_path)
    assert type(exc.value).__name__ == "DataValidationError"
    assert not (tmp_path / "cleansed" / entity).exists()


# Every raw2cleansed script goes through latest_by_key, so these properties cover all of them.
@pytest.mark.parametrize("name", RAW2CLEANSED)
def test_each_raw2cleansed_script_uses_latest_by_key(name):
    source = (SCRIPTS / "raw2cleansed" / f"{name}.py").read_text(encoding="utf-8")
    assert "latest_by_key(" in source
    assert ".unique(subset=" not in source


# --- accumulation through upsert_delta_table (the transactions path) --------------------------

def cleansed_seed(null_key=False):
    key = st.sampled_from(KEYS + [None] if null_key else KEYS)
    return st.lists(key, max_size=6, unique=True)


def seed_frame(keys):
    return pl.DataFrame([(k, "2025-12-31", 1000 + i) for i, k in enumerate(keys)],
                        schema=SCHEMA, orient="row")


def accumulate(tmp, seed_keys, run_frames):
    """Seed cleansed, then upsert each run's latest_by_key output, as raw2cleansed.py does.
    Returns the table after the seed and after each run."""
    F["_connection_root"] = lambda layer: Path(tmp) / layer
    F["upsert_delta_table"]("cleansed", "transactions", seed_frame(seed_keys), [KEY])
    states = [rows(F["read_delta_table"]("cleansed", "transactions"))]
    for frames in run_frames:
        F["upsert_delta_table"]("cleansed", "transactions", latest_by_key(frames), [KEY])
        states.append(rows(F["read_delta_table"]("cleansed", "transactions")))
    return states


def expected_after_upsert(before, new):
    """Keys only in cleansed keep their row; keys in this run take this run's row."""
    new_keys = {k for k, _, _ in new}
    return sorted([r for r in before if r[0] not in new_keys] + new, key=lambda r: r[2])


# Rule: an upsert keeps every cleansed key this run does not carry, and takes this run's row for every key it does.
@DELTA
@given(seed=cleansed_seed(), parts=partitions(max_rows=10))
def test_upsert_keeps_absent_keys_and_takes_this_runs_row(seed, parts):
    frames = to_frames(parts)
    with tempfile.TemporaryDirectory() as tmp:
        before, after = accumulate(tmp, seed, [frames])
    assert after == expected_after_upsert(before, rows(latest_by_key(frames)))


# Rule: a NULL key on either side never removes or changes the rows of other keys.
@DELTA
@given(seed=cleansed_seed(null_key=True), parts=partitions(null_keys=True, max_rows=10))
def test_upsert_null_key_never_touches_other_keys(seed, parts):
    frames = to_frames(parts)
    with tempfile.TemporaryDirectory() as tmp:
        before, after = accumulate(tmp, seed, [frames])
    expected = expected_after_upsert([r for r in before if r[0] is not None],
                                     [r for r in rows(latest_by_key(frames)) if r[0] is not None])
    assert [r for r in after if r[0] is not None] == expected


# Rule: rerunning the same raw over the accumulated table changes nothing.
@DELTA
@given(seed=cleansed_seed(), parts=partitions(max_rows=10))
def test_rerunning_the_same_raw_changes_nothing(seed, parts):
    frames = to_frames(parts)
    with tempfile.TemporaryDirectory() as tmp:
        _, first, second = accumulate(tmp, seed, [frames, frames])
    assert second == first


# Pinned (df-ahua.15.2): the MERGE predicate never matches NULL, so a NULL-key row is inserted again
# on every run. raw2cleansed refuses a NULL id before the merge:
# test_validation.py::test_transactions_script_refuses_a_null_id_on_every_run.
def test_upsert_inserts_a_null_key_again_so_raw_validation_refuses_it():
    frames = to_frames([[(None, "2026-01-01")]])
    with tempfile.TemporaryDirectory() as tmp:
        _, first, second = accumulate(tmp, [], [frames, frames])
    assert len(first) == 1
    assert len(second) == 2
