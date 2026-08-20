"""What `sync_source` actually writes to the database.

The reconciler tests cover configuration changing; these cover the *calendar*
changing underneath a source that stays configured. That is where a deletion
lives, and it had no coverage at all - the pipeline was correct and the bug
was one layer up, in an adapter deciding nothing had changed.

Adapters are faked rather than mocked at the HTTP layer, because the thing
being pinned here is the contract between `CalendarSource.changed` and the
rows that survive a sync.
"""

from datetime import datetime, timedelta, timezone

import pytest
from icalendar import Calendar
from sqlmodel import Session, select

from app.calendars import sync as sync_module
from app.config import Settings
from app.models import CalendarSource, Event, EventInstance


def vevents(*uids: str) -> list:
    """VEVENT masters for one timed event per uid, all inside the window.

    Anchored to the clock rather than written as a literal date. The
    materialization window is measured from `now` and rolls forward with it,
    so a fixed DTSTART silently falls out of the past end of it once enough
    real time has passed - and every assertion about which titles survived a
    sync then reads empty, blaming the reconciler for the calendar.
    """
    start = datetime.now(timezone.utc) + timedelta(days=1)
    starts_at = start.strftime("%Y%m%dT%H0000Z")
    ends_at = (start + timedelta(hours=1)).strftime("%Y%m%dT%H0000Z")
    body = "".join(
        f"BEGIN:VEVENT\r\nUID:{uid}\r\nSUMMARY:{uid}\r\n"
        f"DTSTART:{starts_at}\r\nDTEND:{ends_at}\r\nEND:VEVENT\r\n"
        for uid in uids
    )
    calendar = Calendar.from_ical(
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\n" + body + "END:VCALENDAR\r\n"
    )
    return list(calendar.walk("VEVENT"))


class FakeAdapter:
    """A calendar source that returns what it is told to."""

    def __init__(self, items, changed: bool) -> None:
        self._items = items
        self.changed = changed
        self.sync_state = "state"

    def fetch(self, force: bool = False):
        self.fetched_with_force = force
        return self._items


@pytest.fixture
def source(session: Session) -> CalendarSource:
    row = CalendarSource(
        kind="google", name="Family", url="https://x/e", calendar_id="fam@g", enabled=True
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@pytest.fixture
def serve(monkeypatch):
    """Make the next sync see a given calendar, reporting a given change."""

    def _serve(items, changed: bool = True) -> FakeAdapter:
        adapter = FakeAdapter(items, changed)
        monkeypatch.setattr(sync_module, "build_adapter", lambda _: adapter)
        return adapter

    return _serve


def titles(session: Session) -> list[str]:
    return sorted(i.title for i in session.exec(select(EventInstance)).all())


class TestDeletion:
    def test_an_event_removed_at_the_source_is_removed_from_the_panel(
        self, session, source, serve
    ):
        """The headline bug: an appointment cancelled on a phone must not
        keep showing on the wall."""
        serve(vevents("dentist", "soccer"))
        sync_module.sync_source(session, source)
        assert titles(session) == ["dentist", "soccer"]

        serve(vevents("dentist"))
        sync_module.sync_source(session, source)

        assert titles(session) == ["dentist"]

    def test_the_orphaned_event_row_goes_too(self, session, source, serve):
        """Leaving the parent row behind would keep the deleted event's
        VEVENT available to re-expand into instances on the next sync."""
        serve(vevents("dentist", "soccer"))
        sync_module.sync_source(session, source)

        serve(vevents("dentist"))
        sync_module.sync_source(session, source)

        assert sorted(e.uid for e in session.exec(select(Event)).all()) == ["dentist"]

    def test_emptying_a_calendar_empties_the_panel(self, session, source, serve):
        serve(vevents("only"))
        sync_module.sync_source(session, source)

        serve([])
        sync_module.sync_source(session, source)

        assert titles(session) == []

    def test_a_source_reporting_no_change_is_left_alone(self, session, source, serve):
        """The cheap path has to stay cheap: an unchanged calendar must not
        pay for a rebuild, and must not lose its rows either."""
        serve(vevents("dentist"))
        sync_module.sync_source(session, source)

        adapter = serve(vevents("dentist"), changed=False)
        # Now, not a literal: needs_full_resync measures this against the real
        # clock, so a written-in date reads as overdue the moment the resync
        # interval has elapsed since someone typed it - and the test then
        # exercises the rebuild it exists to prove was skipped.
        source.last_full_sync_at = datetime.now(timezone.utc)
        rewritten = sync_module.sync_source(session, source)

        assert rewritten is False
        assert adapter.fetched_with_force is False
        assert titles(session) == ["dentist"]

    def test_a_forced_resync_rebuilds_even_when_nothing_is_reported_changed(
        self, session, source, serve, monkeypatch
    ):
        """The backstop, end to end.

        This is what saves the panel when an adapter's change detection is
        wrong about a deletion - as Google's was, because its sync-token
        probe did not ask for deleted events. The event vanishes on the next
        forced full resync without anyone reporting a change.
        """
        serve(vevents("dentist", "cancelled-hours-ago"))
        sync_module.sync_source(session, source)

        monkeypatch.setattr(
            sync_module, "settings", Settings(_env_file=None, full_resync_interval_minutes=60)
        )
        source.last_full_sync_at = datetime.now(timezone.utc) - timedelta(hours=2)
        adapter = serve(vevents("dentist"), changed=False)
        rewritten = sync_module.sync_source(session, source)

        assert adapter.fetched_with_force is True
        assert rewritten is True
        assert titles(session) == ["dentist"]
