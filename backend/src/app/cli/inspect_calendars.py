"""Report who hosts each configured calendar, and how fast it can sync.

    uv run homedash-inspect-calendars [--probe] [--state] [--find TEXT]

ICS feeds are cached by the provider for hours, so a calendar that has to be
current needs a different adapter - and which adapter depends on who hosts it.
Run this before setting up credentials: it says, per calendar, whether fast
sync means an app-specific password or a full OAuth2 flow.

--probe additionally makes one network request per calendar: it fetches the
feed to confirm it is reachable, and for unrecognised hosts checks whether
/.well-known/caldav points at a CalDAV server.

--state reports what each calendar has actually stored: event and instance
counts, when it last synced, and the resume token it is holding. It also
names any rows belonging to no calendar at all. This is the mode to reach for
when a configured calendar is showing nothing - or when it is showing an
appointment that was deleted at the source and refuses to go away.

--find TEXT is for that last case specifically: it lists every stored
instance whose title matches, and then asks each calendar involved - with one
forced fetch, the same call the hourly full resync makes - whether it is
still serving that event. That is the difference between a calendar that
still holds the appointment and a row that should have been rebuilt away.
"""

import argparse
import sys
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

import httpx
from sqlmodel import Session, select

from app.calendars.providers import ICS_LATENCY, UNKNOWN, identify
from app.config import get_settings
from app.models import CalendarSource, Event, EventInstance


def _probe_feed(url: str) -> str:
    try:
        response = httpx.get(url, timeout=20.0, follow_redirects=True)
    except Exception as exc:
        return f"unreachable - {type(exc).__name__}: {exc}"
    if response.status_code != 200:
        return f"HTTP {response.status_code}"
    return f"HTTP 200, {response.text.count('BEGIN:VEVENT')} VEVENTs"


def _probe_caldav(url: str) -> str:
    """Check the host's CalDAV well-known location.

    A redirect or a 401 both count as "there is a CalDAV server here" - 401
    is the expected answer to an unauthenticated request, and is a *positive*
    result rather than a failure.
    """
    parts = urlsplit(url)
    well_known = urlunsplit((parts.scheme, parts.netloc, "/.well-known/caldav", "", ""))
    try:
        response = httpx.request(
            "PROPFIND", well_known, timeout=15.0, follow_redirects=False, headers={"Depth": "0"}
        )
    except Exception as exc:
        return f"no CalDAV discovered ({type(exc).__name__})"
    if response.status_code in (301, 302, 307, 308):
        return f"CalDAV likely - redirects to {response.headers.get('location')!r}"
    if response.status_code in (401, 207):
        return f"CalDAV likely - HTTP {response.status_code} (auth required is a good sign)"
    return f"no CalDAV discovered - HTTP {response.status_code}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--probe",
        action="store_true",
        help="make live requests to confirm reachability and detect CalDAV",
    )
    parser.add_argument(
        "--state",
        action="store_true",
        help="report what each calendar has stored - use when one shows nothing",
    )
    parser.add_argument(
        "--find",
        metavar="TEXT",
        help="find stored instances whose title contains TEXT, and ask each "
        "calendar whether it still serves them",
    )
    args = parser.parse_args()

    settings = get_settings()
    calendars = settings.calendars
    if not calendars:
        print("No calendars configured. Set HOMEDASH_CALENDARS (see .env.example).")
        return 1

    needs_oauth: list[str] = []
    stays_slow: list[str] = []

    for entry in calendars:
        # A calendar already configured for a fast kind needs no guessing.
        if entry.kind != "ics":
            print(f"\n{entry.name}")
            print(f"  configured: kind {entry.kind!r} - already on the fast path")
            continue
        provider = identify(entry.url)
        host = urlsplit(entry.url).hostname or "(no host)"
        print(f"\n{entry.name}")
        print(f"  host      : {host}")
        print(f"  provider  : {provider.label}")

        if provider.fast_sync:
            print(f"  fast sync : yes, via the '{provider.fast_sync}' adapter")
            print(f"  needs     : {provider.credentials}")
            if provider.fast_sync == "google":
                needs_oauth.append(entry.name)
        else:
            print(f"  fast sync : not available - stays on ICS, {ICS_LATENCY}")
            stays_slow.append(entry.name)
        print(f"  note      : {provider.notes}")

        if args.probe:
            print(f"  feed      : {_probe_feed(entry.url)}")
            if provider is UNKNOWN:
                print(f"  caldav    : {_probe_caldav(entry.url)}")

    print("\n--- summary ---")
    print(f"{len(calendars)} calendar(s) configured.")
    if needs_oauth:
        print(f"OAuth2 setup required for: {', '.join(needs_oauth)}")
    if stays_slow:
        print(f"Staying on ICS (slow): {', '.join(stays_slow)}")
    if not needs_oauth and not stays_slow:
        if any(entry.kind == "ics" for entry in calendars):
            print("All calendars can use CalDAV with an app-specific password.")
        else:
            print("Every calendar is already configured for a fast kind.")

    if args.state:
        _report_state()
    if args.find:
        _report_find(args.find)
    return 0


