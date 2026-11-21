import json
import os
import re
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from http import HTTPStatus

import requests
import yaml
import polars as pl
from pathlib import Path
from urllib.parse import urlsplit
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
ITEMS_ID = [item.strip() for item in os.getenv("ITEM_IDS", "").split(",") if item.strip()]

# Pluggy paginated endpoints: max page size (used by list_transactions / list_investments).
PLUGGY_PAGE_SIZE = 500
# Every Pluggy call gives up after this many seconds, so a stalled connection fails the task (and its
# retry) instead of holding the daily job forever.
PLUGGY_TIMEOUT_SECONDS = 60
# Pluggy API keys last 2 h; reuse one for that long.
PLUGGY_TOKEN_TTL_SECONDS = 7200
# Most pages one paginated call may read (df-ahua.10.2): at 500 rows a page that is far beyond any
# real history, so reaching it means Pluggy's paging is broken and the call raises instead of looping.
PLUGGY_MAX_PAGES = 1000
# The JSON media type every Pluggy call sends and accepts.
JSON_MEDIA_TYPE = "application/json"
# Where the workspace keeps its connection paths, relative to the workspace root.
CONNECTIONS_FILE = Path("connections") / "connections.yaml"

# The cached API key and when it was created: one dict, mutated in place, so no `global`.
_api_token_cache = {"token": None, "created_at": 0.0}


def _require_ok(response, route):
    # A Pluggy call that did not answer 200 fails the fetch task (df-ahua.10.1): the script exits
    # non-zero, the engine retries the task and the job ends failed, instead of landing a partial day.
    # The message names the route and the status only: never the key, never the body (it may hold
    # personal data).
    if response.status_code != HTTPStatus.OK:
        raise RuntimeError(f"Pluggy {route} answered HTTP {response.status_code}")


def get_api_token():
    now = time.time()
    cached = _api_token_cache["token"]
    if cached is not None and (now - _api_token_cache["created_at"]) < PLUGGY_TOKEN_TTL_SECONDS:
        return cached
    url = "https://api.pluggy.ai/auth"
    body = {"clientId": CLIENT_ID, "clientSecret": CLIENT_SECRET}
    headers = {"accept": JSON_MEDIA_TYPE, "content-type": JSON_MEDIA_TYPE}
    response = requests.post(url, json=body, headers=headers, timeout=PLUGGY_TIMEOUT_SECONDS)
    _require_ok(response, "POST /auth")
    _api_token_cache["token"] = response.json().get("apiKey")
    _api_token_cache["created_at"] = now
    return _api_token_cache["token"]


def list_accounts(item_id):
    token = get_api_token()
    if token is None:
        raise RuntimeError("list_accounts: Pluggy POST /auth answered 200 without an apiKey")
    url = f"https://api.pluggy.ai/accounts?itemId={item_id}"
    headers = {"accept": JSON_MEDIA_TYPE, "X-API-KEY": token}
    response = requests.get(url, headers=headers, timeout=PLUGGY_TIMEOUT_SECONDS)
    _require_ok(response, "GET /accounts")
    return response.json()


def list_transactions(account_id):
    token = get_api_token()
    if token is None:
        raise RuntimeError("list_transactions: Pluggy POST /auth answered 200 without an apiKey")
    headers = {"accept": JSON_MEDIA_TYPE, "X-API-KEY": token}
    all_results = []
    base = "https://api.pluggy.ai/v2/transactions"
    # Pluggy v2/transactions uses cursor pagination (the `next` field) and REJECTS a pageSize
    # param: sending it returns 400 "property pageSize should not exist". Request bare and
    # follow `next` until it is null. (Bills/investments still take pageSize — this is v2-only.)
    url = f"{base}?accountId={account_id}"
    page = 1
    requested = set()   # a `next` that leads back to a page already read would loop forever (df-ahua.10.2)
    while True:
        if url in requested:
            raise RuntimeError(f"list_transactions: Pluggy's next cursor repeats at page {page} "
                               f"for account {account_id}; stopping instead of looping")
        requested.add(url)
        response = requests.get(url, headers=headers, timeout=PLUGGY_TIMEOUT_SECONDS)
        _require_ok(response, f"GET /v2/transactions (page {page})")
        data = response.json()
        results = data.get("results", [])
        all_results.extend(results)
        print(f"  account {account_id}: page {page}, fetched {len(all_results)} so far")
        next_cursor = data.get("next")
        if next_cursor is None:
            break
        if page >= PLUGGY_MAX_PAGES:
            raise RuntimeError(f"list_transactions: still a next cursor after {page} pages "
                               f"for account {account_id}; stopping instead of looping")
        if next_cursor.startswith("http"):
            # The API key goes with the request: follow a full URL only to Pluggy itself (df-ahua.10.4).
            pluggy_host = urlsplit(base).netloc
            next_url = urlsplit(next_cursor)
            if (next_url.scheme, next_url.netloc) != ("https", pluggy_host):
                raise RuntimeError(f"list_transactions: refusing a next URL that is not https://{pluggy_host} "
                                   f"(it would carry the API key)")
            url = next_cursor
        else:
            url = base + "?" + next_cursor.lstrip("?")
        page += 1
    return {"results": all_results, "total": len(all_results)}


def _last_numbered_page(what, page, data, fetched):
    """Whether a page-number loop (list_bills, list_investments) has read its last page (df-ahua.10.2).

    `total` decides, as before: the loop ends once that many rows are in. Besides that, an empty page
    ends it, since asking for more would loop forever, and PLUGGY_MAX_PAGES raises. Counts that
    disagree with what was served are printed as a WARNING, and the rows read so far are kept."""
    total = data.get("total", 0)
    total_pages = data.get("totalPages")
    if len(fetched) >= total:
        if len(fetched) > total or (isinstance(total_pages, int) and page < total_pages):
            print(f"WARNING {what}: Pluggy's counts disagree: {len(fetched)} rows by page {page}, "
                  f"total={total}, totalPages={total_pages}; stopped on total, later pages not requested")
        return True
    if not data.get("results"):
        print(f"WARNING {what}: page {page} came back empty with {len(fetched)} of total={total} rows read; "
              f"keeping the rows read")
        return True
    if page >= PLUGGY_MAX_PAGES:
        raise RuntimeError(f"{what}: {len(fetched)} of total={total} rows after {page} pages; "
                           f"stopping instead of looping")
    return False


def list_bills(account_id):
    token = get_api_token()
    if token is None:
        raise RuntimeError("list_bills: Pluggy POST /auth answered 200 without an apiKey")
    headers = {"accept": JSON_MEDIA_TYPE, "X-API-KEY": token}
    all_results = []
    page = 1
    while True:
        url = f"https://api.pluggy.ai/bills?accountId={account_id}&pageSize={PLUGGY_PAGE_SIZE}&page={page}"
        response = requests.get(url, headers=headers, timeout=PLUGGY_TIMEOUT_SECONDS)
        _require_ok(response, f"GET /bills (page {page})")
        data = response.json()
        results = data.get("results", [])
        all_results.extend(results)
        total = data.get("total", 0)
        print(f"  account {account_id}: bills page {page}, fetched {len(all_results)}/{total}")
        if _last_numbered_page(f"bills of account {account_id}", page, data, all_results):
            break
        page += 1
    return {"results": all_results, "total": len(all_results)}


