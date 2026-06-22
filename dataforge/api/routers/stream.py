import json
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from dataforge.api.broadcaster import get_broadcaster
from dataforge.compat import resolve_workspace

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/{ws}/stream")
async def workspace_stream(ws: str):
    try:
        workspace_dir = resolve_workspace(ws)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Workspace '{ws}' not found")
    if not (workspace_dir / "workspace.yaml").exists():
        raise HTTPException(status_code=404, detail=f"Workspace '{ws}' not found")

    broadcaster = get_broadcaster(ws)
    q = broadcaster.subscribe()

    async def event_generator():
        try:
            while True:
                snapshot = await q.get()
                if snapshot is None:
                    break
                yield f"data: {json.dumps(snapshot)}\n\n"
        finally:
            broadcaster.unsubscribe(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
