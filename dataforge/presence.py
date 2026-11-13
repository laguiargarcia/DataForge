"""Which windows (the admin, workspace app windows) are open, as announced by their heartbeats.

Every window announces itself every ``HEARTBEAT_SECONDS``; one that stays silent for
``EXPIRY_SECONDS`` is taken as closed (a crashed window never says goodbye). The registry is
in-memory and thread-safe; the clock is passed in so callers and tests control time.
"""
import threading
import time
from dataclasses import dataclass
from typing import Literal

HEARTBEAT_SECONDS = 30
EXPIRY_SECONDS = 120


@dataclass
class _Client:
    kind: str
    workspace: str | None
    app: str | None
    last_seen: float


class PresenceRegistry:
    def __init__(self, created_at: float | None = None) -> None:
        self._lock = threading.Lock()
        self._clients: dict[str, _Client] = {}
        self._created_at = time.time() if created_at is None else created_at
        # When the registry last became (or started) empty; meaningful only while it is empty.
        self._empty_since = self._created_at

    def touch(
        self, client_id: str, kind: Literal["admin", "app"], workspace: str | None, app: str | None, now: float
    ) -> None:
        with self._lock:
            self._sweep(now)
            self._clients[client_id] = _Client(kind, workspace, app, now)

    def leave(self, client_id: str) -> None:
        with self._lock:
            if self._clients.pop(client_id, None) is not None and not self._clients:
                self._empty_since = time.time()

    def count(self, now: float) -> int:
        with self._lock:
            self._sweep(now)
            return len(self._clients)

    def app_windows(self, workspace: str, app: str, now: float) -> int:
        with self._lock:
            self._sweep(now)
            return sum(1 for c in self._clients.values() if c.kind == "app" and c.workspace == workspace and c.app == app)

    def empty_since(self, now: float) -> float | None:
        """None while any window is present; else when the last one left or expired.

        If no window was ever present, the moment the registry was created, so a server nobody
        connects to can still shut itself down.
        """
        with self._lock:
            self._sweep(now)
            return None if self._clients else self._empty_since

    def clear(self) -> None:
        """Forget every window and restart the empty clock (used by tests)."""
        with self._lock:
            self._clients.clear()
            self._empty_since = time.time()

    def _sweep(self, now: float) -> None:
        """Drop expired clients; if that empties the registry, remember when the last one expired."""
        expired = [cid for cid, c in self._clients.items() if now - c.last_seen >= EXPIRY_SECONDS]
        if not expired:
            return
        last_gone = max(self._clients[cid].last_seen + EXPIRY_SECONDS for cid in expired)
        for cid in expired:
            del self._clients[cid]
        if not self._clients:
            self._empty_since = last_gone


registry = PresenceRegistry()