def list_investments(item_id):
    token = get_api_token()
    if token is None:
        raise RuntimeError("list_investments: Pluggy POST /auth answered 200 without an apiKey")
    headers = {"accept": JSON_MEDIA_TYPE, "X-API-KEY": token}
    all_results = []
    page = 1
    while True:
        url = f"https://api.pluggy.ai/investments?itemId={item_id}&pageSize={PLUGGY_PAGE_SIZE}&page={page}"
        response = requests.get(url, headers=headers, timeout=PLUGGY_TIMEOUT_SECONDS)
        _require_ok(response, f"GET /investments (page {page})")
        data = response.json()
        results = data.get("results", [])
        all_results.extend(results)
        total = data.get("total", 0)
        print(f"  item {item_id}: page {page}, fetched {len(all_results)}/{total}")
        if _last_numbered_page(f"investments of item {item_id}", page, data, all_results):
            break
        page += 1
    return {"results": all_results, "total": len(all_results)}


def _workspace_root() -> Path:
    # Scripts at scripts/<layer>/x.py (depth 2) sit two dirs under the workspace root. Keep that
    # as the fast path (unchanged behaviour). Scripts directly under scripts/ (e.g. reprocess.py,
    # depth 1) are one dir shallower, so fall back to walking up to the ancestor that actually
    # holds connections/connections.yaml — making path resolution depth-agnostic.
    base = Path(__file__).parent.parent.parent
    if (base / CONNECTIONS_FILE).exists():
        return base
    for anc in Path(__file__).resolve().parents:
        if (anc / CONNECTIONS_FILE).exists():
            return anc
    return base


def _connection_config(name: str) -> dict:
    connections_path = _workspace_root() / CONNECTIONS_FILE
    with open(connections_path, encoding="utf-8") as f:
        return yaml.safe_load(f)["connections"][name]


def _connection_root(name: str) -> Path:
    return _workspace_root() / _connection_config(name)["path"]


# The temp file write_parquet_partition writes before moving it onto data.parquet (df-ahua.10.5).
# It starts with "." and does not end in .parquet, so no reader glob (`ingestion_date=*/data.parquet`)
# sees it; the backup skips it by this name.
RAW_PARTITION_TEMP = ".data.parquet.tmp"


def write_parquet_partition(connection_name, entity, records, ingestion_date):
    cfg = _connection_config(connection_name)
    if cfg["type"] != "parquet_dir":
        raise ValueError(f"connection {connection_name!r} is type {cfg['type']!r}, not parquet_dir")
    base = _connection_root(connection_name)
    partition_dir = base / entity / f"ingestion_date={ingestion_date.isoformat()}"
    partition_dir.mkdir(parents=True, exist_ok=True)
    out_path = partition_dir / "data.parquet"
    all_keys, seen = [], set()
    for r in records:
        for k in r:
            if k not in seen:
                all_keys.append(k); seen.add(k)
    rows = {k: [(str(r[k]) if r.get(k) is not None else None) for r in records] for k in all_keys}
    # Atomic (df-ahua.10.5): write a temp file beside data.parquet, flush it to disk, then os.replace
    # it over data.parquet in one step, so a crash leaves the old file or the new one, never a torn
    # one. The temp name is fixed, so a leftover from a crashed run is overwritten by the next write
    # of the same partition. On Windows os.replace fails while another process holds data.parquet
    # open (as the in-place write did): the task then fails.
    tmp_path = partition_dir / RAW_PARTITION_TEMP
    pl.DataFrame(rows, schema={k: pl.Utf8 for k in all_keys}).write_parquet(str(tmp_path))
    with open(tmp_path, "r+b") as f:
        os.fsync(f.fileno())
    os.replace(tmp_path, out_path)
    return out_path


def latest_by_key(frames, keys, order_by):
    # The raw -> cleansed rule shared by every raw2cleansed script (ADR 0004, df-ahua.15): combine
    # the raw partitions and keep ONE row per distinct `keys`, the one with the greatest `order_by`.
    # `frames` are the partitions in a fixed order (callers pass them sorted by path). Column sets
    # may differ per partition: diagonal_relaxed unions them and fills what is missing with null.
    # Edge cases, pinned by tests/workspaces/test_latest_by_key.py:
    # - rows whose key is null are one group, like any other key;
    # - a null `order_by` sorts FIRST in Polars' descending sort, so it beats every real value;
    # - rows tied on `order_by` keep the first in input order (earliest partition, then row order);
    #   the stable sort (maintain_order=True) is what guarantees it.
    combined = pl.concat(frames, how="diagonal_relaxed")
    return combined.sort(order_by, descending=True, maintain_order=True).unique(
        subset=keys, keep="first", maintain_order=True
    )


# A transaction gone from the latest fetch leaves cleansed only where its absence means it is not real
# (df-7twv): on a card (the bill is the card's judge, and the gone rows are the bill payments Pluggy
# generates from the bill and later drops, with a live twin) and, on a bank account, only when it was
# PENDING (a pending row that vanishes is a duplicate of the posted one). A POSTED bank row that vanishes
# is money that really moved: the account balance and the bank statement show it, so it stays.
GONE_IS_REMOVED = ((pl.col("account_type") == "CREDIT")
                   | ((pl.col("account_type") == "BANK") & (pl.col("status") == "PENDING")))

# An account where this run would remove more than GONE_MIN ids AND more than GONE_PERCENT % of its ids
# inside the window looks like an incomplete fetch, not like removals (df-75ol): nothing leaves it.
GONE_MIN = 20
GONE_PERCENT = 10
INCOMPLETE_FETCH = ((pl.col("ids_newly_removed") > GONE_MIN)
                    & (pl.col("ids_newly_removed") * 100 > pl.col("ids_in_window") * GONE_PERCENT))


