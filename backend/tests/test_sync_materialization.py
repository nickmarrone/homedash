"""What `sync_source` actually writes to the database.

The reconciler tests cover configuration changing; these cover the *calendar*
changing underneath a source that stays configured. That is where a deletion
lives, and it had no coverage at all - the pipeline was correct and the bug
was one layer up, in an adapter deciding nothing had changed.

Adapters are faked rather than mocked at the HTTP layer, because the thing
being pinned here is the contract between `CalendarSource.changed` and the
rows that survive a sync. `TestGoogleEndToEnd` breaks that rule on purpose:
a faked adapter cannot prove the seam between `needs_full_resync` deciding
to force a fetch and Google actually re-listing, and that seam is where a
deleted appointment either dies or lives forever.
"""

from datetime import datetime, timedelta, timezone

import pytest
from icalendar import Calendar
from sqlmodel import Session, select

from app.calendars import sync as sync_module
from app.config import Settings
from app.models import CalendarSource, Event, EventInstance


def anchor() -> datetime:
    """A start time comfortably inside the materialization window.

    Anchored to the clock rather than written as a literal date. The
    materialization window is measured from `now` and rolls forward with it,
    so a fixed DTSTART silently falls out of the past end of it once enough
    real time has passed - and every assertion about which titles survived a
    sync then reads empty, blaming the reconciler for the calendar.
    """
    return (datetime.now(timezone.utc) + timedelta(days=1)).replace(
        minute=0, second=0, microsecond=0
    )


def _stamp(value: datetime) -> str:
    return value.strftime("%Y%m%dT%H%M%SZ")


def _parse(body: str) -> list:
    calendar = Calendar.from_ical(
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\n" + body + "END:VCALENDAR\r\n"
    )
    return list(calendar.walk("VEVENT"))


def _block(uid: str, start: datetime, *extra: str) -> str:
    return (
        f"BEGIN:VEVENT\r\nUID:{uid}\r\nSUMMARY:{uid}\r\n"
        f"DTSTART:{_stamp(start)}\r\nDTEND:{_stamp(start + timedelta(hours=1))}\r\n"
        + "".join(f"{line}\r\n" for line in extra)
        + "END:VEVENT\r\n"
    )


def vevents(*uids: str, status: str | None = None) -> list:
    """VEVENT masters for one timed event per uid, all inside the window.

    `status` sets STATUS on every one of them - CANCELLED is how a calendar
    keeps serving an event it has called off.
    """
    extra = (f"STATUS:{status}",) if status else ()
    return _parse("".join(_block(uid, anchor(), *extra) for uid in uids))


def series(uid: str, count: int = 3, *, status: str | None = None, cancel: int | None = None):
    """A weekly series, optionally with one occurrence cancelled.

    `cancel` is the zero-based index of an occurrence to call off with a
    RECURRENCE-ID override - which is how a CalDAV server reports "not this
    Tuesday" when the client writes an override instead of an EXDATE.
    """
    start = anchor()
    extra = [f"RRULE:FREQ=WEEKLY;COUNT={count}"]
    if status:
        extra.append(f"STATUS:{status}")
    body = _block(uid, start, *extra)
    if cancel is not None:
        moment = start + timedelta(weeks=cancel)
        body += _block(
            uid, moment, f"RECURRENCE-ID:{_stamp(moment)}", "STATUS:CANCELLED"
        )
    return _parse(body)


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


def starts(session: Session) -> list[datetime]:
    return sorted(i.starts_at for i in session.exec(select(EventInstance)).all())


