import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from dataforge.db import init_db, JobRunORM
from dataforge.api import broadcaster as _broadcaster_module
from dataforge.compat import global_db_url
from .routers import workspaces, jobs, executions, stream, triggers, deploy

logger = logging.getLogger(__name__)


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

    yield
    # No shutdown work needed


app = FastAPI(title="DataForge API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO Phase 4.1: restrict to known origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(workspaces.router, prefix="/api/workspaces", tags=["workspaces"])
app.include_router(jobs.router, prefix="/api/workspaces", tags=["jobs"])
app.include_router(executions.router, prefix="/api/executions", tags=["executions"])
app.include_router(stream.router, prefix="/api/workspaces", tags=["stream"])
app.include_router(triggers.router, prefix="/api/workspaces", tags=["triggers"])
app.include_router(deploy.router, prefix="/api/workspaces", tags=["deploy"])


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/api/triggers/notify")
async def notify():
    """Accept fire-and-forget POST from CLI after scheduled run completes (D-09)."""
    logger.info("notify: received daemon wake hint from CLI")
    return {"ok": True}
