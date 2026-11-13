"""Read-only access to a workspace's curated Delta tables."""
import re
from datetime import date
from typing import Annotated, Any

import polars as pl
from fastapi import APIRouter, HTTPException, Query

from dataforge.compat import curated_dir, resolve_workspace

router = APIRouter()

# fullmatch, not ^...$ with match: '$' also matches before a trailing newline (df-ahua.23.1).
# re.ASCII keeps \w to [A-Za-z0-9_]; without it 'relatório' would pass.
_TABLE_NAME = re.compile(r"\w+", re.ASCII)
_MAX_LIMIT = 100000
_NOT_FOUND = "Not found"


def _parse_filter(raw: str, schema: pl.Schema) -> tuple[str, Any]:
    """Split 'column:value' at the first ':' and cast value to the column type."""
    column, sep, value = raw.partition(":")
    if not sep:
        raise HTTPException(status_code=422, detail=f"Invalid filter '{raw}': expected column:value")
    if column not in schema:
        raise HTTPException(status_code=422, detail=f"Unknown column '{column}'")
    dtype = schema[column]
    try:
        if dtype == pl.Date:
            return column, date.fromisoformat(value)
        if dtype == pl.Boolean:
            if value not in ("true", "false"):
                raise ValueError(value)
            return column, value == "true"
        return column, pl.Series([value]).cast(dtype, strict=True)[0]
    except Exception:
        raise HTTPException(status_code=422, detail=f"Invalid value '{value}' for column '{column}'")


@router.get("/{ws}/tables/{table}")
def read_table(
    ws: str,
    table: str,
    eq: Annotated[list[str] | None, Query()] = None,
    gte: Annotated[list[str] | None, Query()] = None,
    lte: Annotated[list[str] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = _MAX_LIMIT,
):
    try:
        ws_path = resolve_workspace(ws)
    except ValueError:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    if not _TABLE_NAME.fullmatch(table):
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    table_path = curated_dir(ws_path) / table
    if not (table_path / "_delta_log").is_dir():
        raise HTTPException(status_code=404, detail=_NOT_FOUND)

    lf = pl.scan_delta(str(table_path))
    schema = lf.collect_schema()
    for raw in eq or []:
        col, val = _parse_filter(raw, schema)
        lf = lf.filter(pl.col(col) == val)
    for raw in gte or []:
        col, val = _parse_filter(raw, schema)
        lf = lf.filter(pl.col(col) >= val)
    for raw in lte or []:
        col, val = _parse_filter(raw, schema)
        lf = lf.filter(pl.col(col) <= val)

    float_cols = [c for c, t in schema.items() if t in (pl.Float32, pl.Float64)]
    if float_cols:
        # NaN and +/-inf have no JSON form: both become null.
        lf = lf.with_columns(
            pl.when(pl.col(c).is_finite()).then(pl.col(c)).alias(c) for c in float_cols
        )

    df = lf.head(limit + 1).collect()
    truncated = df.height > limit
    if truncated:
        df = df.head(limit)
    return {"columns": df.columns, "rows": df.to_dicts(), "truncated": truncated}
