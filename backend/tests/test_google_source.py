"""Google Calendar adapter.

The conversion tests run their output through the real recurrence expander,
because the thing that matters is not the shape of the VEVENT but whether the
occurrences that reach the panel are right.
"""

from datetime import datetime, timezone

import pytest
import recurring_ical_events
from icalendar import Calendar

from app.calendars.google_auth import GoogleCredentials
from app.calendars.google_source import GoogleCalendarSource, GoogleApiError, items_to_vevents

WINDOW_START = datetime(2026, 8, 1, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 9, 30, tzinfo=timezone.utc)


def occurrences(vevents, start=WINDOW_START, end=WINDOW_END):
    """Expand exactly the way sync.py does, and report local start times."""
    calendar = Calendar()
    calendar.add("prodid", "-//test//EN")
    calendar.add("version", "2.0")
    for vevent in vevents:
        calendar.add_component(vevent)
    found = recurring_ical_events.of(calendar).between(start, end)
    return sorted(str(o["DTSTART"].dt) for o in found)


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def credentials() -> GoogleCredentials:
    creds = GoogleCredentials("cid", "secret", "rt")
    creds._access_token = "at"
    creds._expires_at = 1e12
    return creds


def adapter(responses, sync_state=None):
    """An adapter serving a scripted list of responses."""
    calls = []

    def _get(url, params, headers):
        calls.append(params)
        return responses[len(calls) - 1]

    source = GoogleCalendarSource(
        calendar_id="fam@group.calendar.google.com",
        credentials=credentials(),
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        sync_state=sync_state,
        get=_get,
    )
    source.calls = calls
    return source


TIMED = {
    "id": "ev1",
    "iCalUID": "ev1@google.com",
    "status": "confirmed",
    "summary": "Dentist",
    "location": "Main St",
    "start": {"dateTime": "2026-08-15T09:00:00-04:00", "timeZone": "America/New_York"},
    "end": {"dateTime": "2026-08-15T10:00:00-04:00", "timeZone": "America/New_York"},
}

ALL_DAY = {
    "id": "ev2",
    "iCalUID": "ev2@google.com",
    "status": "confirmed",
    "summary": "Camp",
    "start": {"date": "2026-08-17"},
    "end": {"date": "2026-08-20"},
}

WEEKLY = {
    "id": "ev3",
    "iCalUID": "ev3@google.com",
    "status": "confirmed",
    "summary": "Soccer",
    "start": {"dateTime": "2026-08-04T17:00:00-04:00", "timeZone": "America/New_York"},
    "end": {"dateTime": "2026-08-04T18:00:00-04:00", "timeZone": "America/New_York"},
    "recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=TU;COUNT=4"],
}


class TestConversion:
    def test_timed_event_keeps_its_details(self):
        [event] = items_to_vevents([TIMED])
        assert str(event["UID"]) == "ev1@google.com"
        assert str(event["SUMMARY"]) == "Dentist"
        assert str(event["LOCATION"]) == "Main St"

    def test_all_day_event_stays_a_date(self):
        """A date, not a midnight datetime - sync._occurrence_bounds keys its
        all-day handling off exactly that distinction."""
        [event] = items_to_vevents([ALL_DAY])
        assert not isinstance(event["DTSTART"].dt, datetime)
        assert event["DTSTART"].dt.isoformat() == "2026-08-17"

    def test_recurring_event_expands(self):
        assert len(occurrences(items_to_vevents([WEEKLY]))) == 4

    def test_events_without_a_start_are_skipped(self):
        assert items_to_vevents([{"id": "x", "status": "confirmed", "summary": "Broken"}]) == []

    def test_cancelled_master_is_dropped(self):
        assert items_to_vevents([{**TIMED, "status": "cancelled"}]) == []

    def test_falls_back_to_id_when_there_is_no_ical_uid(self):
        item = {k: v for k, v in TIMED.items() if k != "iCalUID"}
        [event] = items_to_vevents([item])
        assert str(event["UID"]) == "ev1"

    def test_unparseable_recurrence_leaves_a_single_event(self):
        """One bad rule must not take the whole calendar down."""
        events = items_to_vevents([{**WEEKLY, "recurrence": ["RRULE:FREQ=NONSENSE;;;"]}])
        assert len(events) == 1


class TestRecurrenceExceptions:
    def test_cancelled_occurrence_disappears(self):
        """A cancelled soccer practice must not keep showing on the wall."""
        cancelled = {
            "id": "ev3_20260811T210000Z",
            "status": "cancelled",
            "recurringEventId": "ev3",
            "originalStartTime": {
                "dateTime": "2026-08-11T17:00:00-04:00",
                "timeZone": "America/New_York",
            },
        }
        starts = occurrences(items_to_vevents([WEEKLY, cancelled]))
        assert len(starts) == 3
        assert not any("2026-08-11" in s for s in starts)

    def test_moved_occurrence_moves(self):
        moved = {
            "id": "ev3_20260818T210000Z",
            "iCalUID": "ev3@google.com",
            "status": "confirmed",
            "summary": "Soccer (rescheduled)",
            "recurringEventId": "ev3",
            "originalStartTime": {
                "dateTime": "2026-08-18T17:00:00-04:00",
                "timeZone": "America/New_York",
            },
            "start": {"dateTime": "2026-08-19T17:00:00-04:00", "timeZone": "America/New_York"},
            "end": {"dateTime": "2026-08-19T18:00:00-04:00", "timeZone": "America/New_York"},
        }
        starts = occurrences(items_to_vevents([WEEKLY, moved]))
        assert any("2026-08-19" in s for s in starts)
        assert not any("2026-08-18" in s for s in starts)

    def test_orphaned_override_is_ignored(self):
        """Its master is outside the window, so its occurrences are not being
        shown either and there is nothing to correct."""
        orphan = {
            "id": "zz",
            "status": "cancelled",
            "recurringEventId": "not-in-this-batch",
            "originalStartTime": {"dateTime": "2026-08-11T17:00:00-04:00"},
        }
        assert items_to_vevents([orphan]) == []


