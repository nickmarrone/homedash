"""`instances_touching`, the one query behind the agenda and every grid.

Timed rows store a UTC instant and all-day rows a floating midnight, so each
edge of the range is really two predicates. A mistake in either one drops or
invents appointments only near a day boundary, which is where these look.
"""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.calendars.queries import instances_touching
from app.models import CalendarSource, Event, EventInstance

TZ = ZoneInfo("America/New_York")
DAY = date(2026, 8, 24)
# Local midnight on DAY, in UTC, and the same for the day after.
LOCAL_START = datetime(2026, 8, 24, 4, 0, tzinfo=timezone.utc)
LOCAL_END = datetime(2026, 8, 25, 4, 0, tzinfo=timezone.utc)
FLOAT_START = datetime(2026, 8, 24, tzinfo=timezone.utc)
FLOAT_END = datetime(2026, 8, 25, tzinfo=timezone.utc)
SECOND = timedelta(seconds=1)
HOUR = timedelta(hours=1)


def add(session, starts_at, ends_at, title, all_day=False, *, event_id=None):
    if event_id is None:
        source = CalendarSource(kind="ics", name="F", color="#000000", url=f"https://x/{title}")
        session.add(source)
        session.flush()
        event = Event(source_id=source.id, uid=title, raw_vevent="x")
        session.add(event)
        session.flush()
        event_id = event.id
    session.add(
        EventInstance(
            event_id=event_id, starts_at=starts_at, ends_at=ends_at, all_day=all_day, title=title
        )
    )
    session.commit()


def found(session, **kwargs) -> list[str]:
    kwargs.setdefault("first", DAY)
    kwargs.setdefault("last", DAY)
    return [row[0].title for row in instances_touching(session, TZ, **kwargs)]


class TestTimedEdges:
    def test_ending_a_second_before_the_range_is_out(self, session):
        add(session, LOCAL_START - HOUR, LOCAL_START - SECOND, "before")
        assert found(session) == []

    def test_ending_a_second_into_the_range_is_in(self, session):
        add(session, LOCAL_START - HOUR, LOCAL_START + SECOND, "overlap")
        assert found(session) == ["overlap"]

    def test_starting_at_the_exclusive_end_is_out(self, session):
        add(session, LOCAL_END, LOCAL_END + HOUR, "after")
        assert found(session) == []

    def test_starting_a_second_before_the_end_is_in(self, session):
        add(session, LOCAL_END - SECOND, LOCAL_END + HOUR, "late")
        assert found(session) == ["late"]

    def test_spanning_the_whole_range_is_in(self, session):
        add(session, LOCAL_START - HOUR, LOCAL_END + HOUR, "span")
        assert found(session) == ["span"]


class TestAllDayEdges:
    """Floating midnights. Compared against UTC instants these would be four
    hours out in New York, and an all-day event would land on the wrong day."""

    def test_ending_before_the_range_is_out(self, session):
        add(session, FLOAT_START - timedelta(days=2), FLOAT_START - SECOND, "before", all_day=True)
        assert found(session) == []

    def test_starting_at_the_exclusive_end_is_out(self, session):
        add(session, FLOAT_END, FLOAT_END + timedelta(days=1), "after", all_day=True)
        assert found(session) == []

    def test_an_all_day_event_on_the_day_is_in(self, session):
        add(session, FLOAT_START, FLOAT_END, "today", all_day=True)
        assert found(session) == ["today"]

    def test_the_floating_end_does_not_use_the_utc_one(self, session):
        """Starts inside the UTC-shifted end but beyond the floating one."""
        add(session, FLOAT_END + HOUR, FLOAT_END + HOUR, "tomorrow", all_day=True)
        assert found(session) == []


class TestOptions:
    def test_no_last_day_means_open_ended(self, session):
        add(session, LOCAL_END + timedelta(days=400), LOCAL_END + timedelta(days=401), "far")
        assert found(session, last=None) == ["far"]

    def test_padding_widens_both_edges(self, session):
        add(session, LOCAL_START - timedelta(hours=20), LOCAL_START - timedelta(hours=19), "early")
        add(session, LOCAL_END + timedelta(hours=20), LOCAL_END + timedelta(hours=21), "late")
        assert found(session) == []
        assert sorted(found(session, pad_days=1)) == ["early", "late"]

    def test_padding_is_exactly_the_days_asked_for(self, session):
        add(session, LOCAL_END + timedelta(days=1), LOCAL_END + timedelta(days=1, hours=1), "edge")
        assert found(session, pad_days=1) == []

    def test_results_are_in_start_order(self, session):
        add(session, LOCAL_START + 3 * HOUR, LOCAL_START + 4 * HOUR, "c")
        add(session, LOCAL_START + HOUR, LOCAL_START + 2 * HOUR, "a")
        add(session, LOCAL_START + 2 * HOUR, LOCAL_START + 3 * HOUR, "b")
        assert found(session) == ["a", "b", "c"]

    def test_the_limit_keeps_the_earliest(self, session):
        for hour in (3, 1, 2):
            add(session, LOCAL_START + hour * HOUR, LOCAL_START + (hour + 1) * HOUR, f"e{hour}")
        assert found(session, limit=2) == ["e1", "e2"]

    def test_a_row_whose_source_has_gone_still_renders(self, session):
        add(session, LOCAL_START + HOUR, LOCAL_START + 2 * HOUR, "orphan", event_id=999_999)
        [(instance, source)] = instances_touching(session, TZ, first=DAY, last=DAY)
        assert instance.title == "orphan"
        assert source is None
