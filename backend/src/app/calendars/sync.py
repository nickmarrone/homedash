from datetime import datetime, timedelta, timezone

import recurring_ical_events
from icalendar import Calendar
from icalendar.cal import Event as VEvent
from sqlmodel import Session, select

from app.calendars.ics import ICSCalendarSource
from app.config import get_settings
from app.models import CalendarSource, Event, EventInstance

settings = get_settings()


def seed_ics_source_from_settings(session: Session) -> None:
    """Ensure HOMEDASH_ICS_URL (if set) is registered as a calendar_sources
    row, since nothing else creates one in Phase 1 (no source-management UI
    yet). The .env value is treated as the source of truth: on a URL change,
    update the existing row rather than creating a duplicate."""
    if not settings.ics_url:
        return

    source = session.exec(select(CalendarSource).where(CalendarSource.kind == "ics")).first()
    if source is None:
        session.add(CalendarSource(kind="ics", url=settings.ics_url, enabled=True))
        session.commit()
    elif source.url != settings.ics_url:
        source.url = settings.ics_url
        source.resource_etag = None
        source.enabled = True
        session.add(source)
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

    existing_events = session.exec(select(Event).where(Event.source_id == source.id)).all()
    if existing_events:
        event_ids = [event.id for event in existing_events]
        existing_instances = session.exec(
            select(EventInstance).where(EventInstance.event_id.in_(event_ids))
        ).all()
        for instance in existing_instances:
            session.delete(instance)
        for event in existing_events:
            session.delete(event)
        session.flush()

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
