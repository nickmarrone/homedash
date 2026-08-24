"""Health, the SSE stream, and the panel's screen schedule.

The three endpoints that are about the installation rather than about what is
on it.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from app.api import deps
from app.api.deps import SessionDep
from app.devices import screen_state, touch_last_seen
from app.models import Device
from app.scheduler import heartbeat_data
from app.sse import broadcaster, format_message

router = APIRouter()


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/api/devices/{device_id}/screen")
def get_device_screen(device_id: int, session: SessionDep) -> dict:
    """Whether the panel's screen should be on, for the Pi's screen agent.

    A GET that writes `last_seen`, which is not idempotent and is meant to be:
    the poll *is* the check-in, and a separate heartbeat endpoint would double
    the request count to learn the same fact. The write is throttled so a
    30-second poll does not rewrite the row 2900 times a day.
    """
    device = session.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail=f"no device with id {device_id}")

    now = datetime.now(timezone.utc)
    touch_last_seen(session, device, now)
    return screen_state(device, now, ZoneInfo(deps.settings.home_timezone))


async def event_stream(request: Request):
    """The SSE message stream for one connected panel.

    A named generator rather than a closure so it can be driven directly in a
    test: an HTTP-level test of a stream that never ends has to be unwound
    carefully, and gets no closer to what actually matters here.
    """
    # A heartbeat before anything else. The panel greys out events that have
    # already finished and reads the day's date off this stream, and it must
    # not use its own clock for either - so a freshly connected panel would
    # otherwise be flying blind until the scheduler's next heartbeat, up to 30
    # seconds later.
    yield format_message("heartbeat", heartbeat_data())
    async for message in broadcaster.subscribe():
        if await request.is_disconnected():
            break
        yield message


@router.get("/api/events/stream")
async def stream_events(request: Request) -> EventSourceResponse:
    return EventSourceResponse(event_stream(request))