def gone_from_latest_fetch(known, latest_fetch, in_cleansed):
    # The transactions to remove from cleansed (df-75ol, rule of df-7twv). Pluggy returns an account's
    # whole window on every fetch, so the latest fetch covers, for each account in it, the days from that
    # account's earliest date in it on. A known id of such an account, dated inside that window and
    # absent from the latest fetch, is gone; it is removed when GONE_IS_REMOVED holds for it. An account
    # absent from the latest fetch and an id dated before the window are not judged.
    # `known` has one row per id (id, accountId, date, status, account_type: its latest known state, raw
    # or cleansed); `latest_fetch` has id, accountId and date (raw text or a typed date: the day is the
    # first 10 characters); `in_cleansed` is the ids cleansed holds before this run.
    # The guard counts only what this run newly removes (ids still in cleansed, or every removal when
    # cleansed is empty, as after reprocess.py): a removal applied once is not counted again, so an old
    # removal never holds an account for good, and an account held today is held again tomorrow while
    # the fetch stays the same.
    # Returns (per_account, removed): per account of the latest fetch, its ids inside the window, the ids
    # gone, the ids newly removed and the ids removed (0 when INCOMPLETE_FETCH holds the account); and the
    # removed rows (id, accountId).
    window = (latest_fetch.select("accountId", _day("date").alias("day"))
              .group_by("accountId").agg(pl.col("day").min().alias("window_start")))
    new_if_empty = in_cleansed.is_empty()
    in_window = (known.with_columns(_day("date").alias("day"))
                 .join(window, on="accountId", how="inner")
                 .filter(pl.col("day") >= pl.col("window_start"))
                 .with_columns(gone=~pl.col("id").is_in(latest_fetch["id"].implode()))
                 .with_columns(to_remove=pl.col("gone") & GONE_IS_REMOVED.fill_null(False))
                 .with_columns(newly=pl.col("to_remove")
                               & (pl.lit(new_if_empty) | pl.col("id").is_in(in_cleansed.implode()))))
    per_account = (in_window.group_by("accountId", maintain_order=True)
                   .agg(ids_in_window=pl.len(), ids_gone=pl.col("gone").sum().cast(pl.UInt32),
                        ids_newly_removed=pl.col("newly").sum().cast(pl.UInt32),
                        ids_to_remove=pl.col("to_remove").sum().cast(pl.UInt32))
                   .with_columns(ids_removed=pl.when(INCOMPLETE_FETCH).then(0).otherwise(pl.col("ids_to_remove"))
                                 .cast(pl.UInt32))
                   .drop("ids_to_remove")
                   .sort("accountId"))
    held = per_account.filter(INCOMPLETE_FETCH)["accountId"]
    removed = in_window.filter(pl.col("to_remove") & ~pl.col("accountId").is_in(held.implode()))
    return per_account, removed.select("id", "accountId")


def kept_bill_ids(bills, referenced_bill_ids):
    # The bill ids that stand for their statement (df-hog). Pluggy may re-issue a statement under a new
    # bill id and keep returning the old one, so the bills of one account and due month are one statement:
    # the one kept is a bill some charge references (the re-issue re-keys the charges), then the latest
    # updatedAt, then the greatest id. A bill without a due date (or one that does not parse) is a
    # statement of its own.
    # `bills` has id, accountId, dueDate (text, the day in its first 10 characters) and updatedAt;
    # `referenced_bill_ids` is the cc_bill_id of the card transactions. fact_card_statement and the copy
    # rule of raw2cleansed (df-mk25) read the statements through this one choice.
    month = pl.col("dueDate").str.slice(0, 10).str.to_date("%Y-%m-%d", strict=False).dt.truncate("1mo")
    return (bills.with_columns(_referenced=pl.col("id").is_in(pl.Series(list(referenced_bill_ids),
                                                                        dtype=pl.Utf8).implode()))
            .sort(["_referenced", "updatedAt", "id"], descending=True, nulls_last=True)
            .filter(month.is_null() | (pl.int_range(pl.len()).over("accountId", month) == 0))
            ["id"])


# --- Data validation (ADR 0004: df-ahua.3 rules, df-ahua.22 reconciliation) ----------------------
# Every run checks the data BEFORE the write it protects, with validate() and a rule list below.
# A rule is data: (name, severity, check). `check` is a Polars expression that is True on every row
# breaking the rule, or a list of column names the frame must have. A column that a row rule reads
# but the frame lacks reads as all-null, as diagonal_relaxed does in raw: a "must not be null" rule
# then catches a column Pluggy stopped sending, and a rule on an optional column stays quiet.
IMPOSSIBLE = "impossible"   # validate raises: the job stops before the write, yesterday's data stays
SUSPICIOUS = "suspicious"   # recorded for the data_warnings task, which alerts; the write goes on
SAMPLE_KEYS = 5             # offending keys quoted per rule
MONEY_TOLERANCE = 0.005     # two money totals within half a centavo are equal


class DataValidationError(Exception):
    """A frame broke an `impossible` rule. The message names each rule, its count and a few keys."""


def not_in(col, values):
    # True where `col` is set and is none of `values` (a list, or a Series such as a dimension's
    # keys): an enum outside its known set, or a foreign key that does not resolve.
    return pl.col(col).is_not_null() & ~pl.col(col).is_in(pl.Series(values).implode())


def not_a_number(col):
    # True where `col` is set but does not parse as a number (raw is all text).
    return pl.col(col).is_not_null() & pl.col(col).cast(pl.Float64, strict=False).is_null()


def not_a_date(col):
    # True where `col` is set but its first 10 characters are not a YYYY-MM-DD date.
    text = pl.col(col).cast(pl.Utf8).str.slice(0, 10)
    return pl.col(col).is_not_null() & text.str.to_date("%Y-%m-%d", strict=False).is_null()


def duplicated(*cols):
    # True on every row that shares its (non-null) key with another row.
    return pl.all_horizontal([pl.col(c).is_not_null() for c in cols]) & pl.struct(list(cols)).is_duplicated()


def null_rule(col, severity=IMPOSSIBLE):
    # The rule "<col> is null", the most common one.
    return (f"{col} is null", severity, pl.col(col).is_null())


def columns_rule(cols, severity=IMPOSSIBLE, name="columns the script reads are present"):
    # The column-set rule: every column of `cols` is in the frame.
    return (name, severity, cols)


def expected_columns_rule(cols):
    # The column-set rule of a frame a script builds itself (its own schema).
    return columns_rule(cols, name="expected columns are present")


def number_rule(col):
    # The rule "<col> is not a number": a value that is set and does not parse.
    return (f"{col} is not a number", IMPOSSIBLE, not_a_number(col))


def date_rule(col):
    # The rule "<col> is not a date": a value that is set and whose day does not parse.
    return (f"{col} is not a date", IMPOSSIBLE, not_a_date(col))


def null_in_a_fetch_rule(col):
    # The rule "<col> is null on every row of a fetch": a field Pluggy stopped sending today, while
    # the older partitions still carry it (so the column itself is there). Raw frames only.
    return (f"{col} is null on every row of a fetch", SUSPICIOUS,
            pl.col(col).is_null().all().over("ingested_at"))


def _day(col):
    # The first 10 characters of `col` as a date, for raw text and typed dates alike; null when they
    # are not a YYYY-MM-DD day.
    return pl.col(col).cast(pl.Utf8).str.slice(0, 10).str.to_date("%Y-%m-%d", strict=False)


def run_day():
    # Today in UTC: the clock landing2raw names its partitions with (ingestion_date).
    return datetime.now(timezone.utc).date()


RUN_DAY_SLACK = timedelta(days=1)   # clocks of two machines (or WSL and Windows) may disagree a little
EARLIEST_DAY = date(2000, 1, 1)     # no account of this pipeline has a day before it
FAR_AHEAD = "366d"                  # an installment or a bill is never due further ahead than this


def after_run_day(col, today=None):
    # True where the day of `col` is later than the run day (`today`, run_day() by default) plus
    # RUN_DAY_SLACK: data from the future. `today` is the seam that makes the rule testable.
    return _day(col) > pl.lit((today or run_day()) + RUN_DAY_SLACK)


