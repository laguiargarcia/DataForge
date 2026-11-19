"""YAML in and out of the package, through libyaml when PyYAML has it (df-n8ac.2).

PyYAML's C loader and dumper (CSafeLoader, CSafeDumper) read and write the same safe schema as
yaml.safe_load / yaml.safe_dump at about twice the speed. Every module of the package reads and
writes YAML through these two functions (the quality gate bans the yaml calls elsewhere in
dataforge/), so all of it behaves the same: one parser, one set of errors, one output format.

A PyYAML built without libyaml has no C classes: that is what uv installs on a Python with no
PyYAML wheel yet, built from source where libyaml is missing (Python 3.15 on Windows, df-xbn9).
There the pure-Python SafeLoader and SafeDumper read and write the same schema, only slower; the
text may differ in corner cases, and one control character (U+0085) does not survive (df-jgnp).
"""
from typing import IO, Any

import yaml

# False only in a PyYAML built without libyaml (see above).
_LIBYAML = hasattr(yaml, "CSafeLoader") and hasattr(yaml, "CSafeDumper")


def load(stream: str | bytes | IO) -> Any:
    """yaml.safe_load, parsed by libyaml when available."""
    if _LIBYAML:
        return yaml.load(stream, Loader=yaml.CSafeLoader)
    return yaml.safe_load(stream)


def dump(data: Any, stream: IO | None = None, **kwargs: Any) -> str | None:
    """yaml.safe_dump, emitted by libyaml when available: plain data only, never Python object tags."""
    if _LIBYAML:
        return yaml.dump(data, stream, Dumper=yaml.CSafeDumper, **kwargs)
    return yaml.safe_dump(data, stream, **kwargs)