def _report_state() -> None:
    """What each calendar has actually stored.

    Deliberately read-only and migration-free: if the schema is not there yet
    then the app has never started, which is itself the answer.
    """
    from app.db import engine

    print("\n--- stored state ---")
    # Stored instants are naive UTC (see calendars/localtime.py), so compare
    # against a naive UTC now rather than the host's clock.
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        with Session(engine) as session:
            sources = session.exec(
                select(CalendarSource).order_by(CalendarSource.display_order)
            ).all()
            if not sources:
                print("  No calendar_sources rows. Has the app started yet?")
                return
            for source in sources:
                _report_source(session, source, now)
            _report_orphans(session)
    except Exception as exc:
        print(f"  Could not read the database: {type(exc).__name__}: {exc}")


def _report_orphans(session: Session) -> None:
    """Rows belonging to no calendar at all.

    These are the only rows a sync cannot fix. Every deletion in
    `calendars/sync.py` is scoped by `source_id`, so a row whose parent is
    gone is invisible to the rebuild and to the hourly forced full resync -
    but not to the panel, whose queries join outwards on purpose so that an
    instance with a missing source still renders. That makes an orphan a
    permanent ghost, and worth naming explicitly when somebody is staring at
    an appointment they cancelled months ago.
    """
    events = session.exec(
        select(Event).where(Event.source_id.not_in(select(CalendarSource.id)))
    ).all()
    # Two ways an instance is stranded: its event is gone, or its event's
    # calendar is. Both render on the panel; neither can be synced away.
    ghosts = list(
        session.exec(
            select(EventInstance).where(EventInstance.event_id.not_in(select(Event.id)))
        ).all()
    )
    if events:
        ghosts += session.exec(
            select(EventInstance).where(EventInstance.event_id.in_([e.id for e in events]))
        ).all()
    if not events and not ghosts:
        return
    print(f"\n  !! orphaned rows: events={len(events)} instances={len(ghosts)}")
    print("     These belong to a calendar that no longer exists, so no sync can")
    print("     reach them - they are removed at startup by the reconciler's sweep.")
    for instance in sorted(ghosts, key=lambda i: i.starts_at)[:10]:
        print(f"       - {instance.starts_at} {instance.title!r}")


def _report_source(session: Session, source: CalendarSource, now: datetime) -> None:
    events = session.exec(select(Event).where(Event.source_id == source.id)).all()
    event_ids = [event.id for event in events]
    instances = (
        session.exec(select(EventInstance).where(EventInstance.event_id.in_(event_ids))).all()
        if event_ids
        else []
    )
    upcoming = [i for i in instances if i.starts_at >= now]

    print(f"\n  [{source.display_order}] {source.name!r} ({source.kind}) {source.color}")
    print(f"      events={len(events)} instances={len(instances)} upcoming={len(upcoming)}")
    print(f"      enabled       : {source.enabled}")
    print(f"      last synced   : {source.last_synced_at}")
    print(f"      last full sync: {source.last_full_sync_at}")
    print(f"      sync state    : {_summarize_token(source.sync_state)}")

    if instances and not upcoming:
        latest = max(i.starts_at for i in instances)
        print(f"      !! every instance is in the PAST (latest {latest})")
    if source.sync_state and not events:
        print("      !! holding a resume token but storing no events, so change")
        print("         detection will keep answering 'nothing changed'. Clearing")
        print("         sync_state on this row forces a full refetch.")
    if source.last_full_sync_at is None:
        print("      !! never fully expanded - it will be on the next poll")


