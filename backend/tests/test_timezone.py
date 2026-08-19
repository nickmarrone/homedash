"""Rendered times must depend on HOMEDASH_HOME_TIMEZONE and nothing else.

Two failure modes are pinned here, both of which looked fine in a container
(where TZ is unset, so the host clock happens to be UTC) and both of which
would have been baked into the grid views:

  * stored instants read back naive and converted in the *host* zone
  * all-day placeholders converted at all, landing a day early
"""

import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.api.serializers import serialize_instance
from app.models import CalendarSource, EventInstance

NEW_YORK = ZoneInfo("America/New_York")  # UTC-4 in August
TOKYO = ZoneInfo("Asia/Tokyo")  # UTC+9, no DST


@pytest.fixture
def host_timezone(monkeypatch):
    """Move the *machine's* clock zone, the way a Pi's OS setting would."""

    def _set(name: str) -> None:
        monkeypatch.setenv("TZ", name)
        time.tzset()

    yield _set
    # monkeypatch restores the env var, but tzset() has to be re-run for the
    # C library to pick the original back up.
    time.tzset()


def timed(**kwargs) -> EventInstance:
    return EventInstance(
        id=1,
        event_id=1,
        # Stored naive-UTC, exactly as SQLite hands it back.
        starts_at=datetime(2026, 8, 15, 16, 0),
        ends_at=datetime(2026, 8, 15, 17, 0),
        all_day=False,
        title="Dentist",
        **kwargs,
    )


def all_day(**kwargs) -> EventInstance:
    return EventInstance(
        id=2,
        event_id=1,
        # sync._occurrence_bounds anchors a date-only VEVENT at UTC midnight.
        starts_at=datetime(2026, 8, 15, 0, 0),
        ends_at=datetime(2026, 8, 16, 0, 0),  # DTEND is exclusive
        all_day=True,
        title="Camp",
        **kwargs,
    )


@pytest.mark.parametrize("host_tz", ["UTC", "America/Los_Angeles", "Asia/Tokyo"])
def test_timed_event_ignores_the_host_clock(host_timezone, host_tz):
    host_timezone(host_tz)
    payload = serialize_instance(timed(), None, NEW_YORK)
    # 16:00 UTC is noon in New York, whatever the panel's OS thinks.
    assert payload["starts_at"] == "2026-08-15T12:00:00-04:00"
    assert payload["ends_at"] == "2026-08-15T13:00:00-04:00"


def test_all_day_event_keeps_its_own_date_behind_utc(host_timezone):
    host_timezone("UTC")
    payload = serialize_instance(all_day(), None, NEW_YORK)
    # Converting the UTC-midnight placeholder would report the 14th.
    assert payload["starts_at"].startswith("2026-08-15")


def test_all_day_event_keeps_its_own_date_ahead_of_utc(host_timezone):
    host_timezone("UTC")
    payload = serialize_instance(all_day(), None, TOKYO)
    assert payload["starts_at"].startswith("2026-08-15")


def test_all_day_dates_survive_a_shifted_host_clock(host_timezone):
    host_timezone("America/Los_Angeles")
    payload = serialize_instance(all_day(), None, NEW_YORK)
    assert payload["starts_at"].startswith("2026-08-15")


def test_calendar_colour_is_attached(host_timezone):
    host_timezone("UTC")
    source = CalendarSource(id=7, kind="ics", name="Family", color="#2563eb", url="x")
    payload = serialize_instance(timed(), source, NEW_YORK)
    assert payload["calendar"] == {"id": 7, "name": "Family", "color": "#2563eb"}


def test_missing_source_still_renders(host_timezone):
    host_timezone("UTC")
    payload = serialize_instance(timed(), None, NEW_YORK)
    assert payload["calendar"] is None
    assert payload["title"] == "Dentist"
