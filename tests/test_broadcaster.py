import asyncio
import pytest
from dataforge.api.broadcaster import WorkspaceBroadcaster, get_broadcaster, _broadcasters


@pytest.fixture(autouse=True)
def clear_registry():
    """Isolate each test from global broadcaster state."""
    _broadcasters.clear()
    yield
    _broadcasters.clear()


@pytest.mark.anyio
async def test_subscribe_and_publish_delivers_event():
    b = WorkspaceBroadcaster()
    q = b.subscribe()
    await b.publish({"status": "running"})
    assert q.get_nowait() == {"status": "running"}


@pytest.mark.anyio
async def test_two_subscribers_each_receive_event():
    b = WorkspaceBroadcaster()
    q1 = b.subscribe()
    q2 = b.subscribe()
    payload = {"workspace": "ws", "status": "running"}
    await b.publish(payload)
    assert q1.get_nowait() == payload
    assert q2.get_nowait() == payload


@pytest.mark.anyio
async def test_unsubscribe_stops_delivery():
    b = WorkspaceBroadcaster()
    q = b.subscribe()
    b.unsubscribe(q)
    await b.publish({"status": "running"})
    assert q.empty()


@pytest.mark.anyio
async def test_publish_with_zero_subscribers_does_not_raise():
    b = WorkspaceBroadcaster()
    await b.publish({"status": "running"})  # must not raise


@pytest.mark.anyio
async def test_get_broadcaster_same_name_returns_same_instance():
    a1 = get_broadcaster("alpha")
    a2 = get_broadcaster("alpha")
    assert a1 is a2


@pytest.mark.anyio
async def test_get_broadcaster_different_names_return_distinct_instances():
    a = get_broadcaster("alpha")
    b = get_broadcaster("beta")
    assert a is not b


@pytest.mark.anyio
async def test_publish_does_not_mutate_during_iteration():
    """Unsubscribing inside a publish cycle must not raise RuntimeError."""
    b = WorkspaceBroadcaster()
    q1 = b.subscribe()
    q2 = b.subscribe()

    results = []

    async def consumer():
        event = await q1.get()
        b.unsubscribe(q1)
        results.append(event)

    task = asyncio.create_task(consumer())
    await b.publish({"status": "running"})
    await task

    assert results == [{"status": "running"}]
    assert q2.get_nowait() == {"status": "running"}
