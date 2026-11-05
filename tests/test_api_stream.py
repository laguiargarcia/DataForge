import asyncio
import json
import pytest
import httpx
from fastapi.testclient import TestClient

from dataforge.api import app
from dataforge.api.broadcaster import get_broadcaster, _broadcasters


@pytest.fixture(autouse=True)
def clear_registry():
    _broadcasters.clear()
    yield
    _broadcasters.clear()


def test_workspace_stream_unknown_ws_returns_404(test_client):
    response = test_client.get("/api/workspaces/nonexistent/stream")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_workspace_stream_yields_event(workspace_dir):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        received = []

        async def publisher():
            await asyncio.sleep(0.05)
            await get_broadcaster("test_ws").publish(
                {"workspace": "test_ws", "job": "j", "run_id": "r1", "status": "running"}
            )
            await get_broadcaster("test_ws").publish(None)

        task = asyncio.create_task(publisher())

        async with client.stream("GET", "/api/workspaces/test_ws/stream") as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers["content-type"]
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    received.append(json.loads(line[6:]))
                    break

        await task

    assert len(received) == 1
    assert received[0]["status"] == "running"
