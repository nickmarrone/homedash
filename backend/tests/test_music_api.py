"""The music endpoints, including the app's first write path.

Most of these are about the difference between the three ways music can be
unavailable, because the panel has to tell them apart: a deployment with no
speakers at all should hide the music UI entirely, while one whose speakers are
merely asleep should keep it and show nothing playing.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import asyncio

from app.api import routes as routes_module
from app.api.routes import router
from app.music.base import Album, Artist, Track
from app.music.heos import HeosController
from app.music.jellyfin import JellyfinError
from app.music.queue import QueueManager
from app.music.tokens import TokenStore
from fake_heos import FakeHeos, FakePlayer


def run_(coro):
    return asyncio.run(coro)


def make_client(controller=None, configured=True, library=None, queues=None):
    monkey = pytest.MonkeyPatch()
    monkey.setattr(routes_module, "music_configured", lambda: configured)
    monkey.setattr(routes_module, "get_controller", lambda: controller)
    monkey.setattr(routes_module, "library_configured", lambda: library is not None)
    monkey.setattr(routes_module, "get_library", lambda: library)
    monkey.setattr(routes_module, "get_queues", lambda: queues)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), monkey


def connected_controller(players=None):
    heos = FakeHeos(players)

    async def connect(host):
        return heos

    controller = HeosController("10.0.0.5", connect=connect)
    controller._heos = heos
    controller._subscribe()
    return controller, heos


def test_a_panel_with_no_music_configured_gets_503_everywhere():
    """Not 404: the routes exist, and the panel distinguishes 'this HomeDash
    has no music' from 'that player is gone' by the status code."""
    client, monkey = make_client(configured=False)
    try:
        assert client.get("/api/music/players").status_code == 503
        assert (
            client.post("/api/music/players/1/transport", json={"action": "play"}).status_code
            == 503
        )
    finally:
        monkey.undo()


def test_players_answers_200_while_still_connecting():
    """The speakers are usually off at boot and the connection retries for as
    long as it takes. That is an ordinary state, not an error - the panel needs
    a body it can render, with `connected` false."""
    client, monkey = make_client(controller=None)
    try:
        response = client.get("/api/music/players")
        assert response.status_code == 200
        assert response.json() == {"connected": False, "library": False, "players": []}
    finally:
        monkey.undo()


def test_a_command_sent_before_the_connection_is_up_is_refused():
    """Reads degrade to 'nothing yet'; writes must not, or a tap on the wall
    would report success while the speaker never heard it."""
    client, monkey = make_client(controller=None)
    try:
        response = client.post("/api/music/players/1/transport", json={"action": "play"})
        assert response.status_code == 503
    finally:
        monkey.undo()


def test_players_reports_the_speakers_once_connected():
    controller, _ = connected_controller()
    client, monkey = make_client(controller=controller)
    try:
        body = client.get("/api/music/players").json()
        assert body["connected"] is True
        assert [p["name"] for p in body["players"]] == ["Kitchen"]
    finally:
        monkey.undo()


@pytest.mark.parametrize("action", ["play", "pause", "stop", "next", "previous"])
def test_every_supported_transport_action_is_accepted(action):
    controller, heos = connected_controller()
    client, monkey = make_client(controller=controller)
    try:
        response = client.post(f"/api/music/players/1/transport", json={"action": action})
        assert response.status_code == 200
        assert heos.players[1].calls[0][0] == action
    finally:
        monkey.undo()


@pytest.mark.parametrize("body", [{}, {"action": ""}, {"action": "eject"}, {"action": 3}])
def test_an_unsupported_transport_action_is_rejected_before_the_speaker_sees_it(body):
    """The allowed set is closed at the route rather than passed through, so a
    typo cannot reach the speaker as an unrecognised HEOS command."""
    controller, heos = connected_controller()
    client, monkey = make_client(controller=controller)
    try:
        assert client.post("/api/music/players/1/transport", json=body).status_code == 400
        assert heos.players[1].calls == []
    finally:
        monkey.undo()


def test_a_command_for_an_unknown_player_is_404():
    controller, _ = connected_controller()
    client, monkey = make_client(controller=controller)
    try:
        response = client.post(
            "/api/music/players/77/transport", json={"action": "play"}
        )
        assert response.status_code == 404
    finally:
        monkey.undo()


def test_volume_is_set_when_it_is_in_range():
    controller, heos = connected_controller()
    client, monkey = make_client(controller=controller)
    try:
        assert client.post("/api/music/players/1/volume", json={"level": 45}).status_code == 200
        assert heos.players[1].calls == [("volume", 45)]
    finally:
        monkey.undo()


