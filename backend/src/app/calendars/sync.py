import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import recurring_ical_events
from icalendar import Calendar
from icalendar.cal import Event as VEvent
from sqlmodel import Session, select

from app.calendars.base import CalendarSource as CalendarSourceProtocol
from app.calendars.caldav_source import CalDAVCalendarSource
from app.calendars.google_auth import GoogleCredentials
from app.calendars.google_source import GoogleCalendarSource
from app.calendars.colors import color_for_index
from app.calendars.localtime import as_utc
from app.calendars.ics import ICSCalendarSource
from app.config import CalendarConfig, get_settings, source_key
from app.models import CalendarSource, Event, EventInstance

logger = logging.getLogger(__name__)
settings = get_settings()


def _delete_source_events(session: Session, source_id: int) -> None:
    """Remove every Event and EventInstance belonging to one source."""
    events = session.exec(select(Event).where(Event.source_id == source_id)).all()
    if not events:
        return
    event_ids = [event.id for event in events]
    instances = session.exec(
        select(EventInstance).where(EventInstance.event_id.in_(event_ids))
    ).all()
    for instance in instances:
        session.delete(instance)
    for event in events:
        session.delete(event)
    session.flush()


GOOGLE_EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/{}/events"


def _row_url(calendar: CalendarConfig) -> str:
    """The URL to store on a source row.

    Google identifies calendars by address, not URL, but the column is NOT
    NULL and a row that says where it came from is far easier to debug, so
    Google sources store their API endpoint.
    """
    if calendar.kind == "google":
        return GOOGLE_EVENTS_URL.format(quote(calendar.calendar_id or "", safe=""))
    return calendar.url or ""


def seed_calendars_from_settings(session: Session) -> None:
    """Reconcile the calendar_sources table against HOMEDASH_CALENDARS.

    The env var is the source of truth: rows are matched by identity key (URL,
    or calendar address for Google), colors and display order are assigned from
    the configured order, and any source no longer listed is deleted along with
    its events - otherwise a removed calendar's appointments would linger on
    the panel forever.
    """
    # De-duplicate by key, keeping the first occurrence, so a copy-pasted entry
    # can't produce two rows fighting over the same feed.
    configured: dict[str, CalendarConfig] = {}
    for calendar in settings.calendars:
        if calendar.key in configured:
            logger.warning(
                "Duplicate calendar in HOMEDASH_CALENDARS; ignoring later entry %r", calendar.name
            )
            continue
        configured[calendar.key] = calendar

    existing = session.exec(select(CalendarSource)).all()
    by_key = {source_key(s.kind, s.url, s.calendar_id): s for s in existing}

    for index, (key, calendar) in enumerate(configured.items()):
        source = by_key.get(key)
        if source is None:
            source = CalendarSource(kind=calendar.kind, url=_row_url(calendar))
        source.kind = calendar.kind
        source.url = _row_url(calendar)
        source.calendar_id = calendar.calendar_id
        source.credentials_ref = calendar.credentials
        source.name = calendar.name
        source.color = color_for_index(index)
        source.display_order = index
        source.enabled = True
        session.add(source)

    for source in existing:
        if source_key(source.kind, source.url, source.calendar_id) in configured:
            continue
        if source.id is not None:
            _delete_source_events(session, source.id)
        session.delete(source)

    session.commit()


def _vevents_to_calendar(vevents: list[VEvent]) -> Calendar:
    calendar = Calendar()
    calendar.add("prodid", "-//HomeDash//ICS Sync//EN")
    calendar.add("version", "2.0")
    for vevent in vevents:
        calendar.add_component(vevent)
    return calendar


def _occurrence_bounds(occurrence: VEvent) -> tuple[datetime, datetime, bool]:
    start = occurrence["DTSTART"].dt
    end = occurrence["DTEND"].dt if occurrence.get("DTEND") else start

    if isinstance(start, datetime):
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if isinstance(end, datetime) and end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        return start.astimezone(timezone.utc), end.astimezone(timezone.utc), False

    # date-only value: an all-day event
    starts_at = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    ends_at = datetime(end.year, end.month, end.day, tzinfo=timezone.utc)
    return starts_at, ends_at, True


def sync_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    """The rolling range of time the panel materializes instances for."""
    now = now or datetime.now(timezone.utc)
    return (
        now - timedelta(days=settings.sync_window_past_days),
        now + timedelta(days=settings.sync_window_future_days),
    )


