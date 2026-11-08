"""Stand-ins shared by the Playback tests and the music route tests."""

import asyncio

from app.music.base import Album, Artist, AudioStream, Track
from app.music.heos import HeosController, HeosSpeaker
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

    def __init__(self, tracks=None, error=None, audio=b"sound", status=200, headers=None):
        self._audio = audio
        self._status = status
        self._audio_headers = headers or {}
        self.opened: list[tuple[str, str | None]] = []
        self.closed = 0
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

    async def open_stream(self, item_id, range_header=None):
        self._maybe_fail()
        self.opened.append((item_id, range_header))

        async def chunks():
            yield self._audio

        async def close():
            self.closed += 1

        return AudioStream(self._status, "audio/flac", self._audio_headers, chunks(), close)


class RecordingSpeaker:
    """The test adapter for `queue.Speaker`: keeps every call, in order.

    `events` is one list rather than a list per verb, because most of what is
    worth asserting about the speaker's queue is ordering - a queue cleared
    before the stop it was meant to follow is a different bug from one that is
    never cleared. `played` and `stopped` are the two views most tests want.

    Given an `inner` speaker it passes every call on after recording it, so a
    test can watch the production adapter drive a FakeHeos without wiring it
    up by hand. A call is recorded once it completes.
    """

    def __init__(self, inner=None):
        self.inner = inner
        self.events: list[tuple] = []
        self.played: list[tuple[int, str]] = []
        self.stopped: list[int] = []

    async def play_url(self, player_id, url):
        if self.inner is not None:
            await self.inner.play_url(player_id, url)
        self.played.append((player_id, url))
        self.events.append(("play_url", url))

    async def stop(self, player_id):
        if self.inner is not None:
            await self.inner.stop(player_id)
        self.stopped.append(player_id)
        self.events.append(("stop",))

    async def clear_queue(self, player_id):
        if self.inner is not None:
            await self.inner.clear_queue(player_id)
        self.events.append(("clear_queue",))

    async def prune_queue(self, player_id):
        if self.inner is not None:
            await self.inner.prune_queue(player_id)
        self.events.append(("prune_queue",))


def queue_manager(controller=None):
    """A queue that records the URLs it sent, optionally driving a speaker.

    With a controller, the speaker is the production `HeosSpeaker` over it -
    not a copy of how `music/service.py` wires one, which is what this used to
    be, and which had already drifted from the real thing.
    """
    speaker = RecordingSpeaker(HeosSpeaker(controller) if controller is not None else None)
    manager = QueueManager(speaker=speaker, url_for=lambda t: f"http://h/{t.id}")
    return manager, speaker.played
