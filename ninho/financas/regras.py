"""The finance editing rules, loaded from the workspace's own scripts so there is one copy of them.

Provisional: the rules still live in ``<workspace>/scripts/report`` as flat modules that import each
other by bare name (``edicao`` imports ``classificacao``). They become an importable package of
ninho's finance module when the workspace moves into ``ninho/financas`` (df-8x0m.23.14); until then
this is the only place of ninho that knows where they are.
"""
import importlib
import sys
import threading
from pathlib import Path
from types import ModuleType

_carregados: dict[Path, ModuleType] = {}
_carregando = threading.Lock()   # the server answers on several threads; the load swaps sys.modules


def edicao(workspace: Path) -> ModuleType:
    """The workspace's ``edicao`` module (the edits tables, their validation and their version)."""
    pasta = (workspace / "scripts" / "report").resolve()
    with _carregando:
        if pasta not in _carregados:
            sys.path.insert(0, str(pasta))
            try:
                for nome in ("classificacao", "edicao"):
                    sys.modules.pop(nome, None)
                _carregados[pasta] = importlib.import_module("edicao")
            finally:
                sys.path.remove(str(pasta))
        return _carregados[pasta]
