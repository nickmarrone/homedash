from datetime import date, datetime, timedelta, timezone
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import and_, or_
from sqlmodel import Session, select
from sse_starlette.sse import EventSourceResponse

from app.api.serializers import serialize_instance
from app.astro import astro_summary
from app.comets import load_comet_elements, visible_comets
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
from app.devices import screen_state, touch_last_seen
from app.models import CalendarSource, Device, Event, EventInstance, Photo
from app.photos.derivatives import (
    PANEL_ORIENTATIONS,
    derivative_path,
    slot_for,
    target_size,
)
from app.scheduler import heartbeat_data
from app.sse import broadcaster, format_message
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

    tz = ZoneInfo(settings.home_timezone)
    now = datetime.now(tz)
    today = now.date()
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
    return screen_state(device, now, ZoneInfo(settings.home_timezone))


@router.get("/api/photos")
def get_photos(session: SessionDep, orientation: str = Query("landscape")) -> dict:
    """The screensaver's playlist, for one way the panel is mounted.

    The server says what exists and how big it is; the panel owns shuffling,
    pairing and dwell timing. That split keeps the server stateless per panel -
    there is no cursor to resume and no way for a page reload to disagree with
    it - and it puts the slideshow's state where every other panel-local
    preference already lives.

    `slot` is "full" for a photo that agrees with this orientation and "half"
    for one that does not; the panel shows two consecutive halves side by side.

    `v` in each URL is the content hash, which is what lets the image endpoint
    mark its response immutable: a photo replaced in place gets a new URL
    rather than a stale cache entry the panel would keep for a year.
    """
    if orientation not in PANEL_ORIENTATIONS:
        raise HTTPException(
            status_code=400,
            detail=f"orientation must be one of {', '.join(PANEL_ORIENTATIONS)}",
        )

    photos = session.exec(
        select(Photo)
        .where(Photo.error == None)  # noqa: E711
        .order_by(Photo.id)
        .limit(settings.photo_max_count)
    ).all()

    items = []
    for photo in photos:
        slot = slot_for(photo.orientation, orientation)
        width, height = target_size(orientation, slot)
        items.append(
            {
                "id": photo.id,
                "slot": slot,
                "width": width,
                "height": height,
                "url": (
                    f"/api/photos/{photo.id}/image"
                    f"?orientation={orientation}&v={photo.hash}"
                ),
            }
        )

    return {
        "dwell_seconds": settings.screensaver_dwell_seconds,
        "idle_minutes": settings.screensaver_idle_minutes,
        "photos": items,
    }


@router.get("/api/photos/{photo_id}/image")
def get_photo_image(
    photo_id: int, session: SessionDep, orientation: str = Query("landscape")
) -> FileResponse:
    """One pre-rendered derivative.

    A pure file read - the resize happened at index time. This is the same
    discipline the weather cache states for itself: handlers read what a
    background job prepared, they never do the work on the request.

    The `v` query parameter is deliberately ignored here. It exists to make the
    URL change when the bytes change; validating it would only turn a panel
    holding a slightly stale playlist into a panel showing gaps.
    """
    if orientation not in PANEL_ORIENTATIONS:
        raise HTTPException(
            status_code=400,
            detail=f"orientation must be one of {', '.join(PANEL_ORIENTATIONS)}",
        )

    photo = session.get(Photo, photo_id)
    if photo is None or photo.error is not None or not photo.hash:
        raise HTTPException(status_code=404, detail=f"no photo with id {photo_id}")

    slot = slot_for(photo.orientation, orientation)
    path = derivative_path(
        settings.photo_cache_dir, photo.hash, target_size(orientation, slot)
    )
    if not path.exists():
        # Indexed but not yet rendered, or the cache was wiped and the next
        # scan has not caught up. 404 and let the panel skip to the next slide;
        # rendering it here would put a multi-second Pillow call on a request
        # the panel makes every few seconds.
        raise HTTPException(status_code=404, detail="derivative not rendered yet")

    return FileResponse(
        path,
        media_type="image/jpeg",
        # Safe to keep forever because the URL carries the content hash. This
        # matters more than usual on a wall panel: the slideshow loops for
        # months, and without it every loop re-fetches the whole library.
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.get("/api/weather")
def get_weather() -> dict:
    """The weather cache, plus the sky.

    The astronomy is computed here rather than folded into the cache on
    refresh, precisely so it does not share the weather's fate: it needs no
    network, and Open-Meteo being unreachable should not also take the moon
    off the panel. It is a few dozen floating-point operations - cheaper than
    serializing the forecast it travels with.
    """
    now = datetime.now(timezone.utc)
    tz = ZoneInfo(settings.home_timezone)
    comets = (
        visible_comets(
            load_comet_elements(),
            now,
            settings.weather_latitude,
            settings.weather_longitude,
            tz,
            settings.comet_magnitude_limit,
        )
        if settings.comets_enabled
        else []
    )
    return {
        **(get_cached_weather() or {}),
        "astro": astro_summary(
            now,
            settings.weather_latitude,
            settings.weather_longitude,
            tz,
            extra_events=comets,
        ),
    }


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