def _report_find(text: str) -> None:
    """Every stored instance whose title matches, and whether its calendar
    still serves it.

    The question behind a stale appointment is always the same one: is this
    row something the source is still handing us, or something a rebuild
    should have cleared? The database alone cannot answer it, so this asks
    the calendar directly with a forced fetch - the same call the hourly full
    resync makes, minus the writing.
    """
    from app.db import engine

    print(f"\n--- searching for {text!r} ---")
    try:
        with Session(engine) as session:
            rows = session.exec(
                select(EventInstance, Event, CalendarSource)
                # Outer, the way the panel's own queries join: a row with no
                # event or no calendar still renders, and is exactly the kind
                # of row somebody runs this looking for.
                .join(Event, EventInstance.event_id == Event.id, isouter=True)
                .join(CalendarSource, Event.source_id == CalendarSource.id, isouter=True)
                .where(EventInstance.title.ilike(f"%{text}%"))
                .order_by(EventInstance.starts_at)
            ).all()
            if not rows:
                print("  No stored instance has that in its title.")
                return
            print(f"  {len(rows)} matching instance(s)")
            # Keyed by id rather than collected into a set: a SQLModel row is
            # not hashable.
            involved = {s.id: s for _, _, s in rows if s is not None}
            served = _served_uids(involved.values())
            for instance, event, source in rows:
                _report_match(instance, event, source, served)
    except Exception as exc:
        print(f"  Could not read the database: {type(exc).__name__}: {exc}")


def _served_uids(sources) -> dict[int, dict | str]:
    """Fetch each calendar once, forced, and index what it currently holds.

    Forced because that is the whole point: an ETag or a sync token would
    answer "nothing changed" and hand back nothing to compare against.
    """
    from app.calendars.sync import build_adapter

    served: dict[int, dict | str] = {}
    for source in sources:
        try:
            vevents = build_adapter(source).fetch(force=True)
        except Exception as exc:
            served[source.id] = f"could not ask - {type(exc).__name__}: {exc}"
            continue
        served[source.id] = {str(vevent.get("UID")): vevent for vevent in vevents}
    return served


def _report_match(instance, event, source, served: dict) -> None:
    name = source.name if source is not None else "NO CALENDAR - orphan"
    print(f"\n  [{name}] {instance.title!r}")
    print(f"      starts   : {instance.starts_at} (all_day={instance.all_day})")
    if event is None:
        print("      event row: MISSING - orphaned instance, swept at next startup")
        return
    raw = event.raw_vevent or ""
    print(f"      uid      : {event.uid}")
    print(
        f"      event row: id={event.id} recurring={'RRULE:' in raw} "
        f"cancelled={'STATUS:CANCELLED' in raw.upper()}"
    )
    if source is None:
        print("      at source: unknown - this event's calendar row is gone")
        return
    current = served.get(source.id)
    if isinstance(current, str):
        print(f"      at source: {current}")
    elif event.uid in current:
        print("      at source: still served, so it is on the panel because the")
        print("                 calendar still has it - check the calendar itself")
    else:
        print("      at source: NOT served any more, and the row survived a fetch")
        print("                 that would have rebuilt it. Report this - it is a bug.")


def _summarize_token(state: str | None) -> str:
    """Tokens are long and credential-adjacent, so show only enough to tell
    present from absent, and one mechanism from another."""
    if not state:
        return "none"
    kind, separator, value = state.partition(":")
    if not separator:
        return f"{len(state)} chars"
    return f"{kind} ({len(value)} chars)"


if __name__ == "__main__":
    sys.exit(main())
