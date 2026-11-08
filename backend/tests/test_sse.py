"""The broadcaster, and the stream route built on it."""

import asyncio
import json
import threading

import pytest

from app.api.routes import event_stream
from app.api.routes import system as system_routes
from app.sse import SSEBroadcaster, format_message


class TestFormatMessage:
    def test_it_is_the_shape_eventsource_response_expects(self):
        assert format_message("weather.updated", {"a": 1}) == {
            "event": "weather.updated",
            "data": json.dumps({"a": 1}),
        }

    def test_no_data_is_an_empty_object_rather_than_null(self):
        """The panel JSON.parses this."""
        assert json.loads(format_message("x")["data"]) == {}


async def subscribed(broadcaster: SSEBroadcaster):
    """A subscriber that is actually registered by the time this returns."""
    stream = broadcaster.subscribe()
    pending = asyncio.ensure_future(anext(stream))
    await asyncio.sleep(0)
    return stream, pending


class TestBroadcaster:
    def test_every_subscriber_gets_every_message(self):
        async def scenario():
            broadcaster = SSEBroadcaster()
            broadcaster.bind_loop(asyncio.get_running_loop())
            first, first_pending = await subscribed(broadcaster)
            second, second_pending = await subscribed(broadcaster)

            broadcaster.publish("events.updated")

            results = await asyncio.wait_for(asyncio.gather(first_pending, second_pending), 1)
            await first.aclose()
            await second.aclose()
            return results

        expected = format_message("events.updated")
        assert asyncio.run(scenario()) == [expected, expected]

    def test_messages_arrive_in_order(self):
        async def scenario():
            broadcaster = SSEBroadcaster()
            broadcaster.bind_loop(asyncio.get_running_loop())
            stream, pending = await subscribed(broadcaster)
            for name in ("a", "b", "c"):
                broadcaster.publish(name)
            received = [await asyncio.wait_for(pending, 1)]
            received.append(await asyncio.wait_for(anext(stream), 1))
            received.append(await asyncio.wait_for(anext(stream), 1))
            await stream.aclose()
            return [message["event"] for message in received]

        assert asyncio.run(scenario()) == ["a", "b", "c"]

    def test_a_publish_from_a_scheduler_thread_reaches_the_loop(self):
        """Every APScheduler job publishes from a worker thread."""

        async def scenario():
            broadcaster = SSEBroadcaster()
            broadcaster.bind_loop(asyncio.get_running_loop())
            stream, pending = await subscribed(broadcaster)
            threading.Thread(target=broadcaster.publish, args=("heartbeat",)).start()
            message = await asyncio.wait_for(pending, 1)
            await stream.aclose()
            return message

        assert asyncio.run(scenario())["event"] == "heartbeat"

    def test_a_publish_before_the_loop_is_bound_is_dropped_not_fatal(self):
        SSEBroadcaster().publish("events.updated")

    def test_a_panel_that_leaves_is_forgotten(self):
        """Otherwise a tab reloading for months leaks one queue per reload, and
        every publish grows all of them forever."""

        async def scenario():
            broadcaster = SSEBroadcaster()
            broadcaster.bind_loop(asyncio.get_running_loop())
            stream, pending = await subscribed(broadcaster)
            during = len(broadcaster._subscribers)
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
            await stream.aclose()
            return during, len(broadcaster._subscribers)

        assert asyncio.run(scenario()) == (1, 0)


class FakeRequest:
    def __init__(self, disconnected_after: int | None = None) -> None:
        self._remaining = disconnected_after

    async def is_disconnected(self) -> bool:
        if self._remaining is None:
            return False
        self._remaining -= 1
        return self._remaining < 0


@pytest.fixture
def private_broadcaster(monkeypatch) -> SSEBroadcaster:
    broadcaster = SSEBroadcaster()
    monkeypatch.setattr(system_routes, "broadcaster", broadcaster)
    monkeypatch.setattr(system_routes, "heartbeat_data", lambda: {"today": "2026-08-24"})
    return broadcaster


class TestEventStream:
    def test_published_messages_follow_the_opening_heartbeat(self, private_broadcaster):
        async def scenario():
            private_broadcaster.bind_loop(asyncio.get_running_loop())
            stream = event_stream(FakeRequest())
            opening = await anext(stream)
            pending = asyncio.ensure_future(anext(stream))
            await asyncio.sleep(0)
            private_broadcaster.publish("music.updated")
            following = await asyncio.wait_for(pending, 1)
            await stream.aclose()
            return opening["event"], following["event"]

        assert asyncio.run(scenario()) == ("heartbeat", "music.updated")

    def test_it_ends_when_the_panel_has_gone(self, private_broadcaster):
        async def scenario():
            private_broadcaster.bind_loop(asyncio.get_running_loop())
            stream = event_stream(FakeRequest(disconnected_after=0))
            await anext(stream)
            pending = asyncio.ensure_future(anext(stream))
            await asyncio.sleep(0)
            private_broadcaster.publish("music.updated")
            with pytest.raises(StopAsyncIteration):
                await asyncio.wait_for(pending, 1)
            # The inner generator is finalized by the loop, a tick after the
            # `break` that abandoned it.
            await asyncio.sleep(0.01)
            return len(private_broadcaster._subscribers)

        assert asyncio.run(scenario()) == 0
