from datetime import date, datetime, timedelta, timezone
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import and_, or_
from sqlmodel import Session, select
from sse_starlette.sse import EventSourceResponse

from app.api.serializers import serialize_instance
from app.calendars.grid import (
    VIEWS,
    GridItem,
    build_days,
    local_dates_spanned,
    normalize_anchor,
    period_bounds,
    period_title,
    step_anchor,
)
from app.config import get_settings
from app.db import get_session
from app.models import CalendarSource, Event, EventInstance
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
        select(EventInstance, CalendarSource)
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
        serialize_instance(instance, source, tz) for instance, source in rows
    ]


@router.get("/api/calendar")
def get_calendar(
    session: SessionDep,
    view: str = Query("month"),
    anchor: str | None = Query(None),
) -> dict:
    """One period of the calendar, as day buckets.

    Day, week, and month differ only in how many buckets come back, so the
    frontend renders one array with different CSS. `prev_anchor` and
    `next_anchor` are returned so navigation needs no date maths in the
    browser - see app/calendars/grid.py for why that line is drawn here.
    """
    if view not in VIEWS:
        raise HTTPException(
            status_code=400, detail=f"view must be one of {', '.join(VIEWS)}"
        )

    tz = ZoneInfo(settings.home_timezone)
    today = datetime.now(tz).date()
    try:
        requested = date.fromisoformat(anchor) if anchor else today
    except ValueError:
        raise HTTPException(status_code=400, detail="anchor must be YYYY-MM-DD") from None

    week_starts_on = settings.week_starts_on
    anchor_date = normalize_anchor(view, requested, week_starts_on)
    first, last = period_bounds(view, anchor_date, week_starts_on)

    rows = _instances_overlapping(session, first, last, tz)
    items = [
        GridItem(
            payload=serialize_instance(instance, source, tz),
            dates=local_dates_spanned(
                instance.starts_at, instance.ends_at, instance.all_day, tz
            ),
            all_day=instance.all_day,
            starts_at=instance.starts_at,
        )
        for instance, source in rows
    ]

    return {
        "view": view,
        "anchor": anchor_date.isoformat(),
        "title": period_title(view, anchor_date, week_starts_on),
        "today": today.isoformat(),
        "prev_anchor": step_anchor(view, anchor_date, -1).isoformat(),
        "next_anchor": step_anchor(view, anchor_date, 1).isoformat(),
        "days": build_days(items, first, last, anchor_date, view, today),
    }


def _instances_overlapping(session: Session, first: date, last: date, tz: ZoneInfo):
    """Every instance touching the local date range [first, last].

    The predicate is an overlap, not a start-time cutoff: an event already in
    progress when the period opens still belongs in every day it spans, and a
    `starts_at >=` filter would drop it from the view entirely.

    All-day rows are compared against floating date anchors because that is
    how they are stored - see calendars/localtime.py.
    """
    # Widened by a day on each side so an event whose local date is in range
    # but whose UTC instant sits outside it is still caught; build_days does
    # the exact per-date bucketing afterwards.
    range_start_utc = datetime(first.year, first.month, first.day, tzinfo=tz).astimezone(
        timezone.utc
    ) - timedelta(days=1)
    range_end_utc = datetime(last.year, last.month, last.day, tzinfo=tz).astimezone(
        timezone.utc
    ) + timedelta(days=2)
    range_start_floating = datetime(first.year, first.month, first.day) - timedelta(days=1)
    range_end_floating = datetime(last.year, last.month, last.day) + timedelta(days=2)

    timed = and_(
        EventInstance.all_day == False,  # noqa: E712
        EventInstance.starts_at < range_end_utc,
        EventInstance.ends_at >= range_start_utc,
    )
    floating = and_(
        EventInstance.all_day == True,  # noqa: E712
        EventInstance.starts_at < range_end_floating,
        EventInstance.ends_at >= range_start_floating,
    )
    return session.exec(
        select(EventInstance, CalendarSource)
        .join(Event, EventInstance.event_id == Event.id, isouter=True)
        .join(CalendarSource, Event.source_id == CalendarSource.id, isouter=True)
        .where(or_(timed, floating))
        .order_by(EventInstance.starts_at)
    ).all()


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
