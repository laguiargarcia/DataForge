import asyncio
import logging
import re
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from fastapi.datastructures import Headers

from dataforge.db import init_db, JobRunORM
from dataforge.api import broadcaster as _broadcaster_module
from dataforge import instance
from dataforge.app_backends import supervisor
from dataforge.presence import registry
from dataforge.apps import discover_apps
from dataforge.compat import allowed_hosts, global_db_url
from dataforge.orphans import reconcile_or_log
from .routers import workspaces, jobs, executions, stream, triggers, deploy, apps, tables, instance as instance_router

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


SWEEP_SECONDS = 10


async def _sweep_backends() -> None:
    """Every SWEEP_SECONDS stop app backends whose windows closed or that sat idle."""
    while True:
        await asyncio.sleep(SWEEP_SECONDS)
        try:
            await asyncio.to_thread(supervisor.sweep, time.time(), registry)
        except Exception:
            logger.exception("App backend sweep failed")


@asynccontextmanager
async def lifespan(app):
    """FastAPI lifespan: on startup, fail the runs whose process provably died (df-bw7), then
    surface the runs still in progress, e.g. scheduled runs in their own process (SCHED-03 / D-10)."""
    reconcile_or_log()
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

    sweeper = asyncio.create_task(_sweep_backends())
    try:
        yield
    finally:
        sweeper.cancel()
        await asyncio.gather(sweeper, return_exceptions=True)
        await apps.close_client()
        await asyncio.to_thread(supervisor.stop_all)


_WORKSPACES_PREFIX = "/api/workspaces"

app = FastAPI(title="DataForge API", lifespan=lifespan)
app.add_middleware(HostOriginMiddleware)

app.include_router(workspaces.router, prefix=_WORKSPACES_PREFIX, tags=["workspaces"])
app.include_router(jobs.router, prefix=_WORKSPACES_PREFIX, tags=["jobs"])
app.include_router(executions.router, prefix="/api/executions", tags=["executions"])
app.include_router(stream.router, prefix=_WORKSPACES_PREFIX, tags=["stream"])
app.include_router(triggers.router, prefix=_WORKSPACES_PREFIX, tags=["triggers"])
app.include_router(deploy.router, prefix=_WORKSPACES_PREFIX, tags=["deploy"])
app.include_router(apps.router, prefix="/apps", tags=["apps"])
app.include_router(tables.router, prefix=_WORKSPACES_PREFIX, tags=["tables"])
app.include_router(instance_router.router, prefix="/api/instance", tags=["instance"])


@app.get("/health")
async def health():
    return instance.health_payload()


@app.post("/api/triggers/notify")
async def notify():
    """Accept fire-and-forget POST from CLI after scheduled run completes (D-09)."""
    logger.info("notify: received daemon wake hint from CLI")
    return {"ok": True}
