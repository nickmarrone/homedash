"""When the background jobs first run.

Every job is registered with an explicit `next_run_time` so it fires at
startup rather than after one full interval - which is what makes the panel
show a calendar within seconds of a restart instead of within fifteen minutes.
That only works if the instant handed over means what the scheduler thinks it
means.
"""

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

import app.scheduler as scheduler_module
from app.config import Settings
from app.models import CalendarSource
from app.scheduler import _at_boot

INTERVALS = {"seconds", "minutes", "hours"}


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
        time.tzset()
        try:
            drift = abs((_at_boot() - datetime.now(timezone.utc)).total_seconds())
        finally:
            # monkeypatch puts the variable back, but the C library has already
            # read it; without this every later test runs in Berlin.
            monkeypatch.undo()
            time.tzset()

        assert drift < 10, f"boot instant is {drift}s away from now"


class TestJobs:
    """Each job's one obligation: tell the panel if, and only if, it changed."""

    @pytest.fixture
    def published(self, monkeypatch):
        seen = []
        monkeypatch.setattr(
            scheduler_module.broadcaster, "publish", lambda event, data=None: seen.append(event)
        )
        return seen

    @pytest.fixture
    def engine(self, monkeypatch, session):
        monkeypatch.setattr(scheduler_module, "engine", session.get_bind())

    @pytest.mark.parametrize(
        "job,function,changed_event",
        [
            ("run_weather_refresh", "refresh_weather", "weather.updated"),
            ("run_comet_refresh", "refresh_comet_elements", "weather.updated"),
        ],
    )
    @pytest.mark.parametrize("changed", [True, False])
    def test_a_refresh_publishes_only_a_real_change(
        self, monkeypatch, published, job, function, changed_event, changed
    ):
        monkeypatch.setattr(scheduler_module, function, lambda: changed)
        getattr(scheduler_module, job)()
        assert published == ([changed_event] if changed else [])

    @pytest.mark.parametrize("changed", [True, False])
    def test_a_photo_scan_publishes_only_a_real_change(
        self, monkeypatch, published, engine, changed
    ):
        monkeypatch.setattr(scheduler_module, "reindex", lambda session: changed)
        scheduler_module.run_photo_index()
        assert published == (["photos.updated"] if changed else [])

    @pytest.mark.parametrize(
        "job,function",
        [
            ("run_weather_refresh", "refresh_weather"),
            ("run_comet_refresh", "refresh_comet_elements"),
            ("run_photo_index", "reindex"),
        ],
    )
    def test_a_failing_job_is_logged_and_contained(
        self, monkeypatch, published, engine, caplog, job, function
    ):
        """APScheduler keeps running a job that raises, but says little; the
        panel's other jobs must not notice either way."""

        def fail(*args):
            raise RuntimeError("boom")

        monkeypatch.setattr(scheduler_module, function, fail)
        getattr(scheduler_module, job)()
        assert published == []
        assert "failed" in caplog.text

    def test_comets_stay_off_when_configured_off(self, monkeypatch, published):
        monkeypatch.setattr(
            scheduler_module, "settings", Settings(_env_file=None, comets_enabled=False)
        )
        called = []
        monkeypatch.setattr(
            scheduler_module, "refresh_comet_elements", lambda: called.append(True)
        )
        scheduler_module.run_comet_refresh()
        assert called == []

    def _sources(self, session):
        session.add(CalendarSource(kind="ics", name="Slow", url="https://x/1"))
        session.add(CalendarSource(kind="caldav", name="Fast", url="https://x/2"))
        session.add(
            CalendarSource(kind="google", name="Faster", url="https://x/3", calendar_id="c")
        )
        session.add(CalendarSource(kind="ics", name="Off", url="https://x/4", enabled=False))
        session.commit()

    @pytest.mark.parametrize(
        "job,expected",
        [("run_slow_sync", ["Slow"]), ("run_fast_sync", ["Fast", "Faster"])],
    )
    def test_each_cadence_syncs_its_own_kinds_and_no_disabled_ones(
        self, monkeypatch, session, engine, published, job, expected
    ):
        """ICS polled every minute would only re-fetch the same stale bytes,
        and a CalDAV source left on the slow job would lag fifteen minutes."""
        self._sources(session)
        synced = []

        def sync(_, sources):
            synced.extend(source.name for source in sources)
            return False

        monkeypatch.setattr(scheduler_module, "sync_sources", sync)
        getattr(scheduler_module, job)()
        assert sorted(synced) == expected

    @pytest.mark.parametrize("changed", [True, False])
    def test_a_sync_publishes_only_a_real_change(
        self, monkeypatch, engine, published, changed
    ):
        monkeypatch.setattr(scheduler_module, "sync_sources", lambda *_: changed)
        scheduler_module.run_slow_sync()
        assert published == (["events.updated"] if changed else [])


class TestRegistration:
    def _start(self, monkeypatch, **settings):
        fake = MagicMock()
        monkeypatch.setattr(scheduler_module, "scheduler", fake)
        monkeypatch.setattr(
            scheduler_module, "settings", Settings(_env_file=None, **settings)
        )
        scheduler_module.start_scheduler()
        return fake, {call.kwargs["id"]: call for call in fake.add_job.call_args_list}

    def test_every_job_is_registered_and_runs_at_boot(self, monkeypatch):
        _, jobs = self._start(monkeypatch, comets_enabled=True)
        assert set(jobs) == {
            "ics_sync",
            "fast_sync",
            "heartbeat",
            "comet_refresh",
            "photo_index",
            "weather_refresh",
        }
        assert all(job.kwargs["next_run_time"] is not None for job in jobs.values())

    def test_the_scheduler_is_actually_started(self, monkeypatch):
        fake, _ = self._start(monkeypatch)
        fake.start.assert_called_once()

    def test_each_job_runs_the_right_function_on_the_right_interval(self, monkeypatch):
        _, jobs = self._start(
            monkeypatch,
            comets_enabled=True,
            ics_poll_interval_minutes=15,
            fast_poll_interval_minutes=1,
            comet_refresh_hours=24,
            photo_index_interval_minutes=10,
            weather_cache_minutes=20,
        )
        summary = {
            name: (job.args[0], {k: v for k, v in job.kwargs.items() if k in INTERVALS})
            for name, job in jobs.items()
        }
        assert summary == {
            "ics_sync": (scheduler_module.run_slow_sync, {"minutes": 15}),
            "fast_sync": (scheduler_module.run_fast_sync, {"minutes": 1}),
            "heartbeat": (scheduler_module.run_heartbeat, {"seconds": 30}),
            "comet_refresh": (scheduler_module.run_comet_refresh, {"hours": 24}),
            "photo_index": (scheduler_module.run_photo_index, {"minutes": 10}),
            "weather_refresh": (scheduler_module.run_weather_refresh, {"minutes": 20}),
        }

    def test_comets_are_not_scheduled_when_off(self, monkeypatch):
        _, jobs = self._start(monkeypatch, comets_enabled=False)
        assert "comet_refresh" not in jobs

    def test_shutdown_does_not_wait_for_a_running_job(self, monkeypatch):
        """A sync stuck on a dead CalDAV server must not hold up `docker stop`."""
        fake = MagicMock()
        monkeypatch.setattr(scheduler_module, "scheduler", fake)
        scheduler_module.stop_scheduler()
        fake.shutdown.assert_called_once_with(wait=False)
