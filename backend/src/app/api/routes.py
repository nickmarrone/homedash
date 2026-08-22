from datetime import date, datetime, timedelta, timezone
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
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
from app.music.heos import TRANSPORT_ACTIONS, HeosController
from app.music.jellyfin import JellyfinError, JellyfinLibrary
from app.music.service import (
    get_controller,
    get_library,
    get_queues,
    get_tokens,
    library_configured,
    music_configured,
)
from app.music.tokens import UrlTooLong
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


def _controller_or_503() -> HeosController:
    """The music controller, or a clear reason there isn't one.

    Three different states answer 503, and the panel shows none of them - it
    just hides the music UI - so the detail string is written for whoever is
    reading the logs or curling the endpoint.
    """
    if not music_configured():
        raise HTTPException(
            status_code=503,
            detail="music is not configured; set HOMEDASH_MUSIC_ENABLED and HOMEDASH_HEOS_HOST",
        )
    controller = get_controller()
    if controller is None or not controller.connected:
        raise HTTPException(
            status_code=503, detail="not connected to HEOS yet; still retrying"
        )
    return controller


@router.get("/api/music/players")
def get_music_players() -> dict:
    """Every speaker, with what it is doing right now.

    Always 200 when music is configured, even before the connection is up, so
    the panel can tell "no speakers yet" from "this panel has no music at all"
    without treating an error as the answer. `connected` is what it switches on.
    """
    if not music_configured():
        raise HTTPException(
            status_code=503,
            detail="music is not configured; set HOMEDASH_MUSIC_ENABLED and HOMEDASH_HEOS_HOST",
        )
    controller = get_controller()
    connected = controller is not None and controller.connected
    queues = get_queues()
    players = controller.players() if controller is not None else []
    for player in players:
        # What HomeDash is holding for this speaker, which the speaker itself
        # cannot report: as far as it knows it was handed one stream.
        player["queue"] = queues.snapshot(player["id"]) if queues is not None else None
    return {
        "connected": connected,
        "library": library_configured(),
        "players": players,
    }


@router.post("/api/music/players/{player_id}/transport")
async def post_music_transport(player_id: int, body: dict = Body(default={})) -> dict:
    """Play, pause, stop, next or previous on one speaker.

    The first write path in this app. There is no auth on it: the panel has
    none, and the household has settled for a LAN-only appliance. Worth knowing
    rather than discovering - see the "kid lock" note in CLAUDE.md.

    Skips are handled by HomeDash's own queue when there is one. Content sent
    to a speaker as a URL never enters the speaker's queue, so HEOS's
    `play_next` has nothing to move to and does nothing at all - a skip button
    that silently did nothing is exactly the kind of fault a wall panel hides
    well. A speaker playing from its own sources still falls through to it.
    """
    action = body.get("action")
    if action not in TRANSPORT_ACTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"action must be one of {', '.join(TRANSPORT_ACTIONS)}",
        )
    controller = _controller_or_503()
    queues = get_queues()

    try:
        if queues is not None and action in ("next", "previous"):
            handled = await (
                queues.next(player_id) if action == "next" else queues.previous(player_id)
            )
            if handled:
                return {"ok": True}
        if queues is not None and action == "stop":
            # Before the command, not after: the speaker reports a finished
            # track and a deliberate stop identically, so the queue has to be
            # gone before the resulting `stop` event arrives or it would
            # helpfully start the next track on somebody who asked for silence.
            queues.clear(player_id)
        await controller.transport(player_id, action)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"no player with id {player_id}") from None
    return {"ok": True}


@router.post("/api/music/players/{player_id}/volume")
async def post_music_volume(player_id: int, body: dict = Body(default={})) -> dict:
    """Set one speaker's volume, 0-100.

    Clamping rather than rejecting an out-of-range number would hide a caller
    bug behind a speaker that quietly went to full volume, which is a bad way
    to find out about it in a kitchen.
    """
    level = body.get("level")
    if not isinstance(level, int) or isinstance(level, bool) or not 0 <= level <= 100:
        raise HTTPException(status_code=400, detail="level must be an integer from 0 to 100")
    controller = _controller_or_503()
    try:
        await controller.set_volume(player_id, level)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"no player with id {player_id}") from None
    return {"ok": True}


def _library_or_503() -> JellyfinLibrary:
    if not library_configured():
        raise HTTPException(
            status_code=503,
            detail="no music library; set HOMEDASH_JELLYFIN_URL and HOMEDASH_JELLYFIN_API_KEY",
        )
    library = get_library()
    if library is None:
        raise HTTPException(status_code=503, detail="music library not started")
    return library


@router.get("/api/music/library")
def get_music_library(
    kind: str = Query("artists"),
    parent: str | None = Query(None),
) -> dict:
    """One level of the library: artists, then albums, then tracks.

    A level at a time rather than a tree. The panel shows one screen at a time
    and a whole music library is far too much to hand it in one response, so
    each call answers exactly what the screen in front of somebody needs.
    """
    if kind not in ("artists", "albums", "tracks"):
        raise HTTPException(status_code=400, detail="kind must be artists, albums or tracks")
    if kind == "tracks" and not parent:
        raise HTTPException(status_code=400, detail="tracks requires a parent album id")

    library = _library_or_503()
    try:
        if kind == "artists":
            items = [{"id": a.id, "name": a.name} for a in library.artists()]
        elif kind == "albums":
            items = [
                {"id": a.id, "name": a.name, "artist": a.artist, "year": a.year}
                for a in library.albums(parent)
            ]
        else:
            items = [
                {
                    "id": t.id,
                    "title": t.title,
                    "artist": t.artist,
                    "album": t.album,
                    "duration_ms": t.duration_ms,
                    "track_number": t.track_number,
                }
                for t in library.tracks(parent or "")
            ]
    except JellyfinError as exc:
        # 502, not 500: the failure is upstream, and saying so is the
        # difference between "check Jellyfin" and "check HomeDash" for whoever
        # is reading the log.
        raise HTTPException(status_code=502, detail=str(exc)) from None

    return {"kind": kind, "parent": parent, "items": items}


