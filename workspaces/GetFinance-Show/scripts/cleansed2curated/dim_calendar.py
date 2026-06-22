import argparse
import polars as pl
from datetime import date, timedelta
from pathlib import Path

exec(open(Path(__file__).parent.parent / "functions" / "functions.py").read())  # noqa: S102

parser = argparse.ArgumentParser()
parser.add_argument("--start", default="2024-01-01")
# --end is an optional override only. Left unset (the normal daily case) the end bound is
# DERIVED from the data so the calendar always covers every fact date: see _data_driven_end().
parser.add_argument("--end", default=None)
parser.add_argument("--source")  # accepted/ignored — daily job passes --source/--target uniformly
parser.add_argument("--target", default="curated")
args = parser.parse_args()

# Fact tables whose date columns the calendar must span. Each is read defensively: the calendar
# task runs with depends_on: [] (its own Delta dir, no shared lock) so on a fresh workspace — or
# the very first run — a fact table may not exist yet, in which case it simply contributes nothing.
FACT_DATE_COLUMNS = {
    "fact_transaction": "date",
    "fact_balance_snapshot": "snapshot_date",
    "fact_investment_position": "snapshot_date",
}


def _data_driven_end(start: date) -> date:
    # Cover through the latest fact date AND a small forward buffer (end of the current month) so
    # same-day rows landing before the calendar is rebuilt still have a member, and the whole
    # current month is always present for "latest snapshot in the month" DAX. Never earlier than
    # start. No hardcoded terminal year — the bound floats with the data.
    candidates = [start]
    today = date.today()
    # Forward buffer: last day of the current month (next month's day-1 minus one day).
    first_of_next = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
    candidates.append(first_of_next - timedelta(days=1))
    for table, col in FACT_DATE_COLUMNS.items():
        if not delta_table_exists(args.target, table):
            continue
        df = read_delta_table(args.target, table)
        if col not in df.columns or df.height == 0:
            continue
        col_max = df.select(pl.col(col).cast(pl.Date, strict=False).max()).item()
        if col_max is not None:
            candidates.append(col_max)
    return max(candidates)

MONTH_NAMES_PT = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março",    4: "Abril",
    5: "Maio",    6: "Junho",     7: "Julho",     8: "Agosto",
    9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro",
}

DAY_NAMES_PT = {
    0: "Segunda-feira", 1: "Terça-feira", 2: "Quarta-feira",
    3: "Quinta-feira",  4: "Sexta-feira", 5: "Sábado", 6: "Domingo",
}

start = date.fromisoformat(args.start)
end = date.fromisoformat(args.end) if args.end else _data_driven_end(start)
rows = []
d = start
while d <= end:
    rows.append((
        int(d.strftime("%Y%m%d")),
        d,
        d.year,
        (d.month - 1) // 3 + 1,
        d.month,
        MONTH_NAMES_PT[d.month],
        d.day,
        d.weekday() + 1,
        DAY_NAMES_PT[d.weekday()],
        d.isocalendar()[1],
        1 if d.weekday() >= 5 else 0,
        d.year * 100 + d.month,
        f"{MONTH_NAMES_PT[d.month][:3].lower()}/{d.year}",
    ))
    d += timedelta(days=1)

df = pl.DataFrame(
    rows,
    schema=[
        ("date_id", pl.Int64),
        ("date", pl.Date),
        ("year", pl.Int64),
        ("quarter", pl.Int64),
        ("month", pl.Int64),
        ("month_name", pl.Utf8),
        ("day", pl.Int64),
        ("day_of_week", pl.Int64),
        ("day_name", pl.Utf8),
        ("week_of_year", pl.Int64),
        ("is_weekend", pl.Int64),
        ("anomes_key", pl.Int64),
        ("Mes_Ano", pl.Utf8),
    ],
    orient="row",
)

write_delta_overwrite("curated", "dim_calendar", df)

print(f"dim_calendar: {len(rows)} rows ({start} to {end}) -> curated")
