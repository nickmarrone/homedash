"""When the background jobs first run.

Every job is registered with an explicit `next_run_time` so it fires at
startup rather than after one full interval - which is what makes the panel
show a calendar within seconds of a restart instead of within fifteen minutes.
That only works if the instant handed over means what the scheduler thinks it
means.
"""

import os
import time
from datetime import datetime, timedelta, timezone

import app.scheduler as scheduler_module
from app.scheduler import _at_boot


class TestBootInstant:
    def test_it_is_timezone_aware(self):
        """APScheduler localizes a naive datetime into the scheduler's own
        timezone rather than converting it. The scheduler is configured UTC, so
        a naive local `now()` is read as though the wall clock were already
        UTC - and every boot job lands hours in the future."""
        assert _at_boot().tzinfo is not None

    def test_it_is_utc(self):
        assert _at_boot().utcoffset() == timedelta(0)

    def test_the_scheduler_it_feeds_is_also_utc(self):
        """The two have to agree. This test is what makes the one above mean
        something rather than merely be true."""
        assert str(scheduler_module.scheduler.timezone) == "UTC"

    def test_it_is_now(self, monkeypatch):
        """The point of the whole exercise: the job runs at boot, not later.

        Pinned under a deliberately non-UTC TZ, because that is the condition
        the bug needed to become visible - nothing sets TZ in the container
        today, which is the only reason a naive `now()` looked fine. Under
        Europe/Berlin the naive version reads one or two hours fast, so a
        generous ten-second window still catches it.
        """
        monkeypatch.setenv("TZ", "Europe/Berlin")
        if hasattr(time, "tzset"):
            time.tzset()

        drift = abs((_at_boot() - datetime.now(timezone.utc)).total_seconds())

        assert drift < 10, f"boot instant is {drift}s away from now"