class TestCancelled:
    """A calendar that says an event is off, without removing it.

    This is the deletion a wholesale rebuild cannot see for itself. Every
    other kind of deletion is an absence: the event stops arriving, and
    rebuilding from what did arrive drops it. `STATUS:CANCELLED` is the
    opposite - the source keeps serving the event, so every rebuild
    faithfully re-materializes it and the forced full resync that exists to
    catch missed deletions re-creates the row instead of clearing it. On the
    wall that is an appointment somebody already cancelled, showing forever.
    """

    def test_a_cancelled_event_never_reaches_the_panel(self, session, source, serve):
        serve(vevents("dentist") + vevents("soccer", status="CANCELLED"))
        sync_module.sync_source(session, source)

        assert titles(session) == ["dentist"]

    def test_an_event_cancelled_after_it_was_synced_stops_showing(
        self, session, source, serve
    ):
        """The headline: it was on the wall, it got cancelled, it must go."""
        serve(vevents("dentist", "soccer"))
        sync_module.sync_source(session, source)
        assert titles(session) == ["dentist", "soccer"]

        serve(vevents("dentist") + vevents("soccer", status="CANCELLED"))
        sync_module.sync_source(session, source)

        assert titles(session) == ["dentist"]

    def test_the_forced_resync_clears_one_the_change_signal_missed(
        self, session, source, serve, monkeypatch
    ):
        """Tied to HOMEDASH_FULL_RESYNC_INTERVAL_MINUTES on purpose.

        A cancellation the provider's sync signal never reported has only one
        thing left to catch it, and a full resync that re-materializes the
        tombstone is not a backstop at all.
        """
        serve(vevents("dentist", "soccer"))
        sync_module.sync_source(session, source)

        monkeypatch.setattr(
            sync_module, "settings", Settings(_env_file=None, full_resync_interval_minutes=60)
        )
        source.last_full_sync_at = datetime.now(timezone.utc) - timedelta(hours=2)
        serve(vevents("dentist") + vevents("soccer", status="CANCELLED"), changed=False)
        sync_module.sync_source(session, source)

        assert titles(session) == ["dentist"]

    def test_no_event_row_survives_for_a_cancelled_event(self, session, source, serve):
        """The parent row goes too. Leaving it behind would have the events
        table claiming a calendar holds something it does not."""
        serve(vevents("dentist") + vevents("soccer", status="CANCELLED"))
        sync_module.sync_source(session, source)

        assert [e.uid for e in session.exec(select(Event)).all()] == ["dentist"]

    def test_a_cancelled_series_goes_entirely(self, session, source, serve):
        serve(series("soccer", 3, status="CANCELLED"))
        sync_module.sync_source(session, source)

        assert titles(session) == []

    def test_one_cancelled_occurrence_leaves_the_rest_of_the_series(
        self, session, source, serve
    ):
        """A RECURRENCE-ID override cancels a single Tuesday. The other
        Tuesdays are still soccer practice."""
        serve(series("soccer", 3, cancel=1))
        sync_module.sync_source(session, source)

        first = anchor()
        assert [s.replace(tzinfo=timezone.utc) for s in starts(session)] == [
            first,
            first + timedelta(weeks=2),
        ]

    def test_a_tentative_event_still_shows(self, session, source, serve):
        """Only CANCELLED is filtered: an unconfirmed appointment is still an
        appointment, and dropping it would hide half an invitation list."""
        serve(vevents("maybe-lunch", status="TENTATIVE"))
        sync_module.sync_source(session, source)

        assert titles(session) == ["maybe-lunch"]

    def test_a_confirmed_event_is_untouched(self, session, source, serve):
        serve(vevents("dentist", status="CONFIRMED"))
        sync_module.sync_source(session, source)

        assert titles(session) == ["dentist"]


