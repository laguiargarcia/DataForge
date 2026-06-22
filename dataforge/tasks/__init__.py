"""Task type registry. Built-ins register at import time in dataforge.__init__."""
from __future__ import annotations
from pathlib import Path
from typing import Protocol, ClassVar


class Handler(Protocol):
    type_name: ClassVar[str]
    required_fields: ClassVar[tuple[str, ...]]
    forces_run_failure: ClassVar[bool]

    def run(self, task, job, workspace_dir: Path, db_url: str, run_id: str | None) -> int:
        ...


_REGISTRY: dict[str, Handler] = {}


def register(handler_cls: type) -> None:
    name = handler_cls.type_name
    if name in _REGISTRY:
        raise ValueError(f"task type '{name}' already registered")
    _REGISTRY[name] = handler_cls()


def get_handler(type_name: str) -> Handler:
    try:
        return _REGISTRY[type_name]
    except KeyError:
        raise KeyError(f"unknown task type '{type_name}'; registered: {sorted(_REGISTRY)}")


def _reset_for_tests() -> None:
    _REGISTRY.clear()
