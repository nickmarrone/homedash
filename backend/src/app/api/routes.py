from datetime import datetime, timezone
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from sqlmodel import Session, select
from sse_starlette.sse import EventSourceResponse

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
    today_start_local = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    today_start_utc = today_start_local.astimezone(timezone.utc)

    rows = session.exec(
        select(EventInstance, Member, CalendarSource)
        .join(Member, EventInstance.member_id == Member.id, isouter=True)
        # Outer: an instance whose event or source has gone missing should
        # still render, uncolored, rather than silently vanish from the panel.
        .join(Event, EventInstance.event_id == Event.id, isouter=True)
        .join(CalendarSource, Event.source_id == CalendarSource.id, isouter=True)
        .where(EventInstance.starts_at >= today_start_utc)
        .order_by(EventInstance.starts_at)
        .limit(200)
    ).all()

    return [
        {
            "id": instance.id,
            "title": instance.title,
            "location": instance.location,
            "all_day": instance.all_day,
            "starts_at": instance.starts_at.astimezone(tz).isoformat(),
            "ends_at": instance.ends_at.astimezone(tz).isoformat(),
            "member": (
                {"id": member.id, "name": member.name, "color": member.color}
                if member
                else None
            ),
            "calendar": (
                {"id": source.id, "name": source.name, "color": source.color}
                if source
                else None
            ),
        }
        for instance, member, source in rows
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