def implausible_day(col, ref=None, today=None):
    # True where the day of `col` is before EARLIEST_DAY or more than FAR_AHEAD after the day of the
    # column `ref` (the fetch's ingested_at), or after the run day when there is no `ref`.
    limit = _day(ref) if ref else pl.lit(today or run_day())
    return (_day(col) < pl.lit(EARLIEST_DAY)) | (_day(col) > limit.dt.offset_by(FAR_AHEAD))


# UTF-8 bytes decoded as cp1252 (the bug of df-ahua.4.4): an "Ã" or "Â" followed by what a
# UTF-8 continuation byte (0x80-0xBF) becomes in cp1252, or the replacement character. A plain
# "Ã"/"Â" is legitimate Portuguese ("SÃO", "CÂMBIO"): it is followed by a letter, never by these.
_MOJIBAKE = (r"Ã[\x80-\xBFŒœŠšŸŽžˆ˜–-›"
             r"€™ƒ]|Â[\x80-\xBF]|�")


def mojibake_rule(col):
    # The rule "<col> has broken encoding": text that went through a wrong decoding on its way here.
    return (f"{col} has broken encoding", SUSPICIOUS, pl.col(col).cast(pl.Utf8).str.contains(_MOJIBAKE))


def no_dim_account_rule(dim_account):
    # A fact row whose account_uuid is not in this run's dim_account: the report cannot name its
    # account (df-ahua.4.9). Built by each script from the dim_account it read.
    return ("account_uuid has no dim_account row", IMPOSSIBLE,
            not_in("account_uuid", dim_account["account_uuid"]))


MIRROR_WINDOW_DAYS = 3   # the two legs of a bill payment (bank debit, card inflow) are within ±3 days


def bill_payment_leg_without_pair_rule(fact, today=None):
    # A bill payment leg (etl_tag card_payment_bank or card_payment_card) with no leg of the other
    # side of equal |amount| within MIRROR_WINDOW_DAYS (df-7lp0). The pair of a healthy payment is
    # always there: when Pluggy renames the category of one side (df-sf61), the leg left alone shows
    # up here, not months later in a report. A leg of the last MIRROR_WINDOW_DAYS before the run day
    # (`today`, run_day() by default) may still get its pair, so it does not warn yet. A frame whose
    # columns are missing or of the wrong type is refused by the column rules; this one stays quiet.
    name = "a bill payment leg has no leg on the other side"
    schema = fact.schema
    if not ({"id", "etl_tag"} <= set(schema) and schema.get("date") == pl.Date
            and schema.get("amount", pl.Null).is_numeric()):
        return (name, SUSPICIOUS, pl.lit(False))
    legs = fact.filter(pl.col("etl_tag").is_in(["card_payment_bank", "card_payment_card"])).select(
        "id", "etl_tag", "date", pl.col("amount").abs().round(2).alias("_abs"))
    paired = (
        legs.join(legs.select(pl.col("etl_tag").alias("_other_tag"), pl.col("date").alias("_other_date"),
                              "_abs"), on="_abs")
        .filter((pl.col("_other_tag") != pl.col("etl_tag"))
                & ((pl.col("date") - pl.col("_other_date")).dt.total_days().abs() <= MIRROR_WINDOW_DAYS))
        ["id"]
    )
    settled = pl.col("date") < pl.lit(today or run_day()) - timedelta(days=MIRROR_WINDOW_DAYS)
    return (name, SUSPICIOUS,
            pl.col("id").is_in(legs["id"].implode()) & ~pl.col("id").is_in(paired.implode()) & settled)


def read_raw(files):
    # Combine raw partitions (all text; diagonal_relaxed unions the column sets, which differ per
    # fetch, and fills what is missing with null), each with the day of its path as `ingestion_date`
    # (A16: ingested_at is that day; the raw rules check it, df-ahua.4.1).
    return pl.concat([pl.read_parquet(f).with_columns(
        ingestion_date=pl.lit(Path(f).parent.name.split("=", 1)[1])) for f in files],
        how="diagonal_relaxed")


def describe_finding(finding):
    # "<rule>: <count> (e.g. <keys>)"; the count is offending rows, or missing columns.
    sample = f" (e.g. {', '.join(finding['keys'])})" if finding["keys"] else ""
    return f"{finding['rule']}: {finding['count']}{sample}"


def _offenders(df, check, keys):
    # (count, sample) for one rule: the offending rows and up to SAMPLE_KEYS of their keys, or the
    # missing columns of a column-set rule. Only key columns are quoted, never other values.
    if not isinstance(check, pl.Expr):
        missing = [c for c in check if c not in df.columns]
        return len(missing), missing[:SAMPLE_KEYS]
    absent = [c for c in dict.fromkeys(check.meta.root_names()) if c not in df.columns]
    frame = df.with_columns([pl.lit(None, dtype=pl.Utf8).alias(c) for c in absent])
    try:
        bad = frame.filter(check)
    except pl.exceptions.PolarsError:
        if not absent:
            raise
        return df.height, [f"missing column {c}" for c in absent]
    quoted = [k for k in keys if k in bad.columns]
    sample = bad.select(quoted).unique(maintain_order=True).head(SAMPLE_KEYS).rows() if quoted else []
    return bad.height, ["|".join("null" if v is None else str(v) for v in row) for row in sample]


def _findings_path(table):
    return _connection_root("validation") / (re.sub(r"[^A-Za-z0-9_.-]+", "_", table) + ".json")


def validate(df, table, rules, key="id"):
    # Check `df` against `rules` before the write it protects. `table` names the check in messages
    # and in its findings file; `key` is the column (or list) quoted for offending rows. Quote ids
    # and dates only: logs and alerts may be shared, so never an amount, a name, a description or
    # account_uuid (it ends in the account number's last digits).
    # - suspicious findings replace this check's file under the `validation` connection, which the
    #   data_warnings task reads to alert (an empty list when clean, so a fixed problem clears);
    # - any impossible finding raises DataValidationError listing every impossible rule broken.
    # Returns the suspicious findings.
    keys = [key] if isinstance(key, str) else list(key)
    findings = []
    for name, severity, check in rules:
        if severity not in (IMPOSSIBLE, SUSPICIOUS):
            raise ValueError(f"rule {name!r}: unknown severity {severity!r}")
        count, sample = _offenders(df, check, keys)
        if count:
            findings.append({"rule": name, "severity": severity, "count": count, "keys": sample})
    suspicious = [f for f in findings if f["severity"] == SUSPICIOUS]
    path = _findings_path(table)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"table": table, "findings": suspicious}, indent=1), encoding="utf-8")
    for f in suspicious:
        print(f"{table}: suspicious: {describe_finding(f)}")
    impossible = [f for f in findings if f["severity"] == IMPOSSIBLE]
    if impossible:
        raise DataValidationError(f"{table}: " + "; ".join(describe_finding(f) for f in impossible))
    return suspicious


