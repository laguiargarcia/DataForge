"""Window presence and the shutdown request of this DataForge server."""
import time
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, model_validator

from dataforge import compat, instance
from dataforge.presence import registry

router = APIRouter()


class PresenceBody(BaseModel):
    client_id: str
    kind: Literal["admin", "app"]
    workspace: str | None = None
    app: str | None = None

    @model_validator(mode="after")
    def _app_needs_workspace_and_app(self):
        if self.kind == "app" and not (compat.valid_name(self.workspace) and compat.valid_name(self.app)):
            raise ValueError("kind 'app' requires a valid workspace and app")
        return self


@router.post("/presence", status_code=204)
def post_presence(body: PresenceBody) -> Response:
    registry.touch(body.client_id, body.kind, body.workspace, body.app, now=time.time())
    return Response(status_code=204)


@router.delete("/presence/{client_id}", status_code=204)
def delete_presence(client_id: str) -> Response:
    registry.leave(client_id)
    return Response(status_code=204)


@router.post("/shutdown")
def shutdown(force: bool = False) -> JSONResponse:
    if instance.server_jobs() > 0 and not force:
        raise HTTPException(status_code=409, detail="há job rodando")
    if not instance.request_shutdown():
        raise HTTPException(status_code=409, detail="este servidor não pode ser parado por aqui; use Ctrl+C")
    return JSONResponse(status_code=202, content={"ok": True})
