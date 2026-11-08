"""The switch that decides the music objects exist, and how they are wired."""

import asyncio
import logging

import pytest

from app.config import Settings
from app.music import service


class FakeController:
    instances: list["FakeController"] = []

    def __init__(self, host, on_change=None, on_state=None) -> None:
        self.host = host
        self.on_change = on_change
        self.on_state = on_state
        self.started = False
        self.stopped = False
        FakeController.instances.append(self)

    def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    """The singletons are process-wide, so every test gets its own and leaves none."""
    FakeController.instances = []
    for name in ("_controller", "_library", "_tokens", "_queues", "_playback"):
        monkeypatch.setattr(service, name, None)
    monkeypatch.setattr(service, "HeosController", FakeController)


def configure(monkeypatch, **kwargs):
    monkeypatch.setattr(service, "settings", Settings(_env_file=None, **kwargs))


SPEAKERS = {"music_enabled": True, "heos_host": "10.0.0.5"}
LIBRARY = {
    **SPEAKERS,
    "jellyfin_url": "http://jellyfin:8096",
    "jellyfin_api_key": "secret",
    "public_base_url": "http://homedash:8000",
}


class TestConfigured:
    @pytest.mark.parametrize(
        "settings,expected",
        [
            ({}, False),
            ({"music_enabled": True}, False),
            ({"heos_host": "10.0.0.5"}, False),
            (SPEAKERS, True),
        ],
    )
    def test_music_needs_both_the_flag_and_a_host(self, monkeypatch, settings, expected):
        configure(monkeypatch, **settings)
        assert service.music_configured() is expected

    @pytest.mark.parametrize(
        "settings,expected",
        [
            (SPEAKERS, False),
            ({**SPEAKERS, "jellyfin_url": "http://j"}, False),
            ({**SPEAKERS, "jellyfin_api_key": "k"}, False),
            ({"jellyfin_url": "http://j", "jellyfin_api_key": "k"}, False),
            ({**SPEAKERS, "jellyfin_url": "http://j", "jellyfin_api_key": "k"}, True),
        ],
    )
    def test_a_library_needs_speakers_and_both_jellyfin_settings(
        self, monkeypatch, settings, expected
    ):
        configure(monkeypatch, **settings)
        assert service.library_configured() is expected


class TestStart:
    def test_nothing_is_built_for_a_panel_with_no_speakers(self, monkeypatch):
        configure(monkeypatch)
        service.start_music()
        assert FakeController.instances == []
        assert service.get_playback() is None
        assert service.get_tokens() is None

    def test_speakers_alone_start_with_transport_only(self, monkeypatch):
        configure(monkeypatch, **SPEAKERS)
        service.start_music()
        [controller] = FakeController.instances
        assert controller.host == "10.0.0.5"
        assert controller.started is True
        assert service.get_playback() is not None
        assert service.get_tokens() is None
        assert service._queues is None

    def test_a_library_builds_the_queue_and_tokens(self, monkeypatch):
        configure(monkeypatch, **LIBRARY)
        service.start_music()
        assert service.get_tokens() is not None
        assert service._queues is not None
        assert service._library is not None

    def test_a_library_with_no_public_url_warns(self, monkeypatch, caplog):
        """The speaker fetches audio from HomeDash, so without an address it
        can route to, playback fails in a way nothing else explains."""
        configure(monkeypatch, **{**LIBRARY, "public_base_url": ""})
        with caplog.at_level(logging.WARNING):
            service.start_music()
        assert "HOMEDASH_PUBLIC_BASE_URL is empty" in caplog.text

    def test_a_complete_configuration_warns_about_nothing(self, monkeypatch, caplog):
        configure(monkeypatch, **LIBRARY)
        with caplog.at_level(logging.WARNING):
            service.start_music()
        assert caplog.records == []


class TestStop:
    def test_it_stops_the_controller_and_forgets_everything(self, monkeypatch):
        configure(monkeypatch, **LIBRARY)
        service.start_music()
        [controller] = FakeController.instances
        asyncio.run(service.stop_music())
        assert controller.stopped is True
        assert service.get_playback() is None
        assert service.get_tokens() is None
        assert service._queues is None
        assert service._library is None

    def test_stopping_what_never_started_is_fine(self, monkeypatch):
        configure(monkeypatch)
        asyncio.run(service.stop_music())


class TestWiring:
    def test_a_change_on_the_speakers_wakes_the_panel(self, monkeypatch):
        published = []
        monkeypatch.setattr(service.broadcaster, "publish", published.append)
        configure(monkeypatch, **SPEAKERS)
        service.start_music()
        FakeController.instances[0].on_change()
        assert published == ["music.updated"]

    def test_speaker_state_reaches_the_queue(self, monkeypatch):
        configure(monkeypatch, **LIBRARY)
        service.start_music()
        seen = []

        async def on_state(player_id, state):
            seen.append((player_id, state))

        monkeypatch.setattr(service._queues, "on_state", on_state)
        asyncio.run(FakeController.instances[0].on_state(7, "stop"))
        assert seen == [(7, "stop")]

    def test_speaker_state_with_no_queue_is_ignored(self, monkeypatch):
        configure(monkeypatch, **SPEAKERS)
        service.start_music()
        asyncio.run(FakeController.instances[0].on_state(7, "stop"))
