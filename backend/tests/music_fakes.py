"""Stand-ins shared by the Playback tests and the music route tests."""

import asyncio

from app.music.base import Album, Artist, Track
from app.music.heos import HeosController
from app.music.queue import QueueManager
from fake_heos import FakeHeos


def run_(coro):
    return asyncio.run(coro)


def connected_controller(players=None):
    """A controller that has been through the real connect path.

    Deliberately `_run()` rather than assigning `_heos` and subscribing by
    hand: loading the player list is part of connecting, and a helper that
    skipped it was what let the controller ship reading an empty dict.
    """
    heos = FakeHeos(players)

    async def connect(host):
        return heos

    controller = HeosController("10.0.0.5", connect=connect)
    run_(controller._run())
    return controller, heos


def unconnected_controller():
    """A controller still looking for the speakers - the usual state at boot."""
    return HeosController("10.0.0.5")


class FakeLibrary:
    """Just enough JellyfinLibrary for Playback and the routes."""

    def __init__(self, tracks=None, error=None):
        self._tracks = tracks if tracks is not None else [
            Track(
                id="t1",
                title="One",
                artist="A",
                album="B",
                duration_ms=1000,
                track_number=1,
                album_id="b1",
            ),
            Track(
                id="t2",
                title="Two",
                artist="A",
                album="B",
                duration_ms=1000,
                track_number=2,
                album_id="b1",
            ),
        ]
        self._error = error
        self.headers = {"Authorization": "MediaBrowser Token=\"x\""}

    def _maybe_fail(self):
        if self._error:
            raise self._error

    def artists(self):
        self._maybe_fail()
        return [Artist(id="a1", name="The Artist", sort_name="Artist, The")]

    def albums(self, artist_id=None):
        self._maybe_fail()
        return [Album(id="b1", name="Album", artist="Artist", year=2001)]

    def tracks(self, album_id):
        self._maybe_fail()
        return list(self._tracks)


def queue_manager(controller=None):
    """A queue that records the URLs it sent, optionally driving a speaker.

    Without a controller it records and nothing else, which is what most of
    these tests want - and it is also the honest shape of a half-wired manager,
    so Playback is exercised against one that cannot stop a speaker.

    With one, it is wired the way `music/service.py` wires the real thing,
    including the two callables that tidy the speaker's own queue. That is the
    only way to test the thing the panel's stop button is actually for: leaving
    the speakers usable by something other than HomeDash.
    """
    played = []

    async def play_url(player_id, url):
        played.append((player_id, url))
        if controller is not None:
            await controller.play_url(player_id, url)

    manager = QueueManager(play_url=play_url, url_for=lambda t: f"http://h/{t.id}")
    if controller is not None:

        async def stop_player(player_id):
            await controller.transport(player_id, "stop")

        async def clear_speaker_queue(player_id):
            # Swallowed, as in `music/service.py`: HEOS errors on an empty
            # queue and that must not fail a stop somebody asked for.
            try:
                await controller.clear_queue(player_id)
            except Exception:
                pass

        manager.stop_player = stop_player
        manager.clear_speaker_queue = clear_speaker_queue
        manager.prune_speaker_queue = controller.prune_queue
    return manager, played
