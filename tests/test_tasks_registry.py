import pytest
from dataforge.tasks import register, get_handler, Handler


@pytest.fixture(autouse=True)
def _isolate_registry_for_this_file():
    """Save + restore _REGISTRY around each registry test.
    Scoped to this file only — other tests rely on the built-ins
    registered at `import dataforge` time."""
    from dataforge.tasks import _REGISTRY
    saved = dict(_REGISTRY)
    _REGISTRY.clear()
    yield
    _REGISTRY.clear()
    _REGISTRY.update(saved)


class _DummyHandler:
    type_name = "dummy"
    required_fields = ()
    forces_run_failure = False

    def run(self, task, job, workspace_dir, db_url, run_id):
        return 0


def test_register_and_lookup():
    register(_DummyHandler)
    assert isinstance(get_handler("dummy"), _DummyHandler)


def test_unknown_type_raises():
    with pytest.raises(KeyError, match="unknown task type"):
        get_handler("never_registered")


def test_double_register_raises():
    register(_DummyHandler)
    with pytest.raises(ValueError, match="already registered"):
        register(_DummyHandler)
