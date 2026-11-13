"""Check that every field the GUI reads from a server response is one the server produces.

The GUI tests used to feed the panels payloads written by hand; the run detail ones carried a
task `output` the real stream never sent, so they passed while the screen stayed empty
(df-ahua.16.1). Here the payloads are the server's own: a test drives a panel against the real
API (tests/live_api.py) with FieldReads.install(), which wraps every `json.loads` result of the
GUI modules in dicts and lists that record each key the GUI code looks up (`get`, `[]`) and whether
the payload had it. `key in payload` is a probe for an optional field, not a read, and is not
recorded.

Lookups on one source line are alternatives of one read (`rec.get("id") or rec.get("run_id")`,
`data.get("executions", data.get("items", []))`): a line is satisfied when, at least once during the
test, one of its keys was in the payload it read. `unsatisfied()` lists the lines that never were:
a field the GUI needs and the server does not send. `never_sent()` lists every key the GUI looked
for and never found, alternatives included.
"""
import json
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path

import dataforge

_GUI_DIR = str(Path(dataforge.__file__).parent / "gui")


@dataclass
class _Site:
    keys: set = field(default_factory=set)
    found: set = field(default_factory=set)


class FieldReads:
    """Lookups the GUI made on server payloads, by source line (file, line)."""

    def __init__(self) -> None:
        self.sites: dict[tuple[str, int], _Site] = {}

    def record(self, frame, path: str, key, present: bool) -> None:
        filename = frame.f_code.co_filename
        if not filename.startswith(_GUI_DIR) or not isinstance(key, str):
            return
        site = self.sites.setdefault((filename, frame.f_lineno), _Site())
        site.keys.add(f"{path}.{key}")
        if present:
            site.found.add(f"{path}.{key}")

    def unsatisfied(self) -> list[str]:
        return sorted(f"{_where(s)}: {sorted(site.keys)}" for s, site in self.sites.items()
                      if not site.found)

    def never_sent(self) -> list[str]:
        return sorted({k for site in self.sites.values() for k in site.keys - site.found})

    def read(self) -> set[str]:
        """Every key path the GUI looked up, found or not."""
        return {k for site in self.sites.values() for k in site.keys}

    def wrap(self, value, path: str = "$"):
        if isinstance(value, dict):
            return _Dict({k: self.wrap(v, f"{path}.{k}") for k, v in value.items()}, self, path)
        if isinstance(value, list):
            return _List(self.wrap(v, f"{path}[]") for v in value)
        return value

    def install(self, monkeypatch, *modules) -> None:
        """Make each module's `json.loads` return recorded payloads."""
        shim = types.SimpleNamespace(**{n: getattr(json, n) for n in dir(json)
                                        if not n.startswith("_")})
        shim.loads = lambda *args, **kwargs: self.wrap(json.loads(*args, **kwargs))
        for module in modules:
            monkeypatch.setattr(module, "json", shim)


def _where(site: tuple[str, int]) -> str:
    return f"{Path(site[0]).relative_to(Path(_GUI_DIR).parent.parent).as_posix()}:{site[1]}"


class _Dict(dict):
    def __init__(self, data: dict, reads: FieldReads, path: str) -> None:
        super().__init__(data)
        self._reads, self._path = reads, path

    def get(self, key, default=None):
        self._reads.record(sys._getframe(1), self._path, key, key in self.keys())
        return super().get(key, default)

    def __getitem__(self, key):
        self._reads.record(sys._getframe(1), self._path, key, key in self.keys())
        return super().__getitem__(key)


class _List(list):
    pass
