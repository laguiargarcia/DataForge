"""Presence heartbeat: tells the local server that a window is open.

The server shuts itself down when no window and no job is left, so every window
(the admin one, an app one) repeats ``POST /api/instance/presence`` every
``HEARTBEAT_SECONDS`` and sends ``DELETE`` when it closes. Async on the Qt thread,
except the final DELETE, which waits a bounded moment so it is sent before exit.
"""
from __future__ import annotations

import json
from uuid import uuid4

from PySide6.QtCore import QByteArray, QEventLoop, QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from dataforge.presence import HEARTBEAT_SECONDS

_LEAVE_WAIT_MS = 1000
_BEAT_TIMEOUT_MS = 10_000


class PresenceHeartbeat(QObject):
    """Periodic presence notice.

    ``server_lost`` fires when the connection is refused; ``server_ok`` on every
    successful beat (so a window can drop its "server off" notice by itself).
    """

    server_lost = Signal()
    server_ok = Signal()

    def __init__(self, port: int, kind: str, workspace: str | None = None,
                 app: str | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._port = port
        self._body = {"client_id": uuid4().hex, "kind": kind, "workspace": workspace, "app": app}
        self.client_id: str = self._body["client_id"]
        self._nam = QNetworkAccessManager(self)
        self._inflight: set[QNetworkReply] = set()
        self._stopped = False
        self._timer = QTimer(self)
        self._timer.setInterval(HEARTBEAT_SECONDS * 1000)
        self._timer.timeout.connect(self._beat)

    def _url(self, suffix: str = "") -> QUrl:
        return QUrl(f"http://127.0.0.1:{self._port}/api/instance/presence{suffix}")

    def start(self) -> None:
        self._beat()
        self._timer.start()

    def beat_now(self) -> None:
        """Beat right away (e.g. a page failed to load: let ``server_lost`` say whether the server is gone).
        Skipped when a beat is on the wire or the heartbeat was stopped."""
        if not self._stopped:
            self._beat()

    def _beat(self) -> None:
        if self._inflight:  # the previous beat is still on the wire
            return
        req = QNetworkRequest(self._url())
        req.setTransferTimeout(_BEAT_TIMEOUT_MS)
        req.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        reply = self._nam.post(req, QByteArray(json.dumps(self._body).encode()))
        self._inflight.add(reply)
        reply.finished.connect(lambda: self._on_beat(reply))

    def _on_beat(self, reply: QNetworkReply) -> None:
        self._inflight.discard(reply)
        error = reply.error()
        reply.deleteLater()
        if error == QNetworkReply.NetworkError.NoError:
            self.server_ok.emit()
        elif error == QNetworkReply.NetworkError.ConnectionRefusedError:
            self.server_lost.emit()

    def stop(self) -> None:
        """Stop beating and tell the server this window left (bounded wait). Idempotent."""
        if self._stopped:
            return
        self._stopped = True
        self._timer.stop()
        for pending in self._inflight:  # abort beats still on the wire; their slot must not fire late
            pending.finished.disconnect()
            pending.abort()
            pending.deleteLater()
        self._inflight.clear()
        reply = self._nam.deleteResource(QNetworkRequest(self._url(f"/{self.client_id}")))
        loop = QEventLoop(self)
        reply.finished.connect(loop.quit)
        QTimer.singleShot(_LEAVE_WAIT_MS, loop.quit)
        loop.exec()
        if not reply.isFinished():
            reply.abort()
        reply.deleteLater()
