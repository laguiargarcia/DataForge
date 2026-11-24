# data_warnings.py — task `data_warnings` of finance_daily (ADR 0004, df-ahua.3). Flat, top-to-bottom.
# Every validate() call of the run records its suspicious findings (ids only) in one file under the
# `validation` connection. This task prints them and exits 1 when there is any, so the job's
# `alert_warnings` message task (when: failed) sends them: its {failed_tasks_summary} quotes the
# last lines printed here. A suspicious finding never stops the data: the writes already happened.
from pathlib import Path

exec((Path(__file__).parent.parent / "functions" / "functions.py").read_text(encoding="utf-8"))

findings = read_findings()
for table, finding in findings:
    print(f"{table}: {describe_finding(finding)}")

if findings:
    tables = sorted({table for table, _ in findings})
    print(f"{len(findings)} suspicious finding(s) in {', '.join(tables)}")
    raise SystemExit(1)

print("data_warnings: no suspicious findings")
