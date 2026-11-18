"""Data mutation (ADR 0004, df-ahua.4): named corruptions of a known-good frame.

The operators know nothing about any table. A table says once which of its columns play which role
(`Shape`: keys, amounts, dates, enums, texts, foreign keys, the partition column); each `Mutation`
names the role it corrupts, and `cases(shape)` lists every (mutation, columns) pair that applies.
An operator is `op(frame, cols, rng) -> frame`: it changes one row (or the whole frame), the same
row in every column of `cols` (columns that move together, such as amount and amount_signed), and
is deterministic given `rng` (a `random.Random`; `rng_for` seeds one from a case name).

Raw frames are all text, as raw is: operators keep a text column text. A typed column stays typed
unless the corruption is the type change itself (comma_decimal, not_parseable).
"""
import random
from dataclasses import dataclass
from datetime import date, timedelta

import polars as pl

KEYS, AMOUNTS, DATES, ENUMS, TEXTS, FKS, COLUMNS, FRAME = (
    "keys", "amounts", "dates", "enums", "texts", "fks", "columns", "frame")
UNKNOWN_ENUM = "NEVER_SEEN"
DANGLING = "dangling-0"
FAR_DAYS = 36525            # a century: a date no account can have
SHIFT_DAYS = (1, 28)        # a small shift: still a plausible date


@dataclass(frozen=True)
class Shape:
    """Which columns of one frame play which role. Each item is a column name or a tuple of columns
    that are corrupted together (a composite key, an amount and its signed copy)."""
    keys: tuple = ()
    amounts: tuple = ()
    dates: tuple = ()
    enums: tuple = ()
    texts: tuple = ()
    fks: tuple = ()
    partition: str | None = None   # the column that tells raw partitions apart (ingested_at)
    # The script fixes this frame's columns and dtypes (an explicit select and cast, or the schema
    # of the Delta table it reads), so a missing column or a type change cannot reach it.
    fixed_schema: bool = False

    def targets(self, role):
        if role == FRAME:
            return [()]
        if role == COLUMNS:
            cols = [c for r in (KEYS, AMOUNTS, DATES, ENUMS, TEXTS, FKS) for c in self._cols(r)]
            return [(c,) for c in dict.fromkeys(cols)]
        return [item if isinstance(item, tuple) else (item,) for item in getattr(self, role)]

    def _cols(self, role):
        return [c for item in self.targets(role) for c in item]


def rng_for(name):
    return random.Random(name)  # str seeds are hashed with sha512: stable across runs


# --- helpers ---------------------------------------------------------------------------------------

def _row(df, rng, where=None):
    rows = [i for i in range(df.height) if where is None or where(i)]
    if not rows:
        raise ValueError("no row to mutate")
    return rng.choice(rows)


def _replace(df, cols, i, fn, as_text=False):
    out = []
    for c in cols:
        values = df[c].to_list()
        values[i] = fn(values[i])
        if as_text:
            values = [None if v is None else str(v) for v in values]
            out.append(pl.Series(c, values, dtype=pl.Utf8))
        else:
            out.append(pl.Series(c, values, dtype=df[c].dtype))
    return df.with_columns(out)


def _number(v, fn):
    # Apply fn to a number kept as text (raw) or as a number (typed), in the same representation.
    if isinstance(v, str):
        return repr(round(fn(float(v)), 2))
    return round(fn(v), 2)


def _shift(v, days):
    # Shift a date kept as a date, or as text whose first 10 characters are YYYY-MM-DD.
    if isinstance(v, date):
        return v + timedelta(days=days)
    return (date.fromisoformat(v[:10]) + timedelta(days=days)).isoformat() + v[10:]


def _latest(df, part):
    return df[part].max()


def _set_row(df, cols, rng):
    # A row where every column of `cols` has a value: nulling or nudging a null changes nothing, and a
    # sparse column (amountInAccountCurrency is set on foreign-currency rows only) has some.
    return _row(df, rng, lambda k: all(df[c][k] is not None for c in cols))


# --- operators -------------------------------------------------------------------------------------

def null_value(df, cols, rng):
    return _replace(df, cols, _set_row(df, cols, rng), lambda _: None)


def duplicate_key(df, cols, rng, part=None):
    # Row j takes row i's key; everything else on row j stays its own. With a partition column, i and
    # j are in the same partition: the same id in two fetches is a refresh, not a corruption.
    def same_part(a, b):
        return part is None or df[part][a] == df[part][b]
    i = _row(df, rng, lambda a: any(b != a and same_part(a, b) for b in range(df.height)))
    j = _row(df, rng, lambda b: b != i and same_part(i, b))
    return df.with_columns([
        pl.Series(c, [df[c][i] if k == j else v for k, v in enumerate(df[c].to_list())],
                  dtype=df[c].dtype)
        for c in cols])


def duplicate_row(df, cols, rng):
    i = _row(df, rng)
    return pl.concat([df, df.slice(i, 1)])


def drop_row(df, cols, rng):
    i = _row(df, rng)
    return pl.concat([df.slice(0, i), df.slice(i + 1)])


def empty_input(df, cols, rng):
    return df.clear()


def flip_sign(df, cols, rng):
    i = _row(df, rng, lambda k: all(df[c][k] is not None and float(df[c][k]) != 0 for c in cols))
    return _replace(df, cols, i, lambda v: _number(v, lambda x: -x))


