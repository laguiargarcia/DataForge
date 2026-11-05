import logging
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from starlette.datastructures import Headers

from dataforge.db import init_db, JobRunORM
from dataforge.api import broadcaster as _broadcaster_module
from dataforge.apps import discover_apps
from dataforge.compat import allowed_hosts, global_db_url
from .routers import workspaces, jobs, executions, stream, triggers, deploy, apps, tables

logger = logging.getLogger(__name__)

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_HOST_HEADER = re.compile(r"(\[[^\[\]]+\]|[^\[\]:]+)(?::\d+)?")


def _host_name(host: str) -> str | None:
    """Name part of a Host header, without port or IPv6 brackets; None if malformed."""
    match = _HOST_HEADER.fullmatch(host)
    return match.group(1).strip("[]").lower() if match else None


def _is_forbidden(scope) -> bool:
    headers = Headers(scope=scope)
    host = headers.get("host", "")
    allowed = allowed_hosts()
    if allowed is not None and _host_name(host) not in allowed:
        return True
    origin = headers.get("origin")
    if scope["method"] in _SAFE_METHODS or origin is None:
        return False
    _, scheme_sep, authority = origin.partition("://")
    return not scheme_sep or authority.lower() != host.lower()


class HostOriginMiddleware:
    """No login, so the browser boundary is here: refuse an unknown Host (DNS rebinding)
    and a state-changing request from another origin. CLI and Qt send no Origin.
    Plain ASGI, so SSE responses are not buffered."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and _is_forbidden(scope):
            await JSONResponse({"detail": "Forbidden"}, status_code=403)(scope, receive, send)
            return
        await self.app(scope, receive, send)


@asynccontextmanager
async def lifespan(app):
    """FastAPI lifespan: on startup, surface in-progress scheduled runs (SCHED-03 / D-10)."""
    try:
        global_engine = init_db(global_db_url())
        with Session(global_engine) as session:
            in_progress = session.query(JobRunORM).filter_by(status="running").all()
        for run in in_progress:
            broadcaster = _broadcaster_module.get_broadcaster(run.workspace)
            await broadcaster.publish({
                "workspace": run.workspace,
                "job": run.job,
                "run_id": run.id,
                "status": "running",
            })
    except Exception:
        logger.exception("Startup scan failed — daemon boot continues")

    try:
        discover_apps()  # logs a warning per broken app; never blocks startup
    except Exception:
        logger.exception("App discovery failed — daemon boot continues")

    yield
    # No shutdown work needed


app = FastAPI(title="DataForge API", lifespan=lifespan)
app.add_middleware(HostOriginMiddleware)

app.include_router(workspaces.router, prefix="/api/workspaces", tags=["workspaces"])
app.include_router(jobs.router, prefix="/api/workspaces", tags=["jobs"])
app.include_router(executions.router, prefix="/api/executions", tags=["executions"])
app.include_router(stream.router, prefix="/api/workspaces", tags=["stream"])
app.include_router(triggers.router, prefix="/api/workspaces", tags=["triggers"])
app.include_router(deploy.router, prefix="/api/workspaces", tags=["deploy"])
app.include_router(apps.router, prefix="/apps", tags=["apps"])
app.include_router(tables.router, prefix="/api/workspaces", tags=["tables"])


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/api/triggers/notify")
async def notify():
    """Accept fire-and-forget POST from CLI after scheduled run completes (D-09)."""
    logger.info("notify: received daemon wake hint from CLI")
    return {"ok": True}
