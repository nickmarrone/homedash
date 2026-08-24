"""Events that are already running when the agenda opens.

The agenda used to filter on `starts_at >= today`, which silently answers the
wrong question: it asks what *begins* from today rather than what *touches*
today. A week-long holiday therefore appeared on the wall on its first morning
and then vanished for six days, while the week and month grids - which have
always used a proper overlap - kept showing it. The agenda is the view the
panel spends most of its time on, and is always rendered in portrait, so this
was the most-looked-at surface in the app.

The times below are anchored to the real clock, because the endpoint reads
`datetime.now()` in the configured home timezone and a literal date would
drift out of range as real time passes.
"""

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import routes as routes_module
from app.api.routes import router
from app.config import Settings
from app.db import get_session
from app.models import CalendarSource, Event, EventInstance

HOME = "America/New_York"


@pytest.fixture
def client(session, monkeypatch):
    monkeypatch.setattr(
        routes_module,
        "settings",
        Settings(_env_file=None, home_timezone=HOME, week_starts_on="sunday"),
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: session
    return TestClient(app)


@pytest.fixture
def calendar(session) -> CalendarSource:
    source = CalendarSource(kind="ics", name="Family", color="#2563eb", url="https://x/a.ics")
    session.add(source)
    session.commit()
    return source


def add(session, calendar, starts_at, ends_at, title, all_day=False):
    event = Event(source_id=calendar.id, uid=f"uid-{title}", raw_vevent="x")
    session.add(event)
    session.flush()
    session.add(
        EventInstance(
            event_id=event.id,
            starts_at=starts_at,
            ends_at=ends_at,
            all_day=all_day,
            title=title,
        )
    )
    session.commit()


def today_home() -> date:
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo(HOME)).date()


def titles(payload) -> list[str]:
    return [item["title"] for item in payload]


def by_title(payload, title) -> dict:
    return next(item for item in payload if item["title"] == title)


class TestAllDayInProgress:
    def test_a_holiday_is_still_on_the_wall_on_its_third_morning(
        self, client, session, calendar
    ):
        """The headline bug. All-day rows carry a floating midnight rather
        than an instant, so this is also the path where the timezone handling
        has to be right at the same time as the overlap."""
        today = today_home()
        start = datetime(today.year, today.month, today.day) - timedelta(days=2)
        add(session, calendar, start, start + timedelta(days=7), "Holiday", all_day=True)

        assert "Holiday" in titles(client.get("/api/agenda").json())

    def test_it_is_filed_under_today_not_the_day_it_began(self, client, session, calendar):
        """A forward-looking list has no heading for a date that has already
        scrolled off it, so an in-progress event grouped by its real start
        date would be rendered under a day nobody can see."""
        today = today_home()
        start = datetime(today.year, today.month, today.day) - timedelta(days=2)
        add(session, calendar, start, start + timedelta(days=7), "Holiday", all_day=True)

        item = by_title(client.get("/api/agenda").json(), "Holiday")

        assert item["agenda_date"] == today.isoformat()

    def test_one_that_ended_yesterday_is_gone(self, client, session, calendar):
        """The overlap must not become 'everything, forever'. This is the
        assertion that stops the fix from simply widening the net."""
        today = today_home()
        midnight = datetime(today.year, today.month, today.day)
        add(session, calendar, midnight - timedelta(days=3), midnight - timedelta(days=1),
            "Last week", all_day=True)

        assert "Last week" not in titles(client.get("/api/agenda").json())


class TestTimedInProgress:
    def test_an_overnight_shift_still_running_this_morning_shows(
        self, client, session, calendar
    ):
        now = datetime.now(timezone.utc)
        add(session, calendar, now - timedelta(hours=3), now + timedelta(hours=3), "Night shift")

        assert "Night shift" in titles(client.get("/api/agenda").json())

    def test_one_that_finished_earlier_today_is_still_listed(
        self, client, session, calendar
    ):
        """The floor is the start of today, not the current moment, and that
        is deliberate: the whole of today stays on the list and the panel
        strikes through what is over (`hasPassed` in format.ts). Dropping
        finished items server-side would empty the morning off the wall by
        lunchtime and leave nothing to strike."""
        now = datetime.now(timezone.utc)
        add(session, calendar, now - timedelta(hours=3), now - timedelta(hours=1), "Finished")

        assert "Finished" in titles(client.get("/api/agenda").json())

    def test_a_meeting_that_began_this_morning_and_runs_all_day_shows(
        self, client, session, calendar
    ):
        now = datetime.now(timezone.utc)
        add(session, calendar, now - timedelta(minutes=30), now + timedelta(hours=1), "Standup")

        assert "Standup" in titles(client.get("/api/agenda").json())


class TestOrdinaryEventsAreUnaffected:
    def test_a_future_event_is_filed_under_its_own_day(self, client, session, calendar):
        now = datetime.now(timezone.utc)
        start = now + timedelta(days=3)
        add(session, calendar, start, start + timedelta(hours=1), "Dentist")

        item = by_title(client.get("/api/agenda").json(), "Dentist")

        assert item["agenda_date"] > today_home().isoformat()

    def test_every_item_carries_an_agenda_date(self, client, session, calendar):
        now = datetime.now(timezone.utc)
        add(session, calendar, now + timedelta(days=1), now + timedelta(days=1, hours=1), "Soon")

        assert all("agenda_date" in item for item in client.get("/api/agenda").json())


class TestTheTwoViewsAgree:
    def test_the_grid_and_the_agenda_both_show_an_in_progress_holiday(
        self, client, session, calendar
    ):
        """The divergence that made this a bug rather than a design choice:
        one predicate said yes and the other said no about the same row."""
        today = today_home()
        start = datetime(today.year, today.month, today.day) - timedelta(days=2)
        add(session, calendar, start, start + timedelta(days=7), "Holiday", all_day=True)

        agenda = titles(client.get("/api/agenda").json())
        grid = client.get(f"/api/calendar?view=day&anchor={today.isoformat()}").json()
        grid_titles = [i["title"] for day in grid["days"] for i in day["items"]]

        assert "Holiday" in agenda
        assert "Holiday" in grid_titles
