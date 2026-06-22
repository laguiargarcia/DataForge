"""Reprocess job (df-fmc) — re-derive a downstream layer from its durable upstream.

Drops the target Delta table and re-runs the existing upstream->target transform over ALL
durable upstream history, so a newly-added column or edited derivation rule applies across the
full history, not just rows ingested after the change.

Lossless because raw is the immutable parquet system-of-record: its partitions accumulate and
are never rewritten, and the raw2cleansed* transforms glob every partition (incl. aged-out ids
Pluggy no longer returns). Manual/explicit only — NEVER scheduled.

  reprocess.py --source raw --target cleansed --entity transactions|accounts|investments|bills|all
  reprocess.py --source raw --target cleansed --entity all --dry-run

Spec: docs/superpowers/specs/2026-06-04-reprocess-job-design.md
"""

import argparse
import shutil
from pathlib import Path

exec(open(Path(__file__).parent / "functions" / "functions.py").read())  # noqa: S102

# entity -> (transform script relative to scripts/, target Delta table). raw -> cleansed only;
# curated is already DROP+CREATE every run, so it needs no reprocess.
ROUTING = {
    "transactions": ("raw2cleansed/raw2cleansed.py", "transactions"),
    "accounts": ("raw2cleansed/raw2cleansed_accounts.py", "accounts"),
    "investments": ("raw2cleansed/raw2cleansed_investments.py", "investments"),
    "bills": ("raw2cleansed/raw2cleansed_bills.py", "bills"),
}

parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
parser.add_argument("--source", required=True, help="upstream connection to re-derive from (e.g. raw)")
parser.add_argument("--target", required=True, help="downstream connection to rebuild (e.g. cleansed)")
parser.add_argument("--entity", required=True,
                    help="transactions | accounts | investments | bills | all")
parser.add_argument("--dry-run", action="store_true",
                    help="print the drops + transforms without executing them")
args = parser.parse_args()

# Law: never rebuild the immutable system of record. raw is a parquet_dir connection.
if _connection_config(args.target)["type"] == "parquet_dir":
    raise SystemExit(
        f"reprocess: refusing to rebuild parquet_dir target {args.target!r} — it is the "
        f"immutable system of record, not a derivable layer."
    )

if args.entity == "all":
    entities = list(ROUTING)
elif args.entity in ROUTING:
    entities = [args.entity]
else:
    raise SystemExit(
        f"reprocess: unknown entity {args.entity!r} (expected one of "
        f"{', '.join(ROUTING)} or 'all')."
    )

scripts_root = Path(__file__).parent
for entity in entities:
    transform_rel, table = ROUTING[entity]
    transform_path = scripts_root / transform_rel

    if delta_table_exists(args.target, table):
        n_before = read_delta_table(args.target, table).height
        target_dir = delta_table_path(args.target, table)
        print(f"{entity}: dropping {args.target}.{table} ({n_before} rows) at {target_dir}")
        if not args.dry_run:
            shutil.rmtree(target_dir)
    else:
        print(f"{entity}: {args.target}.{table} absent — nothing to drop")

    print(f"{entity}: re-deriving via {transform_rel} ({args.source} -> {args.target})")
    if args.dry_run:
        continue
    run_transform_subprocess(transform_path, args.source, args.target)
    if delta_table_exists(args.target, table):
        n_after = read_delta_table(args.target, table).height
        print(f"{entity}: {n_after} rows written to {args.target}.{table}")
    else:
        # A transform can legitimately decline to write (e.g. raw2cleansed exits early when no
        # raw partition exists, to avoid wiping accumulated history). Report rather than crash.
        print(f"{entity}: {args.target}.{table} not written (transform produced no table)")