@pytest.mark.parametrize("level", [-1, 101, "40", 40.5, None, True])
def test_an_out_of_range_or_wrongly_typed_volume_never_reaches_the_speaker(level):
    """Rejecting rather than clamping. A clamp would turn a caller bug into a
    speaker that quietly went to full volume, which is a memorable way to find
    out about it in a kitchen at 6am.

    `True` is in this list on purpose: bool is a subclass of int in Python, so
    a naive range check accepts it and sets the volume to 1.
    """
    controller, heos = connected_controller()
    client, monkey = make_client(controller=controller)
    try:
        assert client.post("/api/music/players/1/volume", json={"level": level}).status_code == 400
        assert heos.players[1].calls == []
    finally:
        monkey.undo()


# --------------------------------------------------------------------------
# The library half: browse, play, and the stream endpoint the speaker fetches.
# --------------------------------------------------------------------------


class FakeLibrary:
    """Just enough JellyfinLibrary for the routes."""

    def __init__(self, tracks=None, error=None):
        self._tracks = tracks if tracks is not None else [
            Track(id="t1", title="One", artist="A", album="B", duration_ms=1000, track_number=1),
            Track(id="t2", title="Two", artist="A", album="B", duration_ms=1000, track_number=2),
        ]
        self._error = error
        self.headers = {"Authorization": "MediaBrowser Token=\"x\""}

    def _maybe_fail(self):
        if self._error:
            raise self._error

    def artists(self):
        self._maybe_fail()
        return [Artist(id="a1", name="Artist")]

    def albums(self, artist_id=None):
        self._maybe_fail()
        return [Album(id="b1", name="Album", artist="Artist", year=2001)]

    def tracks(self, album_id):
        self._maybe_fail()
        return list(self._tracks)


def queue_manager():
    played = []

    async def play_url(player_id, url):
        played.append((player_id, url))

    return QueueManager(play_url=play_url, url_for=lambda t: f"http://h/{t.id}"), played


def test_browsing_without_a_library_configured_is_503():
    """Speakers without Jellyfin is a coherent setup - the panel still controls
    what is already playing - so this is gated separately from the transport
    routes rather than turning the whole music UI off."""
    controller, _ = connected_controller()
    client, monkey = make_client(controller=controller, library=None)
    try:
        assert client.get("/api/music/library").status_code == 503
    finally:
        monkey.undo()


def test_browsing_returns_one_level_at_a_time():
    controller, _ = connected_controller()
    client, monkey = make_client(controller=controller, library=FakeLibrary())
    try:
        artists = client.get("/api/music/library?kind=artists").json()
        assert artists["kind"] == "artists"
        assert artists["items"] == [{"id": "a1", "name": "Artist"}]

        tracks = client.get("/api/music/library?kind=tracks&parent=b1").json()
        assert [t["title"] for t in tracks["items"]] == ["One", "Two"]
    finally:
        monkey.undo()


def test_asking_for_tracks_without_an_album_is_rejected():
    controller, _ = connected_controller()
    client, monkey = make_client(controller=controller, library=FakeLibrary())
    try:
        assert client.get("/api/music/library?kind=tracks").status_code == 400
        assert client.get("/api/music/library?kind=nonsense").status_code == 400
    finally:
        monkey.undo()


def test_a_jellyfin_failure_is_reported_as_upstream_not_as_our_own():
    """502 rather than 500, so the log says "check Jellyfin" instead of
    sending somebody into the HomeDash traceback."""
    controller, _ = connected_controller()
    library = FakeLibrary(error=JellyfinError("boom"))
    client, monkey = make_client(controller=controller, library=library)
    try:
        assert client.get("/api/music/library?kind=artists").status_code == 502
    finally:
        monkey.undo()


def test_playing_an_album_queues_every_track_and_starts_the_first():
    controller, _ = connected_controller()
    queues, played = queue_manager()
    client, monkey = make_client(controller=controller, library=FakeLibrary(), queues=queues)
    try:
        response = client.post("/api/music/players/1/play", json={"album_id": "b1"})
        assert response.status_code == 200
        assert response.json() == {"ok": True, "queued": 2}
        assert played == [(1, "http://h/t1")]
    finally:
        monkey.undo()


def test_playing_on_an_unknown_player_is_404_before_anything_is_fetched():
    controller, _ = connected_controller()
    queues, played = queue_manager()
    client, monkey = make_client(controller=controller, library=FakeLibrary(), queues=queues)
    try:
        assert client.post("/api/music/players/99/play", json={"album_id": "b1"}).status_code == 404
        assert played == []
    finally:
        monkey.undo()


