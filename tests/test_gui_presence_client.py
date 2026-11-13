"""PresenceHeartbeat: the admin/app window tells the server it is open (Task 8)."""
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

pytest.importorskip("PySide6")


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(["test", "-platform", "offscreen"])


@pytest.fixture
def server():
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def _reply(self):
            length = int(self.headers.get("content-length") or 0)
            body = self.rfile.read(length).decode() if length else ""
            seen.append((self.command, self.path, json.loads(body) if body else None))
            self.send_response(204)
            self.end_headers()

        do_POST = do_DELETE = _reply

        def log_message(self, *args):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv.server_address[1], seen
    srv.shutdown()
    srv.server_close()
    t.join(timeout=5)


def _pump(cond, timeout=5.0):
    from PySide6.QtWidgets import QApplication
    end = time.monotonic() + timeout
    while time.monotonic() < end and not cond():
        QApplication.processEvents()
        time.sleep(0.01)
    return cond()


def test_heartbeat_envia_presenca_e_saida(server):
    _app()
    from dataforge.gui.presence_client import PresenceHeartbeat
    port, seen = server
    hb = PresenceHeartbeat(port, "admin")
    hb.start()
    assert _pump(lambda: len(seen) >= 1)
    method, path, body = seen[0]
    assert (method, path) == ("POST", "/api/instance/presence")
    assert body["client_id"] == hb.client_id
    assert body["kind"] == "admin"
    hb.stop()
    assert ("DELETE", f"/api/instance/presence/{hb.client_id}", None) in seen


def test_heartbeat_usa_o_intervalo_do_presence(server):
    _app()
    from dataforge import presence
    from dataforge.gui.presence_client import PresenceHeartbeat
    port, _ = server
    hb = PresenceHeartbeat(port, "app", workspace="w", app="a")
    hb.start()
    assert hb._timer.interval() == presence.HEARTBEAT_SECONDS * 1000
    hb.stop()
    assert not hb._timer.isActive()


def test_heartbeat_emite_server_lost():
    _app()
    from dataforge.gui.presence_client import PresenceHeartbeat
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing listens here: connection refused
    hb = PresenceHeartbeat(port, "admin")
    lost = []
    hb.server_lost.connect(lambda: lost.append(1))
    hb.start()
    try:
        assert _pump(lambda: lost)
    finally:
        hb.stop()


def test_heartbeat_emite_server_ok_em_sucesso(server):
    _app()
    from dataforge.gui.presence_client import PresenceHeartbeat
    port, _ = server
    hb = PresenceHeartbeat(port, "admin")
    ok = []
    hb.server_ok.connect(lambda: ok.append(1))
    hb.start()
    try:
        assert _pump(lambda: ok)
    finally:
        hb.stop()


def test_heartbeat_nao_empilha_beats_e_usa_timeout(server):
    _app()
    from dataforge.gui.presence_client import PresenceHeartbeat
    port, seen = server
    hb = PresenceHeartbeat(port, "admin")
    try:
        hb._beat()
        hb._beat()  # first still pending: skipped
        assert len(hb._inflight) == 1
        reply = next(iter(hb._inflight))
        assert reply.request().transferTimeout() == 10_000
        assert _pump(lambda: len(seen) >= 1 and not hb._inflight)
        assert len(seen) == 1
    finally:
        hb.stop()


def test_heartbeat_stop_aborta_beat_pendente_e_e_idempotente(server):
    _app()
    from dataforge.gui.presence_client import PresenceHeartbeat
    port, seen = server
    hb = PresenceHeartbeat(port, "admin")
    hb._beat()
    from shiboken6 import isValid
    pending = next(iter(hb._inflight))
    hb.stop()
    assert not isValid(pending) or pending.isFinished()  # aborted before the DELETE went out
    assert not hb._inflight
    deletes = lambda: [x for x in seen if x[0] == "DELETE"]
    assert len(deletes()) == 1
    t0 = time.monotonic()
    hb.stop()
    assert time.monotonic() - t0 < 0.5
    _pump(lambda: False, timeout=0.2)
    assert len(deletes()) == 1


def test_beat_now_envia_ja_e_respeita_beat_em_voo(server):
    _app()
    from dataforge.gui.presence_client import PresenceHeartbeat
    port, seen = server
    hb = PresenceHeartbeat(port, "admin")
    try:
        hb.beat_now()
        hb.beat_now()  # first still pending: skipped
        assert len(hb._inflight) == 1
        assert _pump(lambda: len(seen) >= 1 and not hb._inflight)
        assert [m for m, *_ in seen] == ["POST"]
    finally:
        hb.stop()
