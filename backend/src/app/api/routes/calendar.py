"""The calendar the panel is for: the agenda, the grid, and the legend."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from sqlmodel import select

from app.api import deps
from app.api.deps import SessionDep
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
from app.calendars.queries import instances_touching
from app.models import CalendarSource

router = APIRouter()


@router.get("/api/agenda")
def get_agenda(session: SessionDep) -> list[dict]:
    """What is coming up, as a flat list from today onwards.

    Open-ended and capped by count rather than by date, because "the next 200
    things" is what a wall calendar is actually asked for.

    Each item carries `agenda_date`: the day the panel should file it under.
    For almost everything that is simply the day it starts, but an event
    already in progress starts in the past, and a forward-looking list has no
    heading to put that under. Clamping it to today is what keeps a holiday
    visible on its third morning instead of grouped beneath a date that is no
    longer on screen. The panel does not compute this for the same reason it
    computes no other date: it must not consult its own clock.
    """
    tz = ZoneInfo(deps.settings.home_timezone)
    today = datetime.now(tz).date()

    rows = instances_touching(session, tz, first=today, limit=200)

    return [
        serialize_instance(
            instance,
            source,
            tz,
            agenda_date=max(
                local_dates_spanned(
                    instance.starts_at, instance.ends_at, instance.all_day, tz
                )[0],
                today,
            ).isoformat(),
        )
        for instance, source in rows
    ]


@router.get("/api/calendar")
def get_calendar(
    session: SessionDep,
    view: str = Query("month"),
    anchor: str | None = Query(None),
) -> dict:
    """One period of the calendar, as day buckets.

    Every view differs only in how many buckets come back and where they
    start, so the frontend renders one array with different CSS. `next3` and
    `next5` are rolling lookaheads: unlike `week` they are not snapped to a
    week boundary, so with no anchor they begin on today. `prev_anchor` and
    `next_anchor` are returned so navigation needs no date maths in the
    browser - see app/calendars/grid.py for why that line is drawn here.
    """
    if view not in VIEWS:
        raise HTTPException(
            status_code=400, detail=f"view must be one of {', '.join(VIEWS)}"
        )

    tz = ZoneInfo(deps.settings.home_timezone)
    now = datetime.now(tz)
    today = now.date()
    try:
        requested = date.fromisoformat(anchor) if anchor else today
    except ValueError:
        raise HTTPException(status_code=400, detail="anchor must be YYYY-MM-DD") from None

    week_starts_on = deps.settings.week_starts_on
    anchor_date = normalize_anchor(view, requested, week_starts_on)
    first, last = period_bounds(view, anchor_date, week_starts_on)

    # pad_days=1: build_days re-buckets by exact local date afterwards, so this
    # query only has to return a superset - an instance whose local date is in
    # range can carry a UTC instant that is not.
    rows = instances_touching(session, tz, first=first, last=last, pad_days=1)
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
        # The server's clock, alongside the events it is being used to judge.
        # The panel greys out what has already finished, and it cannot ask its
        # own clock for that - the same rule the rest of this file follows.
        # Riding on this response rather than waiting for the next SSE
        # heartbeat is what stops a freshly loaded panel from showing a
        # morning of finished appointments at full strength for half a minute.
        "now": now.isoformat(),
        "prev_anchor": step_anchor(view, anchor_date, -1).isoformat(),
        "next_anchor": step_anchor(view, anchor_date, 1).isoformat(),
        "days": build_days(items, first, last, anchor_date, view, today),
    }


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
