import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


class WorkspaceBroadcaster:
    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue] = []

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.remove(q)

    async def publish(self, event: Any) -> None:
        for q in list(self._subscribers):
            await q.put(event)


_broadcasters: dict[str, WorkspaceBroadcaster] = {}


def get_broadcaster(workspace: str) -> WorkspaceBroadcaster:
    if workspace not in _broadcasters:
        _broadcasters[workspace] = WorkspaceBroadcaster()
    return _broadcasters[workspace]