def test_playing_an_album_with_no_tracks_is_404_rather_than_silent_success():
    controller, _ = connected_controller()
    queues, _ = queue_manager()
    client, monkey = make_client(
        controller=controller, library=FakeLibrary(tracks=[]), queues=queues
    )
    try:
        assert client.post("/api/music/players/1/play", json={"album_id": "b1"}).status_code == 404
    finally:
        monkey.undo()


def test_play_requires_something_to_play():
    controller, _ = connected_controller()
    queues, _ = queue_manager()
    client, monkey = make_client(controller=controller, library=FakeLibrary(), queues=queues)
    try:
        assert client.post("/api/music/players/1/play", json={}).status_code == 400
        # track_ids without the album they came from cannot be resolved.
        assert (
            client.post("/api/music/players/1/play", json={"track_ids": ["t1"]}).status_code
            == 400
        )
    finally:
        monkey.undo()


def test_playing_a_subset_of_an_album_keeps_only_the_requested_tracks():
    controller, _ = connected_controller()
    queues, played = queue_manager()
    client, monkey = make_client(controller=controller, library=FakeLibrary(), queues=queues)
    try:
        response = client.post(
            "/api/music/players/1/play",
            json={"track_ids": ["t2"], "parent_album_id": "b1"},
        )
        assert response.json()["queued"] == 1
        assert played == [(1, "http://h/t2")]
    finally:
        monkey.undo()


def test_skipping_moves_homedash_queue_rather_than_the_speakers_own():
    """Content sent as a URL never enters the speaker's queue, so HEOS's
    play_next has nothing to move to. If the route sent it anyway the skip
    button would do nothing at all, which a wall panel hides very well."""
    controller, heos = connected_controller()
    queues, played = queue_manager()
    client, monkey = make_client(controller=controller, library=FakeLibrary(), queues=queues)
    try:
        client.post("/api/music/players/1/play", json={"album_id": "b1"})
        heos.players[1].calls.clear()
        assert client.post(
            "/api/music/players/1/transport", json={"action": "next"}
        ).status_code == 200
        assert played[-1] == (1, "http://h/t2")
        assert heos.players[1].calls == []
    finally:
        monkey.undo()


def test_skipping_falls_through_to_the_speaker_when_there_is_no_queue():
    """A speaker playing from one of its own sources still has a working skip
    button, which is the whole reason the fall-through exists."""
    controller, heos = connected_controller()
    queues, _ = queue_manager()
    client, monkey = make_client(controller=controller, library=FakeLibrary(), queues=queues)
    try:
        client.post("/api/music/players/1/transport", json={"action": "next"})
        assert heos.players[1].calls == [("next",)]
    finally:
        monkey.undo()


def test_stopping_clears_the_queue_so_it_does_not_resume_by_itself():
    """The speaker reports a finished track and a deliberate stop identically.
    Without the clear, pressing stop would start the next track."""
    controller, _ = connected_controller()
    queues, played = queue_manager()
    client, monkey = make_client(controller=controller, library=FakeLibrary(), queues=queues)
    try:
        client.post("/api/music/players/1/play", json={"album_id": "b1"})
        client.post("/api/music/players/1/transport", json={"action": "stop"})
        assert queues.has(1) is False
        run_(queues.on_state(1, "stop"))
        assert played == [(1, "http://h/t1")]
    finally:
        monkey.undo()


def test_the_players_response_carries_the_queue_the_speaker_cannot_report():
    controller, _ = connected_controller()
    queues, _ = queue_manager()
    client, monkey = make_client(controller=controller, library=FakeLibrary(), queues=queues)
    try:
        client.post("/api/music/players/1/play", json={"album_id": "b1"})
        body = client.get("/api/music/players").json()
        assert body["library"] is True
        assert body["players"][0]["queue"] == {
            "position": 1,
            "length": 2,
            "remaining": 1,
            "track": {"id": "t1", "title": "One"},
        }
    finally:
        monkey.undo()


def test_an_unknown_stream_token_is_404_and_never_reaches_jellyfin():
    controller, _ = connected_controller()
    tokens = TokenStore()
    client, monkey = make_client(controller=controller, library=FakeLibrary())
    monkey.setattr(routes_module, "get_tokens", lambda: tokens)
    try:
        assert client.get("/api/music/s/not-a-token").status_code == 404
    finally:
        monkey.undo()