class TestDstHandling:
    def test_a_weekly_meeting_does_not_drift_across_a_dst_change(self):
        """Normalising DTSTART to UTC would shift every occurrence after the
        clocks change - the meeting is 9am local, not 13:00Z."""
        weekly = {
            "id": "dst",
            "iCalUID": "dst@google.com",
            "status": "confirmed",
            "summary": "Standup",
            "start": {"dateTime": "2026-10-27T09:00:00-04:00", "timeZone": "America/New_York"},
            "end": {"dateTime": "2026-10-27T09:30:00-04:00", "timeZone": "America/New_York"},
            "recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=TU;COUNT=4"],
        }
        starts = occurrences(
            items_to_vevents([weekly]),
            start=datetime(2026, 10, 1, tzinfo=timezone.utc),
            end=datetime(2026, 12, 1, tzinfo=timezone.utc),
        )
        # US DST ends 2026-11-01; every occurrence stays at 09:00 local.
        assert len(starts) == 4
        assert all("09:00:00" in s for s in starts)


class TestSyncToken:
    def test_unchanged_calendar_makes_one_small_request(self):
        source = adapter([FakeResponse(payload={"items": []})], sync_state="tok-1")
        source.fetch()

        assert source.changed is False
        assert len(source.calls) == 1
        assert source.calls[0]["syncToken"] == "tok-1"

    def test_reported_change_triggers_a_full_relist(self):
        source = adapter(
            [
                FakeResponse(payload={"items": [{"id": "ev1"}]}),
                FakeResponse(payload={"items": [TIMED], "nextSyncToken": "tok-2"}),
            ],
            sync_state="tok-1",
        )
        source.fetch()

        assert source.changed is True
        assert source.sync_state == "tok-2"
        assert "timeMin" in source.calls[1]

    def test_expired_token_starts_over_cleanly(self):
        """410 is documented and routine - Google expires tokens on its own
        schedule, and the panel must recover without a human."""
        source = adapter(
            [
                FakeResponse(status_code=410),
                FakeResponse(payload={"items": [TIMED], "nextSyncToken": "fresh"}),
            ],
            sync_state="stale",
        )
        source.fetch()

        assert source.changed is True
        assert source.sync_state == "fresh"

    def test_full_list_asks_for_masters_and_deletions(self):
        source = adapter([FakeResponse(payload={"items": [], "nextSyncToken": "t"})])
        source.fetch()

        params = source.calls[0]
        assert params["singleEvents"] == "false"
        assert params["showDeleted"] == "true"
        assert "syncToken" not in params

    def test_a_forced_fetch_skips_the_token_check(self):
        source = adapter(
            [FakeResponse(payload={"items": [TIMED], "nextSyncToken": "t"})], sync_state="tok-1"
        )
        source.fetch(force=True)

        assert source.changed is True
        assert "timeMin" in source.calls[0]

    def test_pagination_is_followed_and_only_the_last_page_has_the_token(self):
        source = adapter(
            [
                FakeResponse(payload={"items": [TIMED], "nextPageToken": "p2"}),
                FakeResponse(payload={"items": [ALL_DAY], "nextSyncToken": "final"}),
            ]
        )
        vevents = source.fetch()

        assert len(vevents) == 2
        assert source.sync_state == "final"


class TestAuthRecovery:
    def test_a_401_is_retried_once_with_a_fresh_token(self):
        posts = []

        def post(url, data):
            posts.append(data)
            return FakeResponse(payload={"access_token": f"at{len(posts)}", "expires_in": 3600})

        creds = GoogleCredentials("cid", "secret", "rt", post=post)
        responses = [FakeResponse(status_code=401), FakeResponse(payload={"items": [], "nextSyncToken": "t"})]
        seen_headers = []

        def _get(url, params, headers):
            seen_headers.append(headers["Authorization"])
            return responses[len(seen_headers) - 1]

        source = GoogleCalendarSource(
            calendar_id="c",
            credentials=creds,
            window_start=WINDOW_START,
            window_end=WINDOW_END,
            get=_get,
        )
        source.fetch()

        assert seen_headers == ["Bearer at1", "Bearer at2"]

    def test_a_hard_failure_is_raised_not_swallowed(self):
        """A silently empty calendar is worse than a logged failure - the
        scheduler's rollback guard depends on this raising."""
        source = adapter([FakeResponse(status_code=403, text="quota exceeded")])
        with pytest.raises(GoogleApiError, match="403"):
            source.fetch()
