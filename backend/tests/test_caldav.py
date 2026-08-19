"""CalDAV adapter behaviour, against a fake collection.

The property that matters is that an unchanged calendar is cheap: this runs
every minute, so a poll that re-fetches and rebuilds when nothing moved would
undo the reason CalDAV is here at all.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.calendars.caldav_source import DIGEST_PREFIX, TOKEN_PREFIX, CalDAVCalendarSource

WINDOW_START = datetime(2026, 7, 20, tzinfo=timezone.utc)
WINDOW_END = datetime(2027, 8, 19, tzinfo=timezone.utc)


def ics(uid: str, summary: str = "Dentist") -> str:
    return (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\n"
        f"BEGIN:VEVENT\r\nUID:{uid}\r\nSUMMARY:{summary}\r\n"
        "DTSTART:20260815T160000Z\r\nDTEND:20260815T170000Z\r\n"
        "END:VEVENT\r\nEND:VCALENDAR\r\n"
    )


class FakeObject:
    def __init__(self, data: str) -> None:
        self.data = data


class FakeChanges(list):
    def __init__(self, items, sync_token):
        super().__init__(items)
        self.sync_token = sync_token


class FakeCalendar:
    """Stands in for a caldav.Calendar.

    `supports_sync_token=False` models a server without RFC 6578, which is
    what the digest fallback exists for.
    """

    def __init__(self, objects, sync_token="tok-1", supports_sync_token=True):
        self.objects = list(objects)
        self.token = sync_token
        self.supports_sync_token = supports_sync_token
        self.changed_since_token: list = []
        self.search_calls = 0
        self.search_kwargs = None

    def objects_by_sync_token(self, sync_token=None, load_objects=False):
        if not self.supports_sync_token:
            raise RuntimeError("sync-collection not supported")
        if sync_token is None:
            return FakeChanges(self.objects, self.token)
        return FakeChanges(self.changed_since_token, self.token)

    def search(self, **kwargs):
        self.search_calls += 1
        self.search_kwargs = kwargs
        return self.objects


def adapter(calendar, sync_state=None) -> CalDAVCalendarSource:
    return CalDAVCalendarSource(
        url="https://caldav.example/dav/x",
        username="nick",
        password="pw",
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        sync_state=sync_state,
        calendar_factory=lambda *a, **k: calendar,
    )


class TestFirstSync:
    def test_fetches_everything_and_records_a_token(self):
        calendar = FakeCalendar([FakeObject(ics("a")), FakeObject(ics("b"))])
        source = adapter(calendar)

        vevents = source.fetch()

        assert source.changed is True
        assert {str(v["UID"]) for v in vevents} == {"a", "b"}
        assert source.sync_state == f"{TOKEN_PREFIX}tok-1"

    def test_requests_masters_not_expanded_occurrences(self):
        """Expanding server-side would bypass the one recurrence expansion
        every other kind flows through."""
        calendar = FakeCalendar([FakeObject(ics("a"))])
        adapter(calendar).fetch()

        assert calendar.search_kwargs["expand"] is False
        assert calendar.search_kwargs["start"] == WINDOW_START
        assert calendar.search_kwargs["end"] == WINDOW_END


class TestUnchangedCalendar:
    def test_no_changes_means_no_refetch(self):
        calendar = FakeCalendar([FakeObject(ics("a"))])
        source = adapter(calendar, sync_state=f"{TOKEN_PREFIX}tok-1")

        source.fetch()

        assert source.changed is False
        # The expensive call never happened - that is the whole point.
        assert calendar.search_calls == 0

    def test_a_reported_change_triggers_a_full_rebuild(self):
        calendar = FakeCalendar([FakeObject(ics("a"))], sync_token="tok-2")
        calendar.changed_since_token = [FakeObject(ics("a"))]
        source = adapter(calendar, sync_state=f"{TOKEN_PREFIX}tok-1")

        source.fetch()

        assert source.changed is True
        assert calendar.search_calls == 1
        assert source.sync_state == f"{TOKEN_PREFIX}tok-2"


class TestDigestFallback:
    def test_server_without_sync_collection_uses_a_digest(self):
        calendar = FakeCalendar([FakeObject(ics("a"))], supports_sync_token=False)
        source = adapter(calendar)

        source.fetch()

        assert source.changed is True
        assert source.sync_state.startswith(DIGEST_PREFIX)

    def test_identical_content_is_not_a_change(self):
        calendar = FakeCalendar([FakeObject(ics("a"))], supports_sync_token=False)
        first = adapter(calendar)
        first.fetch()

        second = adapter(calendar, sync_state=first.sync_state)
        second.fetch()

        assert second.changed is False

    def test_edited_content_is_a_change(self):
        calendar = FakeCalendar([FakeObject(ics("a"))], supports_sync_token=False)
        first = adapter(calendar)
        first.fetch()

        calendar.objects = [FakeObject(ics("a", summary="Moved"))]
        second = adapter(calendar, sync_state=first.sync_state)
        second.fetch()

        assert second.changed is True

    def test_reordering_is_not_a_change(self):
        """Servers give no ordering guarantee; an order flap must not
        trigger a rebuild every single minute."""
        objects = [FakeObject(ics("a")), FakeObject(ics("b"))]
        calendar = FakeCalendar(objects, supports_sync_token=False)
        first = adapter(calendar)
        first.fetch()

        calendar.objects = list(reversed(objects))
        second = adapter(calendar, sync_state=first.sync_state)
        second.fetch()

        assert second.changed is False


class TestResilience:
    def test_a_rejected_token_falls_back_to_a_full_fetch(self):
        """An expired token is routine, not a failure - the panel must not
        go stale waiting for a human to clear it."""

        class RejectingCalendar(FakeCalendar):
            def objects_by_sync_token(self, sync_token=None, load_objects=False):
                if sync_token is not None:
                    raise RuntimeError("HTTP 403: invalid sync token")
                return FakeChanges(self.objects, self.token)

        calendar = RejectingCalendar([FakeObject(ics("a"))], sync_token="tok-fresh")
        source = adapter(calendar, sync_state=f"{TOKEN_PREFIX}stale")

        source.fetch()

        assert source.changed is True
        assert source.sync_state == f"{TOKEN_PREFIX}tok-fresh"

    def test_undecodable_object_is_skipped(self):
        calendar = FakeCalendar([FakeObject(ics("a")), FakeObject("")])
        source = adapter(calendar)

        vevents = source.fetch()

        assert {str(v["UID"]) for v in vevents} == {"a"}
