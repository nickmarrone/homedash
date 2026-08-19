"""The materialization window has to keep rolling.

Change detection answers "did the calendar move?" - but the window moves too,
a day at a time. A calendar nobody ever edits still needs re-expanding, or its
far end quietly empties: recurring occurrences stop being materialized and
events beyond the old horizon never arrive.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.calendars.ics import ICSCalendarSource
from app.calendars.sync import needs_full_resync
from app.models import CalendarSource

NOW = datetime(2026, 8, 19, 10, 0, tzinfo=timezone.utc)


def source(**kwargs) -> CalendarSource:
    return CalendarSource(id=1, kind="ics", name="Family", url="https://x/a.ics", **kwargs)


class TestNeedsFullResync:
    def test_a_source_never_fully_synced_needs_one(self):
        assert needs_full_resync(source(), NOW) is True

    def test_a_source_synced_today_does_not(self):
        assert needs_full_resync(source(last_full_sync_at=NOW - timedelta(hours=9)), NOW) is False

    def test_a_source_synced_yesterday_does(self):
        assert needs_full_resync(source(last_full_sync_at=NOW - timedelta(days=1)), NOW) is True

    def test_naive_stored_values_are_read_as_utc(self):
        """SQLite hands back naive datetimes; reading one in the host zone
        would make this fire early or late depending on the machine."""
        stored = datetime(2026, 8, 19, 1, 0)  # naive UTC, same day as NOW
        assert needs_full_resync(source(last_full_sync_at=stored), NOW) is False


class FakeResponse:
    def __init__(self, status_code, content=b"", headers=None):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"unexpected status {self.status_code}")


FEED = (
    b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//t//EN\r\n"
    b"BEGIN:VEVENT\r\nUID:a\r\nSUMMARY:Dentist\r\n"
    b"DTSTART:20260815T160000Z\r\nDTEND:20260815T170000Z\r\n"
    b"END:VEVENT\r\nEND:VCALENDAR\r\n"
)


class TestForcedIcsFetch:
    def test_a_forced_fetch_sends_no_conditional_header(self, monkeypatch):
        """A 304 would hand back an empty list to a caller that is about to
        rebuild from it - emptying the calendar."""
        seen = {}

        def fake_get(url, headers=None, **kwargs):
            seen["headers"] = headers
            return FakeResponse(200, FEED, {"ETag": "new"})

        monkeypatch.setattr("app.calendars.ics.httpx.get", fake_get)
        adapter = ICSCalendarSource(url="https://x/a.ics", etag="old")
        vevents = adapter.fetch(force=True)

        assert "If-None-Match" not in seen["headers"]
        assert adapter.changed is True
        assert len(vevents) == 1

    def test_an_ordinary_fetch_still_uses_the_etag(self, monkeypatch):
        seen = {}

        def fake_get(url, headers=None, **kwargs):
            seen["headers"] = headers
            return FakeResponse(304)

        monkeypatch.setattr("app.calendars.ics.httpx.get", fake_get)
        adapter = ICSCalendarSource(url="https://x/a.ics", etag="old")
        adapter.fetch()

        assert seen["headers"]["If-None-Match"] == "old"
        assert adapter.changed is False
