---
topic: compat.py path and database URL discipline
read_when: Touching anything that constructs filesystem paths, reads DATAFORGE_HOME, or builds a SQLite/database URL
subsystem: compat
last_synced_at: "2026-05-23T01:13:02Z"
related_issues: [df-3ul]
code:
  - dataforge/compat.py
status: pronto
---

# compat.py

`dataforge/compat.py` is the single sanctioned interface for path and database URL construction in DataForge. All path and URL logic must route through this module.

## Path and database URL discipline

All path and database URL logic goes through `dataforge/compat.py`. No raw
`os.environ.get('DATAFORGE_HOME')` or inline `sqlite:///` f-strings elsewhere in the codebase.
Always use `from dataforge.compat import data_dir, db_url` for any path/URL construction.

Public API (the only sanctioned interface for path/URL work):

- `data_dir() -> Path` — returns `DATAFORGE_HOME` if set, else `platformdirs.user_data_dir("DataForge")`
- `db_url(path: Path) -> str` — returns `sqlite:///` URL with forward slashes (Windows-safe via `as_posix()`)
- `resolve_db(db, workspace_path) -> str` — returns `db` if given, else `db_url(workspace_path / "dataforge.db")`
- `resolve_workspace(name) -> Path` — resolves workspace by name or CWD detection
- `list_workspaces() -> list[Path]` — enumerates the workspaces under `data_dir() / "workspaces/"` whose folder name `resolve_workspace` accepts; a folder with any other name is skipped with a warning
- `locks_dir() -> Path` / `logs_dir() -> Path` — `data_dir()/locks` (run locks, `instance-start.lock`;
  `run_lock_path(ws)` = `locks_dir()/<ws>.lock`) and `data_dir()/logs` (`background.log`, `gui.log`),
  both created on demand. Never build `data_dir() / "locks"` or `/ "logs"` inline.

Never read `DATAFORGE_HOME` directly outside `dataforge/compat.py`. Never construct inline
`sqlite:///` f-strings — use `db_url()` from `dataforge.compat`.

## Component diagram

<!-- AUTO:mermaid -->
<!-- AUTO-END:mermaid -->

## Past learnings

<!-- LEARNINGS:AUTO -->
- 2026-05-19: All path/URL construction goes through `dataforge.compat`; `registry.py` was deleted and 8 callers migrated. <!-- obs:907 -->
- 2026-05-19: `compat.db_url()` uses `Path.resolve().as_posix()` so SQLite URLs are valid on Windows (backslash fix). <!-- obs:907 -->
- 2026-05-19: `data_dir()` TOCTOU race in directory creation fixed (WR-03); tests alias `db_url`→`make_db_url` to avoid fixture collision (CR-02). <!-- obs:904 -->
<!-- LEARNINGS:AUTO-END -->
