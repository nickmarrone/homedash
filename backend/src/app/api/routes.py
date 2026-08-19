from datetime import datetime, timezone
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from sqlalchemy import and_, or_
from sqlmodel import Session, select
from sse_starlette.sse import EventSourceResponse

from app.api.serializers import serialize_instance
from app.config import get_settings
from app.db import get_session
from app.models import CalendarSource, Event, EventInstance, Member
from app.sse import broadcaster
from app.weather.client import get_cached_weather

router = APIRouter()
settings = get_settings()

SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/api/agenda")
def get_agenda(session: SessionDep) -> list[dict]:
    tz = ZoneInfo(settings.home_timezone)
    today_local = datetime.now(tz).date()
    today_start_utc = datetime(
        today_local.year, today_local.month, today_local.day, tzinfo=tz
    ).astimezone(timezone.utc)
    # All-day rows are stored at UTC midnight of their calendar date as a
    # placeholder, not as a real instant, so they have to be compared against
    # that same anchor. Measuring them from local midnight instead drops
    # today's all-day events entirely in any zone behind UTC, where local
    # midnight is *later* than the placeholder they carry.
    today_start_floating = datetime(today_local.year, today_local.month, today_local.day)

    rows = session.exec(
        select(EventInstance, Member, CalendarSource)
        .join(Member, EventInstance.member_id == Member.id, isouter=True)
        # Outer: an instance whose event or source has gone missing should
        # still render, uncolored, rather than silently vanish from the panel.
        .join(Event, EventInstance.event_id == Event.id, isouter=True)
        .join(CalendarSource, Event.source_id == CalendarSource.id, isouter=True)
        .where(
            or_(
                and_(EventInstance.all_day == False, EventInstance.starts_at >= today_start_utc),  # noqa: E712
                and_(EventInstance.all_day == True, EventInstance.starts_at >= today_start_floating),  # noqa: E712
            )
        )
        .order_by(EventInstance.starts_at)
        .limit(200)
    ).all()

    return [
        serialize_instance(instance, member, source, tz) for instance, member, source in rows
    ]


@router.get("/api/calendars")
def get_calendars(session: SessionDep) -> list[dict]:
    """The calendars the agenda can show, for the legend. Served separately
    from /api/agenda so a calendar with nothing currently scheduled still
    appears, and so swatches don't reshuffle as events come and go."""
    sources = session.exec(
        select(CalendarSource)
        .where(CalendarSource.enabled == True)  # noqa: E712
        .order_by(CalendarSource.display_order, CalendarSource.id)
    ).all()
    return [{"id": s.id, "name": s.name, "color": s.color} for s in sources]


@router.get("/api/weather")
def get_weather() -> dict:
    return get_cached_weather() or {}


@router.get("/api/events/stream")
async def stream_events(request: Request) -> EventSourceResponse:
    async def event_generator():
        async for message in broadcaster.subscribe():
            if await request.is_disconnected():
                break
            yield message

    return EventSourceResponse(event_generator())
