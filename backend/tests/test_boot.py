"""Starting the app, which until this file nothing tested but a deployment.

Two layers, because they answer different questions. The first drives the
lifespan with its collaborators replaced, which is the only way to ask what
happens when one of them fails. The second starts a real server, because no
amount of the first proves the migrations run, the imports resolve, and a
panel can actually connect.
"""

import importlib
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from contextlib import contextmanager

import httpx
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient

from app.config import BACKEND_ROOT, Settings
from app.sse import broadcaster

# Not `from app import main`: the package exports a console-script function of
# that name, which is what that spelling would hand back.
main = importlib.import_module("app.main")


class FakeWatch:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def stop(self) -> None:
        self._calls.append("watch.stop")


@pytest.fixture
def lifespan_calls(monkeypatch, session) -> list[str]:
    """Replace everything the lifespan starts, recording the order."""
    calls: list[str] = []

    def record(name):
        def recorder(*args, **kwargs):
            calls.append(name)

        return recorder

    async def stop_music():
        calls.append("stop_music")

    def start_folder_watch(*args):
        calls.append("start_folder_watch")
        return FakeWatch(calls)

    monkeypatch.setattr(main, "engine", session.get_bind())
    monkeypatch.setattr(main, "run_migrations", record("run_migrations"))
    monkeypatch.setattr(main, "seed_calendars_from_settings", record("seed_calendars"))
    monkeypatch.setattr(main, "seed_device_from_settings", record("seed_device"))
    monkeypatch.setattr(broadcaster, "bind_loop", record("bind_loop"))
    monkeypatch.setattr(main, "refresh_weather", record("refresh_weather"))
    monkeypatch.setattr(main, "start_scheduler", record("start_scheduler"))
    monkeypatch.setattr(main, "start_folder_watch", start_folder_watch)
    monkeypatch.setattr(main, "start_music", record("start_music"))
    monkeypatch.setattr(main, "stop_music", stop_music)
    monkeypatch.setattr(main, "stop_scheduler", record("stop_scheduler"))
    return calls


class TestLifespan:
    def test_the_database_is_ready_before_anything_touches_it(self, lifespan_calls):
        with TestClient(main.create_app()):
            pass
        assert lifespan_calls[:3] == ["run_migrations", "seed_calendars", "seed_device"]

    def test_the_stream_is_bound_before_anything_can_publish(self, lifespan_calls):
        """A publish before `bind_loop` is dropped silently, so a scheduler job
        or a HEOS event that got in first would simply never reach the panel."""
        with TestClient(main.create_app()):
            pass
        bound = lifespan_calls.index("bind_loop")
        assert bound < lifespan_calls.index("start_scheduler")
        assert bound < lifespan_calls.index("start_music")

    def test_the_folder_watch_starts_after_the_first_scan_is_queued(self, lifespan_calls):
        """The watch only reports what changes from here on, so a folder filled
        while the container was down depends on that first scan."""
        with TestClient(main.create_app()):
            pass
        assert lifespan_calls.index("start_scheduler") < lifespan_calls.index("start_folder_watch")

    def test_it_serves_requests_once_started(self, lifespan_calls):
        with TestClient(main.create_app()) as client:
            assert client.get("/healthz").json() == {"status": "ok"}

    def test_shutting_down_stops_everything_it_started(self, lifespan_calls):
        with TestClient(main.create_app()):
            lifespan_calls.clear()
        assert sorted(lifespan_calls) == ["stop_music", "stop_scheduler", "watch.stop"]

    def test_nothing_is_stopped_until_shutdown(self, lifespan_calls):
        with TestClient(main.create_app()):
            assert not any("stop" in call for call in lifespan_calls)

    def test_an_unwatchable_photo_folder_does_not_stop_shutdown(
        self, lifespan_calls, monkeypatch
    ):
        monkeypatch.setattr(main, "start_folder_watch", lambda *args: None)
        with TestClient(main.create_app()):
            lifespan_calls.clear()
        assert sorted(lifespan_calls) == ["stop_music", "stop_scheduler"]

    @pytest.mark.parametrize("error", [RuntimeError("boom"), ValueError("not json")])
    def test_the_weather_cannot_stop_the_app_starting(self, lifespan_calls, monkeypatch, error):
        def refresh():
            raise error

        monkeypatch.setattr(main, "refresh_weather", refresh)
        with TestClient(main.create_app()) as client:
            assert client.get("/healthz").status_code == 200
        assert "start_scheduler" in lifespan_calls


