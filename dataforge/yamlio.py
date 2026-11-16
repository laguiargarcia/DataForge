"""YAML in and out of the package, through libyaml (df-n8ac.2).

PyYAML's C loader and dumper (CSafeLoader, CSafeDumper) read and write the same safe schema as
yaml.safe_load / yaml.safe_dump at about twice the speed. Every module of the package reads and
writes YAML through these two functions (the quality gate bans the yaml calls elsewhere in
dataforge/), so all of it behaves the same: one parser, one set of errors, one output format.
"""
from typing import IO, Any

import yaml


def load(stream: str | bytes | IO) -> Any:
    """yaml.safe_load, parsed by libyaml."""
    return yaml.load(stream, Loader=yaml.CSafeLoader)


def dump(data: Any, stream: IO | None = None, **kwargs: Any) -> str | None:
    """yaml.safe_dump, emitted by libyaml: plain data only, never Python object tags."""
    return yaml.dump(data, stream, Dumper=yaml.CSafeDumper, **kwargs)
