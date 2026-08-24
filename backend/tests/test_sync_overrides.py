"""A recurring series with one occurrence moved, through the whole pipeline.

`RECURRENCE-ID` is how a calendar says "this Tuesday only, at a different
time", and it shares its UID with the series it overrides. Every existing test
that touches one *cancels* it, which takes a different path - the cancelled
component is dropped before any row is written, so it never exercised what
happens when two live VEVENTs claim the same UID.

The panel renders the moved occurrence correctly either way, because the
expansion has already happened by the time rows are written. What the bug cost
was everything downstream of `raw_vevent`: re-expanding the window without
re-fetching, which is the only reason that column exists, and
`homedash-inspect-calendars --find`, which reported the series as
non-recurring - on exactly the events somebody runs it on.
"""

from datetime import datetime, timedelta, timezone

import pytest
from icalendar import Calendar
from sqlmodel import Session, select

from app.calendars import sync as sync_module
from app.models import CalendarSource, Event, EventInstance


def anchor() -> datetime:
    """Inside the materialization window, and anchored to the real clock so a
    literal date cannot drift out of it as time passes."""
    return (datetime.now(timezone.utc) + timedelta(days=2)).replace(
        minute=0, second=0, microsecond=0
    )


def _stamp(value: datetime) -> str:
    return value.strftime("%Y%m%dT%H%M%SZ")


def _vevent(uid: str, summary: str, start: datetime, *extra: str) -> str:
    return (
        f"BEGIN:VEVENT\r\nUID:{uid}\r\nSUMMARY:{summary}\r\n"
        f"DTSTART:{_stamp(start)}\r\nDTEND:{_stamp(start + timedelta(hours=1))}\r\n"
        + "".join(f"{line}\r\n" for line in extra)
        + "END:VEVENT\r\n"
    )


def _parse(body: str) -> list:
    calendar = Calendar.from_ical(
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\n" + body + "END:VCALENDAR\r\n"
    )
    return list(calendar.walk("VEVENT"))


class FakeAdapter:
    def __init__(self, items):
        self._items = items
        self.changed = True
        self.sync_state = "state"

    def fetch(self, force: bool = False):
        return self._items


@pytest.fixture
def source(session: Session) -> CalendarSource:
    row = CalendarSource(kind="ics", name="Family", url="https://x/a.ics", enabled=True)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@pytest.fixture
def serve(monkeypatch):
    def _serve(items):
        monkeypatch.setattr(sync_module, "build_adapter", lambda _: FakeAdapter(items))

    return _serve


def moved_series(master_first=True) -> list:
    """A weekly series of three, with the second one moved a day later."""
    first = anchor()
    second = first + timedelta(days=7)
    master = _vevent("soccer", "soccer", first, "RRULE:FREQ=WEEKLY;COUNT=3")
    override = _vevent(
        "soccer", "soccer moved", second + timedelta(days=1),
        f"RECURRENCE-ID:{_stamp(second)}",
    )
    return _parse(master + override if master_first else override + master)


def events(session) -> list[Event]:
    return list(session.exec(select(Event)).all())


def instances(session) -> list[EventInstance]:
    return list(session.exec(select(EventInstance)).all())


class TestOneRowPerUid:
    def test_a_moved_occurrence_does_not_add_a_second_event_row(
        self, session, source, serve
    ):
        """Two rows sharing a UID is the shape of the bug. It also grew the
        table by one more row on every single rebuild."""
        serve(moved_series())
        sync_module.sync_source(session, source)

        assert len(events(session)) == 1

    def test_the_stored_vevent_is_the_series_not_the_exception(
        self, session, source, serve
    ):
        """The headline. `raw_vevent` exists so the window can be re-expanded
        without re-fetching; an override carries no RRULE, so keeping it there
        means the series can never be re-expanded again.

        Asserted against the row the instances actually point at, not against
        whichever row happens to be first. When the bug was live there were two
        rows and the master was one of them - it was simply the orphaned one,
        which is exactly what a test reading `events()[0]` would fail to see.
        """
        serve(moved_series())
        sync_module.sync_source(session, source)

        referenced = {i.event_id for i in instances(session)}
        by_id = {e.id: e for e in events(session)}
        assert len(referenced) == 1
        assert "RRULE" in by_id[referenced.pop()].raw_vevent

    def test_it_holds_even_when_the_override_comes_first_in_the_feed(
        self, session, source, serve
    ):
        """Feed order is the provider's business, not ours. Picking the last
        component seen is what caused this; picking the first would only move
        the bug to the other kind of feed."""
        serve(moved_series(master_first=False))
        sync_module.sync_source(session, source)

        assert len(events(session)) == 1
        assert "RRULE" in events(session)[0].raw_vevent

    def test_every_instance_hangs_off_that_row(self, session, source, serve):
        """No orphan: the row holding the RRULE is the one the occurrences
        point at, so `--state` counts and the join in /api/agenda agree."""
        serve(moved_series())
        sync_module.sync_source(session, source)

        (event,) = events(session)
        assert {i.event_id for i in instances(session)} == {event.id}


class TestTheOccurrencesAreStillRight:
    def test_the_series_still_expands_to_three(self, session, source, serve):
        """The panel was always correct here, and must stay correct: this is
        the assertion that stops the fix from dropping the override's own
        occurrence along with its row."""
        serve(moved_series())
        sync_module.sync_source(session, source)

        assert len(instances(session)) == 3

    def test_the_moved_one_kept_its_new_title_and_day(self, session, source, serve):
        serve(moved_series())
        sync_module.sync_source(session, source)

        moved = [i for i in instances(session) if i.title == "soccer moved"]
        assert len(moved) == 1
        # The second Tuesday, shifted a day.
        assert moved[0].starts_at.date() == (anchor() + timedelta(days=8)).date()


class TestADetachedInstance:
    def test_an_override_with_no_master_still_gets_a_row(self, session, source, serve):
        """Preferring the master must not mean discarding an orphan override.
        A feed that offers only the exception is better represented by it than
        by nothing at all."""
        second = anchor() + timedelta(days=7)
        serve(_parse(_vevent("orphan", "detached", second, f"RECURRENCE-ID:{_stamp(second)}")))
        sync_module.sync_source(session, source)

        assert [e.uid for e in events(session)] == ["orphan"]


class TestUnrelatedEventsAreUnaffected:
    def test_two_different_uids_still_get_two_rows(self, session, source, serve):
        start = anchor()
        serve(_parse(_vevent("a", "dentist", start) + _vevent("b", "soccer", start)))
        sync_module.sync_source(session, source)

        assert sorted(e.uid for e in events(session)) == ["a", "b"]
