"""The SSE heartbeat.

Its job is twofold: prove to the panel that the stream is alive, and tell it
what day it is. Both matter only on a display that stays open for months.
"""

import json
from datetime import datetime, timezone
from unittest.mock import patch

from app.config import Settings
import app.scheduler as scheduler_module

# 10:30 UTC. The one hour a day when Kiritimati (+14), UTC, and Midway (-11)
# are all on different dates - so a heartbeat that quietly used UTC, or the
# host clock, produces neither of the answers asserted below.
FROZEN = datetime(2026, 8, 19, 10, 30, tzinfo=timezone.utc)


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return FROZEN.astimezone(tz) if tz else FROZEN


def publish_once(monkeypatch, *, freeze: bool = False, **settings_kwargs) -> tuple[str, dict]:
    monkeypatch.setattr(
        scheduler_module, "settings", Settings(_env_file=None, **settings_kwargs)
    )
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
        assert set(payload) == {"today", "now"}
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
