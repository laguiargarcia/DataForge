"""Runs one workspace app's ASGI backend under uvicorn, and ends it when its supervisor's process ends.

``BackendSupervisor`` starts this module with a pipe as stdin and keeps the write end. However
that process ends (a clean exit, a native crash, Stop-Process, SIGKILL), the OS closes the write
end, the read here returns EOF and the backend stops: it never outlives the server that owns it
(df-xy7c). Usage: ``python -m dataforge.app_backend_host <module:attr> <port>``, from the app folder.
"""
import os
import sys
import threading

import uvicorn

HOST = "127.0.0.1"
EXIT_GRACE_SECONDS = 5


def wait_for_eof(fd: int = 0) -> bool:
    """Block until ``fd`` reaches end of file. False when it cannot be read at all."""
    while True:
        try:
            chunk = os.read(fd, 4096)
        except OSError:
            return False
        if not chunk:
            return True


def _stop_with_parent(server: uvicorn.Server) -> None:
    if not wait_for_eof():
        print("app backend: stdin is not readable, so this backend will not stop with its supervisor",
              file=sys.stderr, flush=True)
        return
    server.should_exit = True
    # A request that never finishes would hold the graceful shutdown: give it a bounded time.
    exit_timer = threading.Timer(EXIT_GRACE_SECONDS, os._exit, args=(0,))
    exit_timer.daemon = True
    exit_timer.start()


def main(argv: list[str]) -> None:
    app, port = argv
    server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=int(port)))
    threading.Thread(target=_stop_with_parent, args=(server,), daemon=True,
                     name="dataforge-app-backend-parent-watch").start()
    server.run()


if __name__ == "__main__":
    main(sys.argv[1:])
