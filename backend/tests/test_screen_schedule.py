"""The wall panel's screen schedule and the endpoint its agent polls.

Times are asserted in the home timezone throughout, because that is the whole
point: the Pi's own clock is never consulted.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import routes as routes_module
from app.api.routes import router
from app.config import ScreenScheduleConfig, Settings
from app.db import get_session
from app.devices import (
    LAST_SEEN_THROTTLE,
    PANEL_DEVICE_ID,
    is_lit,
    next_transition,
    schedule_of,
    screen_state,
    seed_device_from_settings,
    touch_last_seen,
)
from app.models import Device

TZ = ZoneInfo("America/New_York")


def at(text: str) -> datetime:
    """A home-local instant, written the way a household would say it."""
    return datetime.fromisoformat(text).replace(tzinfo=TZ)


@pytest.fixture
def device(session) -> Device:
    device = Device(
        id=PANEL_DEVICE_ID,
        name="panel",
        screen_schedule=ScreenScheduleConfig().model_dump_json(),
    )
    session.add(device)
    session.commit()
    return device


@pytest.fixture
def client(session, monkeypatch):
    monkeypatch.setattr(
        routes_module,
        "settings",
        Settings(_env_file=None, home_timezone="America/New_York"),
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: session
    return TestClient(app)


class TestWindowBoundaries:
    schedule = ScreenScheduleConfig(on="06:30", off="21:30")

    @pytest.mark.parametrize(
        "moment,lit",
        [
            ("2026-08-19T06:29", False),
            ("2026-08-19T06:30", True),   # on is inclusive
            ("2026-08-19T21:29", True),
            ("2026-08-19T21:30", False),  # off is exclusive
            ("2026-08-19T00:00", False),
        ],
    )
    def test_state_either_side_of_each_boundary(self, moment, lit):
        assert is_lit(self.schedule, at(moment)) is lit

    def test_next_transition_is_the_upcoming_boundary(self):
        assert next_transition(self.schedule, at("2026-08-19T12:00"), TZ) == at(
            "2026-08-19T21:30"
        )

    def test_next_transition_rolls_into_tomorrow(self):
        assert next_transition(self.schedule, at("2026-08-19T22:00"), TZ) == at(
            "2026-08-20T06:30"
        )


class TestOvernightWrap:
    """`on` later than `off` is a window crossing midnight, not an error."""

    schedule = ScreenScheduleConfig(on="22:00", off="06:00")

    @pytest.mark.parametrize(
        "moment,lit",
        [
            ("2026-08-19T21:59", False),
            ("2026-08-19T22:00", True),
            ("2026-08-20T05:59", True),
            ("2026-08-20T06:00", False),
        ],
    )
    def test_screen_is_lit_across_midnight(self, moment, lit):
        assert is_lit(self.schedule, at(moment)) is lit

    def test_transition_out_of_an_overnight_window(self):
        assert next_transition(self.schedule, at("2026-08-19T23:00"), TZ) == at(
            "2026-08-20T06:00"
        )


class TestWeekendOverride:
    schedule = ScreenScheduleConfig(
        on="06:30", off="21:30", weekend={"on": "08:00", "off": "22:00"}
    )

    def test_weekday_window_applies_monday_to_friday(self):
        assert is_lit(self.schedule, at("2026-08-21T07:00")) is True   # Friday

    def test_weekend_window_applies_saturday_and_sunday(self):
        assert is_lit(self.schedule, at("2026-08-22T07:00")) is False  # Saturday
        assert is_lit(self.schedule, at("2026-08-22T08:00")) is True
        assert is_lit(self.schedule, at("2026-08-23T21:45")) is True   # Sunday

    def test_consecutive_boundaries_that_are_not_transitions_are_skipped(self):
        """Friday's 21:30 off and Saturday's 08:00 on are adjacent boundaries,
        but the screen is off across both - the next real change is Saturday
        morning, not Friday night."""
        assert next_transition(self.schedule, at("2026-08-21T21:45"), TZ) == at(
            "2026-08-22T08:00"
        )


class TestConstantSchedule:
    def test_equal_times_mean_always_on_with_no_transition(self):
        """Read as "always on" rather than a zero-length window: a schedule
        that blanks the panel forever is far likelier to be a typo."""
        schedule = ScreenScheduleConfig(on="00:00", off="00:00")
        assert is_lit(schedule, at("2026-08-19T03:00")) is True
        assert next_transition(schedule, at("2026-08-19T03:00"), TZ) is None


class TestDaylightSaving:
    """A schedule is wall-clock, so it lands on the same reading of the clock
    on either side of a transition rather than drifting by an hour."""

    schedule = ScreenScheduleConfig(on="06:30", off="21:30")

    def test_spring_forward_day(self):
        # 2026-03-08, clocks jump 02:00 -> 03:00 in America/New_York.
        assert is_lit(self.schedule, at("2026-03-08T06:29")) is False
        assert is_lit(self.schedule, at("2026-03-08T06:30")) is True
        assert next_transition(self.schedule, at("2026-03-08T12:00"), TZ) == at(
            "2026-03-08T21:30"
        )

    def test_fall_back_day(self):
        # 2026-11-01, clocks repeat 01:00-02:00.
        assert is_lit(self.schedule, at("2026-11-01T06:30")) is True
        assert next_transition(self.schedule, at("2026-11-01T12:00"), TZ) == at(
            "2026-11-01T21:30"
        )


class TestSchedulePersistence:
    def test_seeding_writes_the_configured_schedule(self, session, monkeypatch):
        import app.devices as devices_module

        monkeypatch.setattr(
            devices_module,
            "settings",
            Settings(_env_file=None, screen_schedule='{"on": "07:00", "off": "20:00"}'),
        )
        device = seed_device_from_settings(session)
        assert schedule_of(device).on == "07:00"

    def test_seeding_is_idempotent_and_preserves_last_seen(self, session, monkeypatch):
        import app.devices as devices_module

        monkeypatch.setattr(devices_module, "settings", Settings(_env_file=None))
        first = seed_device_from_settings(session)
        seen = datetime(2026, 8, 19, 12, tzinfo=timezone.utc)
        first.last_seen = seen
        session.add(first)
        session.commit()

        again = seed_device_from_settings(session)
        assert again.id == first.id
        assert again.last_seen is not None

    def test_unreadable_schedule_falls_back_to_config(self, session):
        """A panel going dark is a miserable way to discover a bad row."""
        device = Device(id=99, name="panel", screen_schedule="{not json")
        assert schedule_of(device).on == ScreenScheduleConfig().on


class TestLastSeenThrottle:
    def test_first_check_in_is_recorded(self, session, device):
        now = datetime(2026, 8, 19, 12, tzinfo=timezone.utc)
        touch_last_seen(session, device, now)
        assert device.last_seen is not None

    def test_a_second_poll_within_the_window_does_not_rewrite(self, session, device):
        now = datetime(2026, 8, 19, 12, tzinfo=timezone.utc)
        touch_last_seen(session, device, now)
        first = device.last_seen
        touch_last_seen(session, device, now + timedelta(seconds=5))
        assert device.last_seen == first

    def test_a_poll_after_the_window_records_again(self, session, device):
        now = datetime(2026, 8, 19, 12, tzinfo=timezone.utc)
        touch_last_seen(session, device, now)
        later = now + LAST_SEEN_THROTTLE + timedelta(seconds=1)
        touch_last_seen(session, device, later)
        assert device.last_seen is not None
        assert device.last_seen != now


class TestScreenEndpoint:
    def test_payload_shape(self, client, device):
        response = client.get(f"/api/devices/{PANEL_DEVICE_ID}/screen")
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"state", "until", "poll_after_seconds"}
        assert body["state"] in ("on", "off")
        assert body["poll_after_seconds"] > 0

    def test_unknown_device_is_404(self, client, device):
        assert client.get("/api/devices/424242/screen").status_code == 404

    def test_polling_records_the_check_in(self, client, session, device):
        assert device.last_seen is None
        client.get(f"/api/devices/{PANEL_DEVICE_ID}/screen")
        session.refresh(device)
        assert device.last_seen is not None

    def test_state_is_computed_in_the_home_timezone(self, session, device):
        """The same instant reads differently in two zones - the configured
        one wins, never the host's."""
        instant = datetime(2026, 8, 19, 23, 0, tzinfo=timezone.utc)  # 19:00 in New York
        device.screen_schedule = ScreenScheduleConfig(
            on="06:30", off="21:30"
        ).model_dump_json()
        assert screen_state(device, instant, TZ)["state"] == "on"
        assert screen_state(device, instant, ZoneInfo("Europe/London"))["state"] == "off"