def _credentials(source: CalendarSource) -> dict:
    """The credential blob a source's row refers to."""
    if not source.credentials_ref:
        raise ValueError(
            f"calendar {source.name!r} is kind {source.kind!r} but has no credentials "
            "configured; give it a \"credentials\" key naming an entry in "
            "HOMEDASH_CALENDAR_CREDENTIALS"
        )
    blob = settings.calendar_credentials.get(source.credentials_ref)
    if blob is None:
        raise ValueError(
            f"calendar {source.name!r} references credentials "
            f"{source.credentials_ref!r}, which is not defined in "
            "HOMEDASH_CALENDAR_CREDENTIALS"
        )
    return blob


def build_adapter(source: CalendarSource) -> CalendarSourceProtocol:
    """The adapter that serves one source's kind, resumed from its sync state."""
    if source.kind == "ics":
        return ICSCalendarSource(url=source.url, etag=source.sync_state)
    if source.kind == "caldav":
        blob = _credentials(source)
        missing = [key for key in ("username", "password") if not blob.get(key)]
        if missing:
            raise ValueError(
                f"credentials {source.credentials_ref!r} for calendar {source.name!r} "
                f"is missing {', '.join(missing)}"
            )
        window_start, window_end = sync_window()
        return CalDAVCalendarSource(
            url=source.url,
            username=blob["username"],
            password=blob["password"],
            window_start=window_start,
            window_end=window_end,
            sync_state=source.sync_state,
        )
    if source.kind == "google":
        blob = _credentials(source)
        window_start, window_end = sync_window()
        return GoogleCalendarSource(
            calendar_id=source.calendar_id or "",
            credentials=GoogleCredentials.from_blob(blob, source.name),
            window_start=window_start,
            window_end=window_end,
            sync_state=source.sync_state,
        )
    raise ValueError(
        f"calendar source {source.id} has unsupported kind {source.kind!r}"
    )


def needs_full_resync(source: CalendarSource, now: datetime) -> bool:
    """Whether this source must be re-expanded regardless of change detection.

    Two jobs, and the second is why this runs hourly rather than daily.

    The materialization window moves - it rolls forward a day at a time. A
    calendar that genuinely never changes would otherwise never be
    re-expanded, and the far end of its window would slowly empty out.

    It is also the backstop for change detection being wrong. Every kind
    decides "did anything change?" from a provider signal, and a signal that
    misses a change leaves the panel showing something that is not true, with
    nothing to notice it. Deletions are the case that actually bites - a
    provider reports a deleted event by omitting it, which is far easier to
    miss than a difference - and a stale row is an appointment somebody has
    already cancelled. Daily was too generous a bound on that.
    """
    last = source.last_full_sync_at
    if last is None:
        return True
    interval = timedelta(minutes=settings.full_resync_interval_minutes)
    return now - as_utc(last) >= interval


def sync_source(session: Session, source: CalendarSource) -> bool:
    """Fetch, expand, and materialize one calendar source's event instances
    for the rolling sync window. Returns True if the source had changed and
    instances were rewritten.

    Change detection is the adapter's job; rebuilding is always wholesale.
    Upserting individual events would be the usual next step, but a full
    rebuild of a few hundred events is nothing at this scale and it keeps one
    well-understood materialization path for every kind - which is where sync
    bugs would otherwise live.
    """
    adapter = build_adapter(source)
    now = datetime.now(timezone.utc)
    force = needs_full_resync(source, now)
    vevents = adapter.fetch(force=force)

    if not adapter.changed and not force:
        source.last_synced_at = now
        session.add(source)
        session.commit()
        return False

    window_start, window_end = sync_window(now)
    calendar = _vevents_to_calendar(vevents)
    occurrences = recurring_ical_events.of(calendar).between(window_start, window_end)

    _delete_source_events(session, source.id)

    events_by_uid: dict[str, Event] = {}
    for vevent in vevents:
        uid = str(vevent.get("UID"))
        event = Event(
            source_id=source.id,
            uid=uid,
            raw_vevent=vevent.to_ical().decode("utf-8"),
        )
        session.add(event)
        session.flush()
        events_by_uid[uid] = event

    for occurrence in occurrences:
        uid = str(occurrence.get("UID"))
        event = events_by_uid.get(uid)
        if event is None or event.id is None:
            continue
        starts_at, ends_at, all_day = _occurrence_bounds(occurrence)
        location = occurrence.get("LOCATION")
        session.add(
            EventInstance(
                event_id=event.id,
                member_id=source.member_id,
                starts_at=starts_at,
                ends_at=ends_at,
                all_day=all_day,
                title=str(occurrence.get("SUMMARY", "")),
                location=str(location) if location else None,
            )
        )

    source.sync_state = adapter.sync_state
    source.last_synced_at = now
    source.last_full_sync_at = now
    session.add(source)
    session.commit()
    return True
