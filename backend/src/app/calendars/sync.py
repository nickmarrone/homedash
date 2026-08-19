import logging
from datetime import datetime, timedelta, timezone

import recurring_ical_events
from icalendar import Calendar
from icalendar.cal import Event as VEvent
from sqlmodel import Session, select

from app.calendars.colors import color_for_index
from app.calendars.ics import ICSCalendarSource
from app.config import get_settings
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


def seed_ics_calendars_from_settings(session: Session) -> None:
    """Reconcile the calendar_sources table against HOMEDASH_ICS_CALENDARS.

    The env var is the source of truth: rows are matched by URL, colors and
    display order are assigned from the configured order, and any ICS source
    no longer listed is deleted along with its events (otherwise a removed
    calendar's appointments would linger on the panel forever).
    """
    # De-duplicate by URL, keeping the first occurrence, so a copy-pasted entry
    # can't produce two rows fighting over the same feed.
    configured: dict[str, str] = {}
    for calendar in settings.ics_calendars:
        if calendar.url in configured:
            logger.warning(
                "Duplicate URL in HOMEDASH_ICS_CALENDARS; ignoring later entry %r", calendar.name
            )
            continue
        configured[calendar.url] = calendar.name

    existing = session.exec(select(CalendarSource).where(CalendarSource.kind == "ics")).all()
    by_url = {source.url: source for source in existing}

    for index, (url, name) in enumerate(configured.items()):
        source = by_url.get(url)
        if source is None:
            source = CalendarSource(kind="ics", url=url)
        source.name = name
        source.color = color_for_index(index)
        source.display_order = index
        source.enabled = True
        session.add(source)

    for source in existing:
        if source.url in configured:
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


def sync_ics_source(session: Session, source: CalendarSource) -> bool:
    """Fetch, expand, and materialize one ICS calendar source's event
    instances for the rolling sync window. Returns True if the feed had
    changed and instances were rewritten."""
    adapter = ICSCalendarSource(url=source.url, etag=source.resource_etag)
    vevents = adapter.fetch()
    now = datetime.now(timezone.utc)

    if not adapter.changed:
        source.last_synced_at = now
        session.add(source)
        session.commit()
        return False

    window_start = now - timedelta(days=settings.sync_window_past_days)
    window_end = now + timedelta(days=settings.sync_window_future_days)
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

    source.resource_etag = adapter.etag
    source.last_synced_at = now
    session.add(source)
    session.commit()
    return True