def nudge_amount(df, cols, rng):
    # One centavo more: a total that no longer matches, on a value that still looks right.
    return _replace(df, cols, _set_row(df, cols, rng), lambda v: _number(v, lambda x: x + 0.01))


def comma_decimal(df, cols, rng):
    # A number arriving as text with a comma decimal ("10,5"): the column becomes text.
    i = _row(df, rng, lambda k: df[cols[0]][k] is not None)
    return _replace(df, cols, i, lambda v: str(float(v)).replace(".", ","), as_text=True)


def not_parseable(df, cols, rng):
    return _replace(df, cols, _row(df, rng), lambda _: "n/a", as_text=True)


def shift_date(df, cols, rng):
    days = rng.randint(*SHIFT_DAYS)
    return _replace(df, cols, _row(df, rng), lambda v: _shift(v, days))


def far_date(df, cols, rng):
    return _replace(df, cols, _row(df, rng), lambda v: _shift(v, FAR_DAYS))


def broken_encoding(df, cols, rng):
    # UTF-8 bytes read back as cp1252 (the df-ahua.24 / df-ahua.4.4 class of bug): "ç" -> "Ã§".
    def mojibake(v):
        return v.encode("utf-8").decode("cp1252", errors="replace")
    return _replace(df, cols, _row(df, rng, lambda k: not (df[cols[0]][k] or "").isascii()), mojibake)


def unknown_enum(df, cols, rng):
    return _replace(df, cols, _row(df, rng), lambda _: UNKNOWN_ENUM)


def dangling_fk(df, cols, rng):
    return _replace(df, cols, _row(df, rng), lambda _: DANGLING)


def missing_column(df, cols, rng):
    return df.drop(cols)


def repeat_partition(df, part, rng):
    # The latest partition arrives twice (the same fetch landed again).
    (col,) = part
    return pl.concat([df, df.filter(pl.col(col) == _latest(df, col))])


def truncate_partition(df, part, rng):
    # The latest partition lost its last rows (a fetch cut short).
    (col,) = part
    latest = df.with_row_index("_i").filter(pl.col(col) == _latest(df, col))["_i"].to_list()
    gone = set(latest[len(latest) // 2:])
    return df.with_row_index("_i").filter(~pl.col("_i").is_in(list(gone))).drop("_i")


def dropped_today(df, cols, rng, part):
    # The column stopped arriving today: null on every row of the latest partition only (the older
    # partitions still carry it, so the combined raw keeps the column).
    latest = _latest(df, part)
    return df.with_columns([pl.when(pl.col(part) == latest).then(None).otherwise(pl.col(c)).alias(c)
                            for c in cols])


@dataclass(frozen=True)
class Mutation:
    name: str
    role: str
    op: object
    needs_partition: bool = False
    changes_schema: bool = False    # skipped on a Shape with fixed_schema


MUTATIONS = [
    Mutation("null_value", KEYS, null_value),
    Mutation("null_value", AMOUNTS, null_value),
    Mutation("null_value", DATES, null_value),
    Mutation("null_value", ENUMS, null_value),
    Mutation("null_value", FKS, null_value),
    Mutation("duplicate_key", KEYS, duplicate_key),
    Mutation("duplicate_row", FRAME, duplicate_row),
    Mutation("drop_row", FRAME, drop_row),
    Mutation("empty_input", FRAME, empty_input),
    Mutation("flip_sign", AMOUNTS, flip_sign),
    Mutation("nudge_amount", AMOUNTS, nudge_amount),
    Mutation("comma_decimal", AMOUNTS, comma_decimal, changes_schema=True),
    Mutation("not_parseable", AMOUNTS, not_parseable, changes_schema=True),
    Mutation("not_parseable", DATES, not_parseable, changes_schema=True),
    Mutation("shift_date", DATES, shift_date),
    Mutation("far_date", DATES, far_date),
    Mutation("broken_encoding", TEXTS, broken_encoding),
    Mutation("unknown_enum", ENUMS, unknown_enum),
    Mutation("dangling_fk", FKS, dangling_fk),
    Mutation("missing_column", COLUMNS, missing_column, changes_schema=True),
    Mutation("dropped_today", COLUMNS, dropped_today, needs_partition=True),
    Mutation("repeat_partition", FRAME, repeat_partition, needs_partition=True),
    Mutation("truncate_partition", FRAME, truncate_partition, needs_partition=True),
]


def cases(shape):
    """Every (case name, apply) pair for one frame: `apply(df, rng) -> df`. The name is
    "<mutation>" for a whole-frame mutation, "<mutation>[<col>+<col>]" otherwise."""
    out = []
    for m in MUTATIONS:
        if (m.needs_partition and shape.partition is None) or (m.changes_schema and shape.fixed_schema):
            continue
        for cols in shape.targets(m.role):
            name = m.name + (f"[{'+'.join(cols)}]" if cols else "")
            out.append((name, _bind(m, cols, shape.partition)))
    return out


def _bind(m, cols, part):
    if m.op in (dropped_today, duplicate_key):
        return lambda df, rng: m.op(df, cols, rng, part)
    if m.needs_partition:
        return lambda df, rng: m.op(df, (part,), rng)
    return lambda df, rng: m.op(df, cols, rng)
