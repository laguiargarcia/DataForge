---
topic: Workspace Scripts Conventions
read_when: When writing workspace-level scripts (functions.py, argparse params, logging)
last_synced_at: "2026-05-23T01:13:02Z"
related_issues: [df-762, df-t7v]
transversal: true
---

# Workspace Scripts Conventions

## functions.py

All workspace scripts share a `functions/functions.py` (a `functions/` subdir under
`scripts/`) loaded via `exec(...)`. The relative path depends on script depth:

```python
# Scripts under scripts/<layer>/x.py (e.g. scripts/cleansed/foo.py):
exec((Path(__file__).parent.parent / "functions" / "functions.py").read_text(encoding="utf-8"))

# Scripts directly under scripts/x.py:
exec((Path(__file__).parent / "functions" / "functions.py").read_text(encoding="utf-8"))
```

Write it exactly so (df-g4z7): `read_text` closes the file (the older `exec(open(...).read())` left
it open, which the tests now report as a leak), the tests recognize this line to drop it
(`compile_script` in the workspace's `tests/_delta_util.py`), and coverage excludes it by its text
(`[tool.coverage.report]` in `pyproject.toml`). A script that opens a connection closes it on the
failure path too: `with closing(abrir(...)) as conn:`.

Rules:
- All imports go at the **top of `functions.py`**, never inside functions
- Ask before adding heavy deps not already in `requirements.txt`

## Params via argparse

Engine passes task `params` as `--key value` CLI args to each script. Scripts receive them via `argparse`:

```python
import argparse
parser = argparse.ArgumentParser()
parser.add_argument("--source")
parser.add_argument("--target")
args = parser.parse_args()
```