class TestStaticMount:
    def _settings(self, frontend_dist):
        return Settings(_env_file=None, frontend_dist=frontend_dist)

    def test_it_serves_the_built_panel_at_the_root(self, monkeypatch, tmp_path):
        (tmp_path / "index.html").write_text("<title>panel</title>")
        monkeypatch.setattr(main, "settings", self._settings(tmp_path))
        response = TestClient(main.create_app()).get("/")
        assert response.status_code == 200
        assert "panel" in response.text

    def test_the_api_is_not_shadowed_by_the_panel(self, monkeypatch, tmp_path):
        """The mount is at `/`, so registering it first would swallow every route."""
        (tmp_path / "index.html").write_text("<title>panel</title>")
        monkeypatch.setattr(main, "settings", self._settings(tmp_path))
        assert TestClient(main.create_app()).get("/healthz").json() == {"status": "ok"}

    def test_it_starts_without_a_built_panel(self, monkeypatch, tmp_path):
        monkeypatch.setattr(main, "settings", self._settings(tmp_path / "missing"))
        client = TestClient(main.create_app())
        assert client.get("/healthz").status_code == 200
        assert client.get("/").status_code == 404


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@contextmanager
def running_server(tmp_path):
    """A real uvicorn, on a throwaway database, that cannot reach the internet.

    Run from `tmp_path` so a developer's backend/.env is not picked up.
    """
    port = free_port()
    log = tmp_path / "server.log"
    env = {
        **os.environ,
        "HOMEDASH_DB_PATH": str(tmp_path / "boot.db"),
        "HOMEDASH_PHOTOS_DIR": str(tmp_path / "photos"),
        "HOMEDASH_PHOTO_CACHE_DIR": str(tmp_path / "cache"),
        "HOMEDASH_COMETS_ENABLED": "false",
        "HTTP_PROXY": "http://127.0.0.1:9",
        "HTTPS_PROXY": "http://127.0.0.1:9",
        "NO_PROXY": "127.0.0.1",
    }
    base = f"http://127.0.0.1:{port}"
    with log.open("w") as output:
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)],
            cwd=tmp_path,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
    try:
        deadline = time.monotonic() + 30
        while True:
            assert process.poll() is None, f"server died:\n{log.read_text()}"
            try:
                if httpx.get(f"{base}/healthz", trust_env=False, timeout=1).is_success:
                    break
            except httpx.TransportError:
                pass
            assert time.monotonic() < deadline, f"server never came up:\n{log.read_text()}"
            time.sleep(0.05)
        yield base, process, tmp_path / "boot.db", log
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()


class TestARealServer:
    def test_it_comes_up_with_a_migrated_seeded_database(self, tmp_path):
        with running_server(tmp_path) as (_, _process, db_path, _log):
            with sqlite3.connect(db_path) as db:
                revision = db.execute("select version_num from alembic_version").fetchone()[0]
                devices = db.execute("select count(*) from devices").fetchone()[0]

        config = Config(str(BACKEND_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
        assert revision == ScriptDirectory.from_config(config).get_current_head()
        assert devices == 1

    def test_a_panel_can_connect_and_gets_a_heartbeat_at_once(self, tmp_path):
        with running_server(tmp_path) as (base, *_):
            with httpx.stream(
                "GET", f"{base}/api/events/stream", trust_env=False, timeout=5
            ) as response:
                assert response.headers["content-type"].startswith("text/event-stream")
                lines = response.iter_lines()
                assert next(lines) == "event: heartbeat"
                assert '"today"' in next(lines)

    def test_the_api_answers_on_an_empty_install(self, tmp_path):
        """No calendars, no photos, no weather, no speakers. Every first boot."""
        with running_server(tmp_path) as (base, *_):
            def get(path):
                return httpx.get(f"{base}{path}", trust_env=False, timeout=5)

            assert get("/api/agenda").json() == []
            assert get("/api/calendars").json() == []
            assert get("/api/calendar?view=month").status_code == 200
            assert get("/api/photos?orientation=landscape").json()["photos"] == []
            assert get("/api/music/players").status_code == 503

    def test_it_stops_promptly_with_a_panel_still_connected(self, tmp_path):
        """A panel holds this stream open permanently, so this is every restart.
        A server that waits for it gets SIGKILLed by `docker stop` instead."""
        with running_server(tmp_path) as (base, process, _, log):
            with httpx.stream(
                "GET", f"{base}/api/events/stream", trust_env=False, timeout=15
            ) as response:
                next(response.iter_lines())
                process.send_signal(signal.SIGTERM)
                exit_code = process.wait(timeout=8)
            # Negative because uvicorn re-raises the signal once it has finished
            # shutting down cleanly, which the log line is what actually proves.
            assert exit_code in (0, -signal.SIGTERM), log.read_text()
            assert "Application shutdown complete" in log.read_text()
