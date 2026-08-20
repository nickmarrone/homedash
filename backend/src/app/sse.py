import asyncio
import json
from collections.abc import AsyncIterator


class SSEBroadcaster:
    """Fan-out pub-sub for server-sent events.

    `publish` is safe to call from a background (non-asyncio) thread, such
    as an APScheduler job, via `call_soon_threadsafe`.
    """

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def subscribe(self) -> AsyncIterator[dict]:
        queue: asyncio.Queue[dict] = asyncio.Queue()
        self._subscribers.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)

    def publish(self, event_type: str, data: dict | None = None) -> None:
        message = format_message(event_type, data)
        if self._loop is None:
            return
        for queue in list(self._subscribers):
            self._loop.call_soon_threadsafe(queue.put_nowait, message)


def format_message(event_type: str, data: dict | None = None) -> dict:
    """One SSE message, in the shape EventSourceResponse expects.

    Shared with the stream route, which sends a heartbeat of its own the
    moment a panel connects rather than making it wait for the scheduler's.
    """
    return {"event": event_type, "data": json.dumps(data or {})}


broadcaster = SSEBroadcaster()