def read_findings():
    # Every recorded suspicious finding, as (table, finding), for the data_warnings task.
    out = []
    for path in sorted(_connection_root("validation").glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        out.extend((record["table"], f) for f in record["findings"])
    return out


def layer_totals(before, after, by, value=None):
    # One row per `by` group with its row count (and `value` total) on each side of a write, for
    # the reconciliation rules (df-ahua.22). A group found on one side only counts 0 on the other.
    def side(df, name):
        aggs = [pl.len().alias(f"rows_{name}")]
        if value is not None:
            aggs.append(pl.col(value).sum().alias(f"total_{name}"))
        return df.group_by(by).agg(aggs)
    joined = side(before, "before").join(side(after, "after"), on=by, how="full", coalesce=True,
                                         nulls_equal=True)
    return joined.with_columns(pl.col(r"^(rows|total)_(before|after)$").fill_null(0))


# Rule lists, one per boundary. Kept as plain data so the data mutator (df-ahua.4) can import them.

# raw -> cleansed: the combined raw partitions, before latest_by_key. This is also the live check
# of Pluggy's payload: a renamed or dropped field shows up as a column of nulls.

# latest_by_key keeps the row with the greatest ingested_at, compared as text: a value that is not a
# day ("n/a") sorts above every real one and wins, as a null did (df-ahua.15.1). Data mutation,
# df-ahua.4. Two early partitions carry a full timestamp; its first 10 characters are the day.
INGESTED_AT_IS_A_DATE = date_rule("ingested_at")

# The rules every raw entity shares (df-ahua.4.1, df-ahua.4.3). A row whose ingested_at is later than
# its fetch wins latest_by_key over the next fetches, and one in the future wins every fetch to come:
# ingested_at must be the day of its partition (A16: landing2raw writes both from one clock) and not
# after the run day. The same id twice in one fetch with different content leaves latest_by_key an
# arbitrary row (an identical copy is harmless and stays quiet). Suspicious: raw is never rewritten,
# so an impossible rule would block every run after one occurrence, and the next fetch, with a later
# ingested_at, wins latest_by_key anyway.
RAW_COMMON = [
    null_rule("id"),             # df-ahua.15.2
    null_rule("ingested_at"),    # df-ahua.15.1
    INGESTED_AT_IS_A_DATE,
    ("ingested_at is not its partition's day", IMPOSSIBLE,
     pl.col("ingested_at").str.slice(0, 10) != pl.col("ingestion_date")),
    ("ingested_at is after the run day", IMPOSSIBLE, after_run_day("ingested_at")),
    ("id appears twice in one fetch with different content", SUSPICIOUS,
     pl.col("id").is_not_null() & (pl.struct(pl.all()).n_unique().over(["id", "ingested_at"]) > 1)),
]

RAW_TRANSACTIONS_RULES = RAW_COMMON + [
    columns_rule(["id", "date", "amount", "category", "categoryId", "description", "descriptionRaw",
                  "merchant", "accountName", "accountId", "ingested_at"]),
    null_rule("accountId"),
    ("date is missing or not a date", IMPOSSIBLE, pl.col("date").is_null() | not_a_date("date")),
    # An installment is dated up to a few months ahead; a century away is a broken date (df-ahua.4.7).
    ("date is more than a year after ingested_at or before 2000", SUSPICIOUS,
     implausible_day("date", "ingested_at")),
    ("amount is missing or not a number", IMPOSSIBLE,
     pl.col("amount").is_null() | not_a_number("amount")),
    number_rule("amountInAccountCurrency"),   # cleansed takes it over amount when it is set
    ("type is not DEBIT or CREDIT", SUSPICIOUS, not_in("type", ["DEBIT", "CREDIT"])),
    ("status is not POSTED or PENDING", SUSPICIOUS, not_in("status", ["POSTED", "PENDING"])),
    # A null type skips the sign rule of cleansed and the counterparty side; a null status clears
    # cash_flag (df-ahua.4.2). Suspicious: the row is incomplete, not contradictory.
    null_rule("type", SUSPICIOUS),
    null_rule("status", SUSPICIOUS),
    null_in_a_fetch_rule("description"),   # df-ahua.4.5
    null_in_a_fetch_rule("category"),
    mojibake_rule("description"),          # df-ahua.4.4
    mojibake_rule("category"),
    # A purchase in another currency carries the foreign amount in `amount` and the BRL one, converted
    # by the bank, in amountInAccountCurrency, which cleansed takes (df-ahua.3.1). Without it the
    # foreign number would be summed as BRL. Suspicious: raw is never rewritten, so an impossible rule
    # would block every run after one occurrence. A null currencyCode reads as the account's.
    ("currencyCode is not BRL and amountInAccountCurrency is null", SUSPICIOUS,
     pl.col("currencyCode").is_not_null() & (pl.col("currencyCode") != "BRL")
     & pl.col("amountInAccountCurrency").is_null()),
]

# Balances, bills and holdings are summed as BRL and have no converted value to take (df-ahua.3.1
# converted transactions only): another currency there stays suspicious.
RAW_ACCOUNTS_RULES = RAW_COMMON + [
    columns_rule(["id", "itemId", "type", "name", "balance", "currencyCode", "ingested_at"]),
    number_rule("balance"),
    null_rule("balance", SUSPICIOUS),
    null_rule("itemId", SUSPICIOUS),
    ("type is not BANK or CREDIT", SUSPICIOUS, not_in("type", ["BANK", "CREDIT"])),
    ("currencyCode is not BRL", SUSPICIOUS, pl.col("currencyCode").ne_missing("BRL")),   # df-ahua.4.6
]

# Pluggy omits a field that no row of a fetch has, so a column may legitimately be absent: the column
# rules of bills and investments are suspicious (df-ahua.4.5).
RAW_BILLS_RULES = RAW_COMMON + [
    columns_rule(["id", "accountId", "dueDate", "totalAmount", "totalAmountCurrencyCode",
                  "minimumPaymentAmount", "payments", "allowsInstallments", "ingested_at"], SUSPICIOUS),
    null_rule("accountId"),
    date_rule("dueDate"),
    ("dueDate is more than a year after ingested_at or before 2000", SUSPICIOUS,
     implausible_day("dueDate", "ingested_at")),
    number_rule("totalAmount"),
    number_rule("minimumPaymentAmount"),
    null_rule("dueDate", SUSPICIOUS),    # fact_fatura_mes skips the bill
    null_rule("totalAmount", SUSPICIOUS),
    null_rule("payments", SUSPICIOUS),
    ("allowsInstallments is not True or False", SUSPICIOUS,
     not_in("allowsInstallments", ["True", "False"])),
    null_in_a_fetch_rule("minimumPaymentAmount"),
    null_in_a_fetch_rule("allowsInstallments"),
    ("totalAmountCurrencyCode is not BRL", SUSPICIOUS,
     pl.col("totalAmountCurrencyCode").ne_missing("BRL")),
]

RAW_INVESTMENTS_RULES = RAW_COMMON + [
    columns_rule(["id", "itemId", "name", "type", "balance", "amountOriginal", "currencyCode",
                  "ingested_at"], SUSPICIOUS),
    number_rule("balance"),
    number_rule("amountOriginal"),
    number_rule("amountProfit"),
    null_rule("balance", SUSPICIOUS),
    ("type is not a Pluggy investment type", SUSPICIOUS,
     not_in("type", ["FIXED_INCOME", "EQUITY", "MUTUAL_FUND", "SECURITY", "ETF", "COE", "OTHER"])),
    null_in_a_fetch_rule("name"),
    null_in_a_fetch_rule("type"),
    null_in_a_fetch_rule("amountOriginal"),
    mojibake_rule("name"),
    ("currencyCode is not BRL", SUSPICIOUS, pl.col("currencyCode").ne_missing("BRL")),
]

# Pluggy's sign convention, which curated relies on (amount_signed, card payment legs, refunds): on a
# BANK account money in (CREDIT) is positive and money out (DEBIT) negative; on a CREDIT card a
# charge (DEBIT) is positive and a payment or refund (CREDIT) negative. Zero amounts are not judged.
_EXPECTED_SIGN = (
    pl.when((pl.col("account_type") == "BANK") == (pl.col("txn_type") == "CREDIT")).then(1).otherwise(-1)
)
_SIGN_IS_JUDGED = (pl.col("account_type").is_in(["BANK", "CREDIT"])
                   & pl.col("txn_type").is_in(["DEBIT", "CREDIT"]))

# raw -> cleansed: the transactions frame about to be merged, with `cleansed_ingested_at` (the
# ingested_at of the row already in cleansed for the same id, null for a new id) joined in.
CLEANSED_TRANSACTIONS_RULES = [
    ("accountId matches no raw account", IMPOSSIBLE, pl.col("account_type").is_null()),
    ("amount sign contradicts account and transaction type", IMPOSSIBLE,
     _SIGN_IS_JUDGED & (pl.col("amount") * _EXPECTED_SIGN < 0)),
    # df-ahua.22.1: the MERGE takes the raw row even when cleansed holds a newer one (raw restored
    # from an older backup without rebuilding cleansed). Detected, not prevented.
    ("ingested_at is older than the row already in cleansed", SUSPICIOUS,
     pl.col("ingested_at").str.slice(0, 10) < pl.col("cleansed_ingested_at").str.slice(0, 10)),
]

# raw -> cleansed: the per_account frame of gone_from_latest_fetch (df-75ol). Suspicious: the write goes
# on without removing anything from the account, and the next complete fetch removes what is gone.
FETCH_REMOVALS_RULES = [
    ("too many ids to remove from the latest fetch: none removed from the account", SUSPICIOUS,
     INCOMPLETE_FETCH),
]

# raw -> cleansed: the per-statement frame of copias_pela_fatura (df-mk25, report/conferencia_fatura.py).
# Suspicious: the bill has more entries than its total and copies could explain it, but not in one way only
# or not with a clear createdAt order, so nothing leaves; a person decides which entry is the copy.
CARD_COPIES_RULES = [
    ("card entries that look copied and the bill does not tell which: none removed", SUSPICIOUS,
     pl.col("retida")),
]

# Reconciliation (df-ahua.22), on layer_totals frames.
ROWS_KEPT_RULES = [
    ("rows lost", IMPOSSIBLE, pl.col("rows_after") < pl.col("rows_before")),
]
IDS_KEPT_RULES = ROWS_KEPT_RULES + [
    ("id written more than once", IMPOSSIBLE, pl.col("rows_after") > 1),
]
# Before: the rows a write had to delete; after: the table it left (df-75ol).
ROWS_REMOVED_RULES = [
    ("removed id still written", IMPOSSIBLE, (pl.col("rows_before") > 0) & (pl.col("rows_after") > 0)),
]
ROWS_CARRIED_RULES = [
    ("rows not carried one to one", IMPOSSIBLE, pl.col("rows_after") != pl.col("rows_before")),
]
TOTALS_CONSERVED_RULES = ROWS_CARRIED_RULES + [
    ("amount total not conserved", IMPOSSIBLE,
     (pl.col("total_after") - pl.col("total_before")).abs() > MONEY_TOLERANCE),
]

# cleansed -> curated, before each write.
DIM_ACCOUNT_RULES = [
    null_rule("pluggy_account_id"),
    # Every fact joins on it: a duplicate doubles each fact row of that account.
    ("pluggy_account_id is not unique", IMPOSSIBLE, duplicated("pluggy_account_id")),
    null_rule("account_uuid"),
    # The facts carry account_uuid: two accounts sharing one become one account in every fact and
    # in the report (data mutation, df-ahua.4).
    ("account_uuid is not unique", IMPOSSIBLE, duplicated("account_uuid")),
    ("account_uuid has no account digits", SUSPICIOUS, pl.col("account_uuid").str.ends_with("-")),
    # cleansed2curated_accounts.py falls back to an 'unknown' institution with a printed warning only.
    ("institution is a placeholder", SUSPICIOUS, pl.col("institution").str.starts_with("unknown")),
    mojibake_rule("institution"),
]

FACT_TRANSACTION_COLUMNS = [
    "id", "date", "account_uuid", "account_type", "etl_merchant_key", "description_normalized",
    "amount", "amount_signed", "category", "categoryId", "counterparty_document",
    "payment_method", "cc_bill_id", "cc_installment_number", "cc_total_installments",
    "cc_purchase_date", "status", "txn_type", "operation_type", "cash_flag", "etl_tag",
    "netting_applied", "income_source_key",
]
FACT_TRANSACTION_RULES = [
    expected_columns_rule(FACT_TRANSACTION_COLUMNS),
    null_rule("id"),
    ("id is not unique", IMPOSSIBLE, duplicated("id")),
    null_rule("account_uuid"),
    null_rule("date"),
    null_rule("amount"),
    ("amount_signed is not amount (BANK) or -amount (CREDIT)", IMPOSSIBLE,
     pl.col("amount_signed")
     != pl.when(pl.col("account_type") == "CREDIT").then(-pl.col("amount")).otherwise(pl.col("amount"))),
]

# The snapshot facts below are written as built (write_delta_overwrite accepts any schema): each
# list checks the columns the script builds, that its amounts and dates are typed, and the values
# readers sum (df-ahua.4.10). A null or negative value is suspicious; a text amount is impossible.
FACT_CARD_STATEMENT_COLUMNS = [
    "account_uuid", "statement_id", "due_date", "anomes_key", "statement_amount", "minimum_payment",
    "paid_amount", "paid_date", "payment_type", "paid_in_full", "finance_charges",
    "allows_installments", "currency", "updated_at",
]
FACT_CARD_STATEMENT_RULES = [
    expected_columns_rule(FACT_CARD_STATEMENT_COLUMNS),
    null_rule("statement_id"),
    ("statement_id is not unique", IMPOSSIBLE, duplicated("statement_id")),
    ("more than one statement per account and due month", IMPOSSIBLE,
     duplicated("account_uuid", "anomes_key")),
    null_rule("account_uuid"),
    null_rule("due_date", SUSPICIOUS),   # fact_fatura_mes skips it
    date_rule("due_date"),
    ("due_date is more than a year after the run day or before 2000", SUSPICIOUS,
     implausible_day("due_date")),
    number_rule("statement_amount"),
    number_rule("paid_amount"),
    null_rule("statement_amount", SUSPICIOUS),   # an unpaid bill has no paid_amount: that one is legal
]

FACT_FATURA_MES_COLUMNS = [
    "account_uuid", "anomes_key", "mes_inicio", "due_date", "statement_id", "valor_fatura", "valor_pago",
    "valor_pendente", "is_open",
]
FACT_FATURA_MES_RULES = [
    expected_columns_rule(FACT_FATURA_MES_COLUMNS),
    ("more than one row per account and due month", IMPOSSIBLE,
     duplicated("account_uuid", "anomes_key")),
    null_rule("account_uuid"),
    null_rule("anomes_key"),
    # The open row (charges not yet billed) has no due date; a closed bill always has one.
    ("due_date is null on a closed bill", SUSPICIOUS, pl.col("due_date").is_null() & ~pl.col("is_open")),
    date_rule("due_date"),
    ("due_date is more than a year after the run day or before 2000", SUSPICIOUS,
     implausible_day("due_date")),
    number_rule("valor_fatura"),
    null_rule("valor_fatura", SUSPICIOUS),
]

FACT_BALANCE_SNAPSHOT_COLUMNS = [
    "account_uuid", "snapshot_date", "account_type", "balance", "available", "card_outstanding",
    "available_credit_limit", "credit_limit", "minimum_payment", "statement_close_date",
    "statement_due_date", "currency",
]
FACT_BALANCE_SNAPSHOT_RULES = [
    expected_columns_rule(FACT_BALANCE_SNAPSHOT_COLUMNS),
    ("more than one row per account and day", IMPOSSIBLE, duplicated("account_uuid", "snapshot_date")),
    null_rule("account_uuid"),
    null_rule("snapshot_date"),
    date_rule("snapshot_date"),
    ("snapshot_date is after the run day", IMPOSSIBLE, after_run_day("snapshot_date")),   # df-ahua.4.7
    number_rule("balance"),
    null_rule("balance", SUSPICIOUS),
    null_rule("account_type", SUSPICIOUS),
]

# The balance check (df-z4fh.1): one row per BANK account and interval between two snapshots, keyed by
# its end `ate`; `coletado_em` is the day its `ate` snapshot first arrived.
CONFERENCIA_SALDO_COLUMNS = [
    "account_uuid", "de", "ate", "coletado_em", "saldo_de", "saldo_ate", "variacao_saldo",
    "soma_lancamentos", "n_lancamentos", "diferenca", "diferenca_acumulada", "estado",
]
CONFERENCIA_SALDO_RULES = [
    expected_columns_rule(CONFERENCIA_SALDO_COLUMNS),
    null_rule("account_uuid"),
    ("more than one row per account and interval end", IMPOSSIBLE, duplicated("account_uuid", "ate")),
    ("de is not before ate", IMPOSSIBLE, pl.col("de") >= pl.col("ate")),
    ("estado is not a known state", IMPOSSIBLE,
     pl.col("estado").is_null()
     | not_in("estado", ["fecha", "fecha_com_vizinho", "em_aberto", "historico_incompleto", "nao_fecha"])),
]


# The card check by each bill's total (df-acz4): one row per closed statement of fact_card_statement. It only
# measures: no rule here is suspicious, so a bill that does not close never warns (owner's call, df-5g8b).
CONFERENCIA_FATURA_COLUMNS = [
    "account_uuid", "statement_id", "due_date", "total_fatura", "saldo_anterior", "soma_lancamentos",
    "n_lancamentos", "diferenca_lancamentos", "estado_lancamentos", "pago", "n_pagamentos",
    "diferenca_pagamento", "estado_pagamento",
]
CONFERENCIA_FATURA_RULES = [
    expected_columns_rule(CONFERENCIA_FATURA_COLUMNS),
    null_rule("account_uuid"),
    ("more than one row per statement", IMPOSSIBLE, duplicated("statement_id")),
    ("estado_lancamentos is not a known state", IMPOSSIBLE,
     pl.col("estado_lancamentos").is_null()
     | not_in("estado_lancamentos", ["fecha", "nao_fecha", "historico_incompleto", "sem_total"])),
    ("estado_pagamento is not a known state", IMPOSSIBLE,
     pl.col("estado_pagamento").is_null()
     | not_in("estado_pagamento", ["fecha", "nao_fecha", "historico_incompleto", "a_vencer", "sem_total"])),
]


# Rank of the balance-check interval that warns, newest first: two snapshots later, a transaction posted
# after a weekend or one sync late has arrived, so an older break is a real one (df-z4fh.1).
BALANCE_WARN_RANK = 3


def new_balance_break_rule(today=None):
    # The one warning of the balance check (df-z4fh.1): the third-newest interval of an account is
    # `nao_fecha` and the account's newest snapshot arrived on the run day (`today`, run_day() by
    # default). Two snapshots later, a transaction the bank posts after a weekend or one sync late has
    # arrived (`fecha_com_vizinho`), so a healthy account stays quiet. A run without a new snapshot of
    # the account moves no interval up, so it does not repeat yesterday's warning: the run day is
    # compared exactly, without RUN_DAY_SLACK, which would repeat it on the next day. A break that
    # becomes the third-newest on a run that fails, or an older interval that changes later, gives no
    # warning; the table still shows it (docs/architecture.md).
    newest_arrival = pl.col("coletado_em").sort_by("ate").last().over("account_uuid")
    return ("the third-newest interval of an account does not close", SUSPICIOUS,
            (pl.col("estado") == "nao_fecha")
            & (pl.col("ate").rank("ordinal", descending=True).over("account_uuid") == BALANCE_WARN_RANK)
            & (newest_arrival == pl.lit(today or run_day())))


# The suspect transactions (df-z4fh.2): one row per (transaction, reason), with the balance-check interval
# it falls in. They only mark: no rule here is suspicious, so a suspect never warns (data_warnings).
LANCAMENTOS_SUSPEITOS_COLUMNS = [
    "transaction_id", "account_uuid", "date", "amount", "description", "status", "motivo",
    "primeira_coleta", "ultima_coleta", "n_coletas", "intervalo_de", "intervalo_ate", "intervalo_estado",
]
LANCAMENTOS_SUSPEITOS_RULES = [
    expected_columns_rule(LANCAMENTOS_SUSPEITOS_COLUMNS),
    null_rule("transaction_id"),
    null_rule("account_uuid"),
    ("more than one row per transaction and reason", IMPOSSIBLE, duplicated("transaction_id", "motivo")),
]

# The bank statement PDFs (df-z4fh.5): one row per entry of the statement, and one per daily balance with
# its check. They only read and record: a day that does not close is printed and kept in `fecha`, never a
# suspicious finding (an old statement would repeat it every day).
EXTRATO_COLUMNS = ["arquivo", "account_uuid", "dia", "ordem", "descricao", "valor", "saldo_do_dia"]
EXTRATO_RULES = [
    expected_columns_rule(EXTRATO_COLUMNS),
    null_rule("account_uuid"),
    null_rule("dia"),
    null_rule("valor"),
    ("more than one row per account, day and position", IMPOSSIBLE, duplicated("account_uuid", "dia", "ordem")),
]
EXTRATO_SALDOS_COLUMNS = [
    "account_uuid", "dia", "arquivo", "saldo_do_dia", "saldo_anterior", "soma_lancamentos", "n_lancamentos",
    "diferenca", "fecha",
]
EXTRATO_SALDOS_RULES = [
    expected_columns_rule(EXTRATO_SALDOS_COLUMNS),
    null_rule("account_uuid"),
    null_rule("saldo_do_dia"),
    ("more than one balance per account and day", IMPOSSIBLE, duplicated("account_uuid", "dia")),
]
# One row per PDF read, with what it gave and how many lines of its list were neither an entry nor a balance.
# Ignored lines are printed and kept here, never a finding: the file stays in the folder, so it would repeat.
EXTRATO_ARQUIVOS_COLUMNS = ["arquivo", "account_uuid", "emitido_em", "n_lancamentos", "n_saldos",
                            "linhas_ignoradas"]
EXTRATO_ARQUIVOS_RULES = [
    expected_columns_rule(EXTRATO_ARQUIVOS_COLUMNS),
    null_rule("account_uuid"),
    ("more than one row per file", IMPOSSIBLE, duplicated("arquivo")),
]

# The statement's verdict on each suspect (df-z4fh.3): one row per (transaction, reason), the statement lines no
# transaction matched, and the count of each verdict per reason. They only measure: no rule here is suspicious,
# since an old statement would repeat the same finding every day.
VEREDITO_SUSPEITOS_COLUMNS = [
    "transaction_id", "account_uuid", "date", "amount", "motivo", "veredito", "dia_banco", "extrato_dia",
    "extrato_ordem",
]
VEREDITO_SUSPEITOS_RULES = [
    expected_columns_rule(VEREDITO_SUSPEITOS_COLUMNS),
    null_rule("transaction_id"),
    null_rule("account_uuid"),
    ("more than one row per transaction and reason", IMPOSSIBLE, duplicated("transaction_id", "motivo")),
    ("veredito is not a known verdict", IMPOSSIBLE,
     pl.col("veredito").is_null()
     | not_in("veredito", ["no_extrato", "fora_do_extrato", "sem_extrato_no_periodo"])),
    ("the statement line is set exactly when the verdict is no_extrato", IMPOSSIBLE,
     (pl.col("veredito") == "no_extrato") != pl.col("extrato_dia").is_not_null()),
]
EXTRATO_SEM_LANCAMENTO_COLUMNS = ["arquivo", "account_uuid", "dia", "ordem", "descricao", "valor"]
EXTRATO_SEM_LANCAMENTO_RULES = [
    expected_columns_rule(EXTRATO_SEM_LANCAMENTO_COLUMNS),
    null_rule("account_uuid"),
    ("more than one row per account, day and position", IMPOSSIBLE, duplicated("account_uuid", "dia", "ordem")),
]
ACERTO_POR_MOTIVO_COLUMNS = ["motivo", "n", "n_no_extrato", "n_fora_do_extrato"]
ACERTO_POR_MOTIVO_RULES = [
    expected_columns_rule(ACERTO_POR_MOTIVO_COLUMNS),
    null_rule("motivo"),
    ("more than one row per reason", IMPOSSIBLE, duplicated("motivo")),
    ("the verdicts add up to more than n", IMPOSSIBLE,
     pl.col("n_no_extrato") + pl.col("n_fora_do_extrato") > pl.col("n")),
]

# Append-only: a corrupt row here has no source to rebuild it from (A11).
FACT_INVESTMENT_POSITION_COLUMNS = [
    "holding_uuid", "snapshot_date", "invested_amount", "current_value", "return", "currency",
]
FACT_INVESTMENT_POSITION_RULES = [
    expected_columns_rule(FACT_INVESTMENT_POSITION_COLUMNS),
    ("more than one row per holding and day", IMPOSSIBLE, duplicated("holding_uuid", "snapshot_date")),
    null_rule("holding_uuid"),
    null_rule("snapshot_date"),
    date_rule("snapshot_date"),
    ("snapshot_date is after the run day", IMPOSSIBLE, after_run_day("snapshot_date")),
    number_rule("current_value"),
    null_rule("current_value", SUSPICIOUS),
    ("current_value is negative", SUSPICIOUS, pl.col("current_value").cast(pl.Float64, strict=False) < 0),
]

FACT_TRANSACTION_CLASSIFIED_RULES = [
    null_rule("id"),
    ("id is not unique", IMPOSSIBLE, duplicated("id")),
]


def run_transform_subprocess(transform_path, source, target):
    # Run a sibling transform in its own process with the standard --source/--target argv —
    # exactly as the daily job invokes it. sys.executable is the active (.venv) interpreter, so
    # no hardcoded venv path. Used by reprocess.py (df-fmc) to re-derive a layer from upstream.
    subprocess.run(
        [sys.executable, str(transform_path), "--source", source, "--target", target],
        check=True,
    )


def delta_table_path(layer, table):
    return _connection_root(layer) / table


def delta_table_exists(layer, table):
    return (delta_table_path(layer, table) / "_delta_log").exists()


def read_delta_table(layer, table):
    return pl.read_delta(str(delta_table_path(layer, table)))


def write_delta_overwrite(layer, table, df):
    path = delta_table_path(layer, table)
    path.mkdir(parents=True, exist_ok=True)
    # schema_mode="overwrite": DROP+CREATE tables intentionally accept schema drift (a
    # rebuilt-every-run table redefines its own schema), unlike accumulating upserts below.
    df.write_delta(str(path), mode="overwrite", delta_write_options={"schema_mode": "overwrite"})


def upsert_delta_table(layer, table, df, keys):
    path = delta_table_path(layer, table)
    if not delta_table_exists(layer, table):
        path.mkdir(parents=True, exist_ok=True)
        # No schema_mode here (unlike write_delta_overwrite): accumulating tables must fail loud
        # on schema drift rather than silently redefine an evolving history. (First run on an
        # empty df still creates the empty, typed table — the empty-seam contract.)
        df.write_delta(str(path), mode="overwrite")
        return
    # A11: once history exists, an empty source must never lose it — an empty MERGE source is a
    # no-op that leaves prior rows intact (and skips a pointless merge).
    if df.is_empty():
        return
    (
        df.write_delta(str(path), mode="merge", delta_merge_options=_merge_options(keys))
        .when_matched_update_all()
        .when_not_matched_insert_all()
        .execute()
    )


def delete_delta_rows(layer, table, df, keys):
    # Delete the rows of the table whose `keys` match a row of `df` (a MERGE with only a delete clause;
    # rows that match nothing are left alone). No table yet, or nothing to delete: a no-op.
    if df.is_empty() or not delta_table_exists(layer, table):
        return
    (
        df.select(keys).write_delta(str(delta_table_path(layer, table)), mode="merge",
                                    delta_merge_options=_merge_options(keys))
        .when_matched_delete()
        .execute()
    )


def _merge_options(keys):
    predicate = " AND ".join(f"t.{k} = s.{k}" for k in keys)
    return {"predicate": predicate, "source_alias": "s", "target_alias": "t"}
