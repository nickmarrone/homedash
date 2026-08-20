"""The SSE heartbeat.

Its job is threefold: prove to the panel that the stream is alive, tell it what
day it is, and tell it whether its screen is meant to be lit. All three matter
only on a display that stays open for months.
"""

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.api.routes import event_stream
from app.config import ScreenScheduleConfig, Settings
from app.devices import PANEL_DEVICE_ID, screen_state
from app.models import Device
import app.scheduler as scheduler_module

# 10:30 UTC. The one hour a day when Kiritimati (+14), UTC, and Midway (-11)
# are all on different dates - so a heartbeat that quietly used UTC, or the
# host clock, produces neither of the answers asserted below.
FROZEN = datetime(2026, 8, 19, 10, 30, tzinfo=timezone.utc)


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return FROZEN.astimezone(tz) if tz else FROZEN


def publish_once(
    monkeypatch, *, freeze: bool = False, screen: str = "on", **settings_kwargs
) -> tuple[str, dict]:
    monkeypatch.setattr(
        scheduler_module, "settings", Settings(_env_file=None, **settings_kwargs)
    )
    # Stubbed so these stay about the payload rather than about the database;
    # what the screen state itself should be is TestScreenStatus's job.
    monkeypatch.setattr(scheduler_module, "screen_status", lambda session: screen)
    if freeze:
        monkeypatch.setattr(scheduler_module, "datetime", FrozenDatetime)
    with patch.object(scheduler_module.broadcaster, "publish") as publish:
        scheduler_module.run_heartbeat()
    (event_type, payload), _ = publish.call_args
    return event_type, payload


class TestHeartbeat:
    def test_publishes_a_named_event(self, monkeypatch):
        """A named event, not sse-starlette's ping. A ping is an SSE comment,
        which EventSource never surfaces to an addEventListener, so the panel
        could not tell a healthy quiet stream from a wedged one."""
        event_type, _ = publish_once(monkeypatch)
        assert event_type == "heartbeat"

    def test_carries_the_date_and_time(self, monkeypatch):
        _, payload = publish_once(monkeypatch)
        assert set(payload) == {"today", "now", "screen"}
        assert payload["now"].startswith(payload["today"])

    def test_date_is_in_the_home_timezone(self, monkeypatch):
        """At one instant, two zones 25 hours apart report different dates, and
        neither is UTC's. The configured zone decides - never the host clock."""
        _, kiritimati = publish_once(
            monkeypatch, freeze=True, home_timezone="Pacific/Kiritimati"
        )
        _, midway = publish_once(monkeypatch, freeze=True, home_timezone="Pacific/Midway")
        assert kiritimati["today"] == "2026-08-20"
        assert midway["today"] == "2026-08-18"
        # UTC would have said the 19th to both.

    def test_payload_is_json_serializable(self, monkeypatch):
        """It goes over the wire as JSON and is parsed by the panel."""
        _, payload = publish_once(monkeypatch)
        assert json.loads(json.dumps(payload)) == payload


class TestScheduling:
    def test_heartbeat_interval_is_well_under_the_client_stale_threshold(self):
        """The panel reloads after three missed heartbeats (100s in
        frontend/src/lib/watchdog.ts). Keep this interval comfortably under a
        third of that, or a single slow beat becomes a reload."""
        assert scheduler_module.HEARTBEAT_SECONDS <= 30


class TestHeartbeatOnConnect:
    """A panel must not have to wait for the scheduler to learn the time."""

    def test_the_stream_opens_with_a_heartbeat(self):
        """The panel greys out finished events and reads the date off this
        stream, using the server's clock rather than its own. Without an
        immediate heartbeat a freshly loaded panel is flying blind until the
        next scheduled one, up to 30 seconds later - which is exactly long
        enough to be seen as a bug.
        """

        class NeverDisconnects:
            async def is_disconnected(self):
                return False

        async def first_message():
            stream = event_stream(NeverDisconnects())
            try:
                return await anext(stream)
            finally:
                await stream.aclose()

        message = asyncio.run(first_message())

        assert message["event"] == "heartbeat"
        payload = json.loads(message["data"])
        assert payload["now"].startswith(payload["today"])


class TestScreenOnTheHeartbeat:
    def test_the_screen_state_rides_along(self, monkeypatch):
        """The screensaver must not start while the schedule says the display
        is meant to be dark, so the panel needs to know. It arrives here rather
        than by polling /api/devices/1/screen, which writes last_seen as a side
        effect - that field means "the screen agent is alive", and a second
        client writing it would blur that."""
        _, payload = publish_once(monkeypatch, screen="off")
        assert payload["screen"] == "off"

    def test_a_failure_reading_the_schedule_reports_on(self, monkeypatch):
        """Fail-safe direction matters: a panel showing the calendar when it
        could have been dark is a far smaller problem than a panel that blacks
        itself out because a query failed."""
        monkeypatch.setattr(
            scheduler_module, "settings", Settings(_env_file=None)
        )

        def explode(session):
            raise RuntimeError("no such table: devices")

        monkeypatch.setattr(scheduler_module, "screen_status", explode)
        with patch.object(scheduler_module.broadcaster, "publish") as publish:
            scheduler_module.run_heartbeat()

        (_, payload), _ = publish.call_args
        assert payload["screen"] == "on"


class TestScreenStatus:
    def test_it_agrees_with_what_the_pi_is_told(self, session, monkeypatch):
        """The browser and the screen agent read the same function. If these
        ever diverged, the panel would run a slideshow on a screen the agent
        had just switched off."""
        monkeypatch.setattr(
            scheduler_module,
            "settings",
            Settings(_env_file=None, home_timezone="America/New_York"),
        )
        device = Device(
            id=PANEL_DEVICE_ID,
            name="panel",
            screen_schedule=ScreenScheduleConfig(on="06:30", off="21:30").model_dump_json(),
        )
        session.add(device)
        session.commit()

        now = datetime.now(timezone.utc)
        expected = screen_state(device, now, ZoneInfo("America/New_York"))["state"]
        assert scheduler_module.screen_status(session) == expected

    def test_no_device_row_means_on(self, session, monkeypatch):
        """Before the first seed, or on a database restored without it. The
        calendar is the safe thing to show."""
        monkeypatch.setattr(scheduler_module, "settings", Settings(_env_file=None))
        assert scheduler_module.screen_status(session) == "on"