@router.get("/api/music/art/{item_id}")
async def get_music_art(item_id: str, size: int = Query(480)) -> Response:
    """One cover image, proxied.

    Proxied rather than linked for the same reason the audio is: the Jellyfin
    API key must never reach the browser. Jellyfin already generated these at
    import time, so `size` bounds what crosses the LAN rather than asking it to
    resize anything.
    """
    if not 32 <= size <= 1920:
        raise HTTPException(status_code=400, detail="size must be between 32 and 1920")
    library = _library_or_503()

    import httpx

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            upstream = await client.get(library.art_url(item_id, size), headers=library.headers)
    except Exception:
        raise HTTPException(status_code=502, detail="could not reach Jellyfin") from None
    if upstream.status_code != 200:
        # 404 rather than passing the upstream status through: a missing cover
        # is an ordinary thing for the panel to handle, and it already falls
        # back to a placeholder.
        raise HTTPException(status_code=404, detail="no cover art")

    return Response(
        content=upstream.content,
        media_type=upstream.headers.get("Content-Type", "image/jpeg"),
        # Not immutable: unlike the photo derivatives, this URL carries no
        # content hash, so replacing a cover in Jellyfin has to be able to win
        # eventually. An hour is long enough that scrolling a library does not
        # refetch, and short enough that a fix shows up the same afternoon.
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.post("/api/music/players/{player_id}/play")
async def post_music_play(player_id: int, body: dict = Body(default={})) -> dict:
    """Start an album, or an explicit list of tracks, on one speaker.

    HomeDash holds the resulting queue and feeds the speaker one track at a
    time - see app/music/queue.py for why there is no way to hand over the
    whole album at once.
    """
    album_id = body.get("album_id")
    track_ids = body.get("track_ids")
    if not isinstance(album_id, str) and not isinstance(track_ids, list):
        raise HTTPException(status_code=400, detail="pass either album_id or track_ids")

    controller = _controller_or_503()
    library = _library_or_503()
    queues = get_queues()
    if queues is None:
        raise HTTPException(status_code=503, detail="music library not started")
    if player_id not in {p["id"] for p in controller.players()}:
        raise HTTPException(status_code=404, detail=f"no player with id {player_id}")

    try:
        if isinstance(album_id, str):
            tracks = library.tracks(album_id)
        else:
            wanted = [t for t in track_ids if isinstance(t, str)]
            # One album fetch and a filter, rather than a request per track:
            # the panel only ever sends a subset of an album it is already
            # looking at, and it sends that album's id alongside.
            parent = body.get("parent_album_id")
            if not isinstance(parent, str):
                raise HTTPException(
                    status_code=400, detail="track_ids requires parent_album_id"
                )
            by_id = {t.id: t for t in library.tracks(parent)}
            tracks = [by_id[t] for t in wanted if t in by_id]
    except JellyfinError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None

    if not tracks:
        raise HTTPException(status_code=404, detail="nothing to play")

    try:
        await queues.start(player_id, tracks)
    except UrlTooLong as exc:
        # 500, and loudly: this is configuration, not a bad request, and it is
        # the failure that otherwise presents as a speaker playing silence.
        raise HTTPException(status_code=500, detail=str(exc)) from None

    return {"ok": True, "queued": len(tracks)}


@router.get("/api/music/s/{token}")
async def get_music_stream(token: str, request: Request) -> StreamingResponse:
    """The audio itself. **This endpoint is fetched by the speaker, not the panel.**

    It exists so that no Jellyfin credential ever has to travel in a URL, and
    so that the URL stays under the 255 characters HEOS will fetch. Everything
    about its shape follows from that.

    Range requests are forwarded rather than answered here: HEOS asks for them,
    and Jellyfin already implements them properly for the underlying file.
    """
    tokens = get_tokens()
    library = get_library()
    if tokens is None or library is None:
        raise HTTPException(status_code=503, detail="music library not started")

    track_id = tokens.resolve(token)
    if track_id is None:
        raise HTTPException(status_code=404, detail="unknown or expired stream token")

    import httpx

    headers = dict(library.headers)
    # Only Range is forwarded. Passing the speaker's whole header set through
    # would hand Jellyfin an Accept-Encoding it might honour, and a
    # transparently compressed audio stream is not what was asked for.
    if "range" in request.headers:
        headers["Range"] = request.headers["range"]

    client = httpx.AsyncClient(timeout=None, follow_redirects=True)
    try:
        upstream_request = client.build_request(
            "GET", library.stream_url(track_id), headers=headers
        )
        upstream = await client.send(upstream_request, stream=True)
    except Exception:
        await client.aclose()
        raise HTTPException(status_code=502, detail="could not reach Jellyfin") from None

    if upstream.status_code >= 400:
        status = upstream.status_code
        await upstream.aclose()
        await client.aclose()
        raise HTTPException(status_code=502, detail=f"Jellyfin returned {status}")

    async def body():
        # The client outlives this function, so it is closed here rather than
        # in a context manager: returning a StreamingResponse means the bytes
        # are pulled long after the handler has returned.
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    passthrough = {
        name: upstream.headers[name]
        for name in ("content-length", "content-range", "accept-ranges")
        if name in upstream.headers
    }
    return StreamingResponse(
        body(),
        status_code=upstream.status_code,
        media_type=upstream.headers.get("Content-Type", "audio/mpeg"),
        headers=passthrough,
    )
