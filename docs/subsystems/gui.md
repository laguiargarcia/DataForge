---
topic: GUI
read_when: When working on the PySide6 / Qt desktop surface
subsystem: gui
last_synced_at: "2026-05-23T01:13:02Z"
related_issues: [df-0lf, df-37a, df-3qd]
---

# GUI

## API surface

<!-- AUTO:overview -->
<!-- AUTO-END:overview -->

## How to use

<!-- MANUAL:how-to-use -->
A **PySide6 (Qt)** desktop app. `dataforge/gui/__init__.py::run_app()` is the sole entry
point (wired to `dataforge-gui` in `pyproject.toml` and the `dataforge gui` CLI command):
it creates the `QApplication`, calls `instance.ensure_server()` (on `InstanceError` it shows a
`QMessageBox.critical` and returns 1), shows `MainWindow` and starts a `PresenceHeartbeat`. The admin
window is a plain client of the single local server: no tray, no embedded daemon, closing quits for real.

- `dataforge/gui/presence_client.py` — `PresenceHeartbeat` posts presence to
  `/api/instance/presence` every `HEARTBEAT_SECONDS` (async QNetworkAccessManager), sends DELETE on
  `stop()` (idempotent; aborts a beat in flight), skips a beat while one is pending (10 s transfer timeout),
  emits `server_lost` on connection refused and `server_ok` on every successful beat.
  On `server_lost` the admin window shows a banner "DataForge desligado" with a "Ligar de novo" button
  (`instance.ensure_server()` under a busy cursor, then reloads workspaces; an `InstanceError` is shown in
  a warning and the banner stays). A successful beat hides the banner by itself.
- `dataforge/gui/background.py` — the background process (hidden `dataforge background` command):
  owns the server (`DaemonThread`) and the system tray; its menu opens the admin window. The tray icon is
  `dataforge/gui/icons.py` `default_icon()` (blue square, white "D"), also the default app window icon.
- `dataforge/gui/daemon.py` — `DaemonThread` (used by the background process only) hosts the embedded FastAPI app
  (`dataforge.api:app`) via uvicorn in a background thread; `DaemonServer` suppresses
  uvicorn's signal handlers so Qt owns SIGINT/SIGTERM.
- `dataforge/gui/main_window.py` — `MainWindow` is a sidebar + `QStackedWidget` shell.
  `_load_panels()` lazily imports panel modules (only after `QApplication` exists) and adds
  one `Panel()` per section, connecting `workspace_changed` to each panel's `set_workspace`
  before `_load_workspaces()` fetches the workspace list from the local server.

**Jobs run detail.** Opened from History (or by drilling into a run), it follows
`GET /api/executions/{run_id}/stream`: the badge shows the run status, one tab per task
(`name [status]`), and each tab's log is replaced by a snapshot's task `output` when there is one;
a snapshot without `output` keeps the log (the stream sends each output once, see `api.md`). The log
appears when the task ends, since that is when the engine stores it. Before df-ahua.16.1 the stream
never sent `output` and every log stayed empty. The panel closes the stream on `success`/`failed`.

Panels under `dataforge/gui/panels/` each export `class Panel(QWidget)`:
`dashboards`, `deploy`, `jobs`, `scheduler`, `settings`, `sql`. (`job_editor.py` exports
`JobEditorPanel` and is embedded inside the `jobs` panel — it is not a sidebar section.)
<!-- MANUAL-END:how-to-use -->

## Subsystem-specific rules

<!-- MANUAL:rules -->
- Import panel/widget modules **inside** `run_app`/`_load_panels`, never at module top level —
  they pull in PySide6 widgets that are only safe to construct after `QApplication` exists.
- Each sidebar section module must export exactly `class Panel(QWidget)` so `_load_panels()`
  can instantiate them uniformly.
- Connect `workspace_changed` to every panel **before** `_load_workspaces()` emits the first
  workspace name (the documented "Pitfall 4" ordering in `main_window.py`).
- `gui/app_window.py` — `AppWindow` shows one workspace app in a `QWebEngineView` at
  `http://127.0.0.1:<port>/apps/<ws>/<app>/` (`dataforge app <ws> <app>`, `run_app_window`).
  Navigation: only **main-frame** navigation outside the app (`is_inside_app`) goes to the system
  browser, and only for `http`/`https`; iframes (data:, blob:, other origins) are always allowed, and
  other schemes (data:, blob:, javascript:, file:) are refused silently. `localhost` counts as outside
  (only `127.0.0.1` is the app's host). `target=_blank` links (`createWindow` returns a throwaway page)
  also go to the system browser (http/https only; other schemes are dropped).
  Storage: one persistent web profile per process (`app_profile()`, folder from
  `compat.webengine_profile_dir()` = `<home>/webengine`), so localStorage/IndexedDB/cookies survive a
  restart. Downloads (e.g. CSV export) are accepted into the user's Downloads folder with a status-bar
  message: "Baixado: <name>" when the download completed, "Falha ao baixar: <name>" when it was
  interrupted or cancelled.
  Icon: `info.icon`, else `gui/icons.py` `default_icon()` (blue square, white "D"), the same icon as
  the background tray.
  Geometry is saved in `QSettings("DataForge", "apps/<ws>/<app>")` on close and on `aboutToQuit`
  (Ctrl+C). Presence: `start_presence()`/`stop_presence()` drive a `PresenceHeartbeat(kind="app")`.
  Server notice: `loadFinished(False)` does NOT show the banner (a refused link, 404 or 503 is not a
  dead server); it calls `PresenceHeartbeat.beat_now()` and `server_lost` decides. The shared
  `server_lost_banner.ServerLostBanner` ("DataForge desligado" + **Ligar de novo** → `ensure_server`
  then `reload()`) is also used by `MainWindow`. `run_app_window` sets `AA_ShareOpenGLContexts` before
  the `QApplication` exists (QtWebEngine requirement), then creates it before validating the app: an
  unknown workspace or app shows `QMessageBox.critical` (and prints to stderr when there is one), since
  under `pythonw` (the desktop shortcut) a stderr message would be invisible.
<!-- MANUAL-END:rules -->

## Component diagram

<!-- AUTO:mermaid -->
<!-- AUTO-END:mermaid -->

## Past learnings

<!-- LEARNINGS:AUTO -->
- 2026-05-20: `CronPicker.current_cron()` returns UTC, converting local→UTC at the UI boundary; UTC is the canonical storage format. <!-- obs:1013 -->
- 2026-05-19: Each sidebar section module exports exactly `class Panel(QWidget)`; `_load_panels()` instantiates them uniformly. <!-- obs:918 -->
<!-- LEARNINGS:AUTO-END -->