class TestOrphanSweep:
    """Rows whose parent is gone, which no rebuild can reach.

    Every deletion in sync.py is scoped by source_id, so a row that cannot be
    reached from a live source row is invisible to both the ordinary rebuild
    and the forced full resync. It is not invisible to the *panel*, though -
    the agenda and calendar queries join outwards on purpose so that an
    instance whose source went missing still renders. That combination is a
    ghost appointment that outlives every sync.
    """

    def test_an_instance_whose_event_is_gone_is_swept(self, session, source, serve):
        serve(vevents("dentist"))
        sync_module.sync_source(session, source)
        event = session.exec(select(Event)).one()
        session.delete(event)
        session.commit()

        assert sync_module.sweep_orphaned_events(session) == 1
        session.commit()

        assert titles(session) == []

    def test_an_event_whose_calendar_is_gone_goes_with_its_instances(
        self, session, source, serve
    ):
        serve(vevents("dentist"))
        sync_module.sync_source(session, source)
        # Delete the source row alone, the way a version that did not cascade
        # would have left it.
        session.delete(session.get(CalendarSource, source.id))
        session.commit()

        assert sync_module.sweep_orphaned_events(session) == 2
        session.commit()

        assert session.exec(select(Event)).all() == []
        assert titles(session) == []

    def test_live_rows_are_left_alone(self, session, source, serve):
        serve(vevents("dentist", "soccer"))
        sync_module.sync_source(session, source)

        assert sync_module.sweep_orphaned_events(session) == 0
        session.commit()

        assert titles(session) == ["dentist", "soccer"]


class GoogleResponse:
    def __init__(self, payload):
        self.status_code = 200
        self._payload = payload
        self.text = ""

    def json(self):
        return self._payload


def google_item(uid: str, status: str | None = None) -> dict:
    start = anchor()
    item = {
        "id": uid,
        "iCalUID": uid,
        "summary": uid,
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": (start + timedelta(hours=1)).isoformat()},
    }
    if status:
        item["status"] = status
    return item


class TestGoogleEndToEnd:
    """The whole chain for the kind that actually reports deletions badly.

    `needs_full_resync` -> `fetch(force=True)` -> a real `events.list` with
    `showDeleted` -> cancelled tombstones dropped -> the source's rows
    rebuilt. Every link has its own test; this is the one that fails if two
    of them stop meeting.
    """

    @pytest.fixture
    def google(self, session, monkeypatch):
        monkeypatch.setattr(
            sync_module,
            "settings",
            Settings(
                _env_file=None,
                full_resync_interval_minutes=60,
                calendar_credentials={
                    "g": {"client_id": "c", "client_secret": "s", "refresh_token": "r"}
                },
            ),
        )
        monkeypatch.setattr(
            "app.calendars.google_auth.refresh_access_token",
            lambda *a, **k: {"access_token": "at", "expires_in": 3600},
        )
        row = CalendarSource(
            kind="google",
            name="Family",
            url="https://x/e",
            calendar_id="fam@g",
            credentials_ref="g",
            enabled=True,
        )
        session.add(row)
        session.commit()
        session.refresh(row)

        served: dict = {"items": [], "probe": []}

        def fake_get(url, params=None, headers=None, **kwargs):
            if "syncToken" in (params or {}):
                return GoogleResponse({"items": served["probe"]})
            return GoogleResponse({"items": served["items"], "nextSyncToken": "tok"})

        monkeypatch.setattr("app.calendars.google_source.httpx.get", fake_get)
        return row, served

    def test_a_forced_resync_drops_an_event_deleted_on_a_phone(self, session, google):
        source, served = google
        served["items"] = [google_item("dentist"), google_item("soccer")]
        sync_module.sync_source(session, source)
        assert titles(session) == ["dentist", "soccer"]

        # Deleted on a phone: Google keeps returning it as a cancelled
        # tombstone. The probe is told to report nothing, so only the forced
        # resync is left to notice.
        served["items"] = [google_item("dentist"), google_item("soccer", status="cancelled")]
        source.last_full_sync_at = datetime.now(timezone.utc)
        session.add(source)
        session.commit()
        assert sync_module.sync_source(session, source) is False
        assert titles(session) == ["dentist", "soccer"]

        source.last_full_sync_at = datetime.now(timezone.utc) - timedelta(hours=2)
        session.add(source)
        session.commit()

        assert sync_module.sync_source(session, source) is True
        assert titles(session) == ["dentist"]
        assert [e.uid for e in session.exec(select(Event)).all()] == ["dentist"]
