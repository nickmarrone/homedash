"""Ad-hoc check: why is a configured calendar showing no events?

Run from backend/ with the same env as the app:
    uv run python diagnose_calendars.py
"""

from datetime import datetime, timezone

import httpx
from sqlmodel import Session, select

from app.config import get_settings
from app.db import engine
from app.models import CalendarSource, Event, EventInstance

settings = get_settings()

print("=== configured (HOMEDASH_CALENDARS) ===")
if not settings.calendars:
    print("  (empty!)")
for entry in settings.calendars:
    scheme = (entry.url or "").split(":", 1)[0].lower()
    warn = (
        "  <-- httpx cannot fetch this scheme"
        if entry.kind == "ics" and scheme not in ("http", "https")
        else ""
    )
    print(f"  {entry.name} [{entry.kind}]: {scheme}://...{warn}")

print("\n=== calendar_sources rows ===")
now = datetime.now(timezone.utc)
with Session(engine) as session:
    for source in session.exec(
        select(CalendarSource).order_by(CalendarSource.display_order)
    ).all():
        events = session.exec(select(Event).where(Event.source_id == source.id)).all()
        ids = [e.id for e in events]
        instances = (
            session.exec(select(EventInstance).where(EventInstance.event_id.in_(ids))).all()
            if ids
            else []
        )
        future = [i for i in instances if i.starts_at >= now.replace(tzinfo=None)]
        print(
            f"  [{source.display_order}] {source.name!r} {source.color} "
            f"events={len(events)} instances={len(instances)} upcoming={len(future)}"
        )
        print(f"      url          : {source.url}")
        print(f"      enabled      : {source.enabled}")
        print(f"      last_synced  : {source.last_synced_at}")
        print(f"      sync_state   : {source.sync_state!r}")
        if instances and not future:
            latest = max(i.starts_at for i in instances)
            print(f"      !! all {len(instances)} instances are in the PAST (latest {latest})")
        if source.sync_state and not events:
            print("      !! has an ETag but no events: a 304 will keep it empty forever.")
            print("         Clear it to force a full refetch (see the note below).")

print("\n=== live fetch check ===")
for entry in settings.calendars:
    try:
        r = httpx.get(entry.url, timeout=20.0, follow_redirects=True)
        body = r.text if r.status_code == 200 else ""
        print(
            f"  {entry.name}: HTTP {r.status_code} "
            f"bytes={len(r.content)} VEVENTs={body.count('BEGIN:VEVENT')} "
            f"etag={r.headers.get('ETag')!r}"
        )
    except Exception as exc:
        print(f"  {entry.name}: FETCH FAILED -> {type(exc).__name__}: {exc}")
