"""Embedded FastAPI daemon thread.

Runs uvicorn.Server in a background threading.Thread so Qt's event loop
(main thread) and uvicorn's asyncio loop (daemon thread) never conflict.
"""
import threading

import uvicorn


class DaemonServer(uvicorn.Server):
    """uvicorn.Server subclass that suppresses signal handler installation.

    uvicorn.run() installs SIGINT/SIGTERM handlers that conflict with Qt.
    Running Server directly and overriding install_signal_handlers avoids this.
    """

    def install_signal_handlers(self) -> None:  # noqa: D102
        pass  # Qt owns signal handling


class DaemonThread(threading.Thread):
    """Background thread hosting the embedded FastAPI/uvicorn daemon."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8000) -> None:
        super().__init__(daemon=True, name="dataforge-api-daemon")
        config = uvicorn.Config(
            "dataforge.api:app",
            host=host,
            port=port,
            log_level="info",
            reload=False,
        )
        self.server = DaemonServer(config=config)

    def run(self) -> None:
        self.server.run()  # blocks; runs its own asyncio loop internally

    def stop(self) -> None:
        self.server.should_exit = True
        self.join(timeout=5)
