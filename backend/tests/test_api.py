"""The /api/calendar and /api/agenda endpoints, over a real database.

Constructed without the lifespan, so no migrations run, no scheduler starts
and no weather is fetched - the session fixture supplies the schema.
"""

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import routes as routes_module
from app.api.routes import router
from app.config import Settings
from app.db import get_session
from app.models import CalendarSource, Event, EventInstance


@pytest.fixture
def client(session, monkeypatch):
    monkeypatch.setattr(
        routes_module,
        "settings",
        Settings(_env_file=None, home_timezone="America/New_York", week_starts_on="sunday"),
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


def add_instance(session, calendar, starts_at, ends_at, title="Dentist", all_day=False):
    event = Event(source_id=calendar.id, uid=f"uid-{title}", raw_vevent="x")
    session.add(event)
    session.flush()
    instance = EventInstance(
        event_id=event.id,
        starts_at=starts_at,
        ends_at=ends_at,
        all_day=all_day,
        title=title,
    )
    session.add(instance)
    session.commit()
    return instance


def days_by_date(payload) -> dict:
    return {day["date"]: day for day in payload["days"]}


class TestShape:
    @pytest.mark.parametrize("view,expected", [("day", 1), ("week", 7)])
    def test_bucket_counts(self, client, view, expected):
        payload = client.get(f"/api/calendar?view={view}&anchor=2026-08-19").json()
        assert len(payload["days"]) == expected

    def test_month_is_whole_weeks(self, client):
        payload = client.get("/api/calendar?view=month&anchor=2026-08-19").json()
        assert len(payload["days"]) % 7 == 0
        assert payload["anchor"] == "2026-08-01"
        assert payload["title"] == "August 2026"

    def test_navigation_anchors_are_returned(self, client):
        payload = client.get("/api/calendar?view=month&anchor=2026-12-15").json()
        assert payload["prev_anchor"] == "2026-11-01"
        assert payload["next_anchor"] == "2027-01-01"

    def test_anchor_defaults_to_today(self, client):
        payload = client.get("/api/calendar?view=day").json()
        assert payload["anchor"] == payload["today"]

    def test_unknown_view_is_rejected(self, client):
        response = client.get("/api/calendar?view=decade")
        assert response.status_code == 400

    def test_malformed_anchor_is_rejected(self, client):
        response = client.get("/api/calendar?view=day&anchor=last-tuesday")
        assert response.status_code == 400


class TestContent:
    def test_a_timed_event_lands_on_its_local_day(self, client, session, calendar):
        # 16:00 UTC is noon in New York.
        add_instance(session, calendar, datetime(2026, 8, 19, 16, 0), datetime(2026, 8, 19, 17, 0))
        payload = client.get("/api/calendar?view=week&anchor=2026-08-19").json()

        day = days_by_date(payload)["2026-08-19"]
        assert [i["title"] for i in day["items"]] == ["Dentist"]
        assert day["items"][0]["starts_at"] == "2026-08-19T12:00:00-04:00"

    def test_a_late_evening_event_does_not_slip_to_the_next_day(self, client, session, calendar):
        """23:00 New York is 03:00 UTC the following day - bucketing on the
        stored instant would put it on the wrong date."""
        add_instance(session, calendar, datetime(2026, 8, 20, 3, 0), datetime(2026, 8, 20, 4, 0))
        payload = client.get("/api/calendar?view=week&anchor=2026-08-19").json()

        assert [d for d in payload["days"] if d["items"]][0]["date"] == "2026-08-19"

    def test_an_all_day_event_lands_on_its_own_date(self, client, session, calendar):
        add_instance(
            session, calendar, datetime(2026, 8, 19), datetime(2026, 8, 20),
            title="Camp", all_day=True,
        )
        payload = client.get("/api/calendar?view=week&anchor=2026-08-19").json()

        assert [d["date"] for d in payload["days"] if d["items"]] == ["2026-08-19"]

    def test_a_multi_day_event_spans_its_days(self, client, session, calendar):
        add_instance(
            session, calendar, datetime(2026, 8, 18), datetime(2026, 8, 21),
            title="Camp", all_day=True,
        )
        payload = client.get("/api/calendar?view=week&anchor=2026-08-19").json()

        assert [d["date"] for d in payload["days"] if d["items"]] == [
            "2026-08-18", "2026-08-19", "2026-08-20",
        ]

    def test_an_event_in_progress_when_the_week_opens_is_included(self, client, session, calendar):
        """The overlap predicate exists for this: a start-time cutoff would
        drop it from the view completely."""
        add_instance(
            session, calendar, datetime(2026, 8, 14), datetime(2026, 8, 20),
            title="Holiday", all_day=True,
        )
        payload = client.get("/api/calendar?view=week&anchor=2026-08-19").json()

        opening = payload["days"][0]
        assert opening["date"] == "2026-08-16"
        assert opening["items"][0]["continues_before"] is True

    def test_calendar_colour_travels_with_the_item(self, client, session, calendar):
        add_instance(session, calendar, datetime(2026, 8, 19, 16, 0), datetime(2026, 8, 19, 17, 0))
        payload = client.get("/api/calendar?view=day&anchor=2026-08-19").json()

        assert payload["days"][0]["items"][0]["calendar"]["color"] == "#2563eb"

    def test_agenda_and_calendar_agree_on_item_shape(self, client, session, calendar):
        """One frontend renderer serves both, so the shapes must not drift."""
        add_instance(session, calendar, datetime(2099, 1, 1, 16, 0), datetime(2099, 1, 1, 17, 0))
        agenda = client.get("/api/agenda").json()
        grid = client.get("/api/calendar?view=day&anchor=2099-01-01").json()

        grid_item = grid["days"][0]["items"][0]
        assert set(agenda[0]) <= set(grid_item)
        for key in agenda[0]:
            assert agenda[0][key] == grid_item[key]


class TestWeekStart:
    def test_monday_start_shifts_the_grid(self, client, monkeypatch):
        monkeypatch.setattr(
            routes_module,
            "settings",
            Settings(_env_file=None, home_timezone="America/New_York", week_starts_on="monday"),
        )
        payload = client.get("/api/calendar?view=week&anchor=2026-08-19").json()
        assert payload["days"][0]["date"] == "2026-08-17"
        assert payload["days"][0]["weekday_short"] == "Mon"
