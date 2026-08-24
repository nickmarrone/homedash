"""The music endpoints, including the app's first write path.

Most of these are about the difference between the three ways music can be
unavailable, because the panel has to tell them apart: a deployment with no
speakers at all should hide the music UI entirely, while one whose speakers are
merely asleep should keep it and show nothing playing.
"""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pyheos import CommandFailedError, ConnectionState

from app.api.routes import music as routes_module
from app.api.routes import router
from app.music.base import Album, Artist, Track
from app.music.heos import HeosController
from app.music.jellyfin import JellyfinError
from app.music.queue import QueueManager
from app.music.tokens import TokenStore
from fake_heos import FakeHeos


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
        response = client.post("/api/music/players/1/transport", json={"action": action})
        assert response.status_code == 200
        assert heos.players[1].calls[0][0] == action
    finally:
        monkey.undo()


@pytest.mark.parametrize(
    "body,status",
    [
        # Malformed body: the schema rejects it and names the field. 422.
        ({}, 422),
        ({"action": 3}, 422),
        # Well-formed, but not an action this speaker has. The route's own
        # closed set answers those, so it stays a 400.
        ({"action": ""}, 400),
        ({"action": "eject"}, 400),
    ],
)
def test_an_unsupported_transport_action_is_rejected_before_the_speaker_sees_it(body, status):
    """The allowed set is closed before the command is sent, so a typo cannot
    reach the speaker as an unrecognised HEOS command.

    Two ways to be rejected, and they are worth telling apart. A body that is
    not the right shape fails the schema, which answers 422 saying which field
    and why. A body that is the right shape but names an action that does not
    exist is a domain question the schema cannot answer, and stays a 400
    listing what there is. These used to all be hand-rolled 400s.
    """
    controller, heos = connected_controller()
    client, monkey = make_client(controller=controller)
    try:
        assert client.post("/api/music/players/1/transport", json=body).status_code == status
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
    a naive range check accepts it and sets the volume to 1. `"40"` likewise -
    the field is strict, so a string that looks like a number is not one.

    422 rather than the hand-written 400 this replaced: the schema knows which
    field was wrong and what it wanted, and says so.
    """
    controller, heos = connected_controller()
    client, monkey = make_client(controller=controller)
    try:
        assert client.post("/api/music/players/1/volume", json={"level": level}).status_code == 422
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
        # sort_name rides along so the panel's A-Z rail can index on the same
        # string the list was ordered by, rather than on a display name the
        # library files under a different letter.
        assert artists["items"] == [
            {"id": "a1", "name": "The Artist", "sort_name": "Artist, The"}
        ]

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
        assert 1 not in queues.queues
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


def test_a_homedash_queue_answers_for_the_speaker_about_what_is_playing():
    """The bug this fixes: a HEOS speaker handed a bare URL has no metadata for
    it and describes the stream instead, so the panel showed a bitrate where
    the song title goes and the speaker's own art URL where the cover goes.
    HomeDash sent the track, so HomeDash says what it is."""
    controller, heos = connected_controller()
    # What a speaker actually reports for a URL it was handed.
    heos.players[1].now_playing_media.song = "160kbps MP3"
    heos.players[1].now_playing_media.artist = None
    heos.players[1].now_playing_media.album = None
    heos.players[1].now_playing_media.image_url = "http://speaker/station.jpg"
    heos.players[1].now_playing_media.duration = 0

    queues, _ = queue_manager()
    client, monkey = make_client(controller=controller, library=FakeLibrary(), queues=queues)
    try:
        client.post("/api/music/players/1/play", json={"album_id": "b1"})
        playing = client.get("/api/music/players").json()["players"][0]["now_playing"]
        assert playing["title"] == "One"
        assert playing["artist"] == "A"
        assert playing["album"] == "B"
        # Proxied through HomeDash, so the Jellyfin API key stays server-side.
        assert playing["image_url"] == "/api/music/art/b1"
        # The speaker reports 0 for a stream it did not choose; Jellyfin knows.
        assert playing["duration_ms"] == 1000
        # ...but the speaker is still the only one who knows how far in it is.
        assert playing["position_ms"] == 12000
    finally:
        monkey.undo()


def test_a_speaker_playing_its_own_source_keeps_its_own_metadata():
    """The override applies only where HomeDash owns the queue. A speaker
    playing Spotify or a radio station reports perfectly good metadata, and
    replacing it would be a regression on the transport-only setup."""
    controller, _ = connected_controller()
    queues, _ = queue_manager()
    client, monkey = make_client(controller=controller, library=FakeLibrary(), queues=queues)
    try:
        playing = client.get("/api/music/players").json()["players"][0]["now_playing"]
        assert playing["title"] == "Weightless"
        assert playing["image_url"] == "http://speaker/art.jpg"
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


# -- how a half-configured deployment announces itself ------------------------
#
# Both of these are about the log line and nothing else, which is unusual for a
# test. It earns its place because the failure it guards against is *silence*:
# a speaker address with the flag left off produced no log, no UI, and a 503
# from a route the panel calls and discards, so the only evidence that anything
# was wrong was the absence of a feature.


def _start_music_with(monkeypatch, **overrides):
    """Run start_music() against patched settings, without touching a network.

    HeosController is replaced wholesale: this is about what start_music
    decides, and building a real one would start an asyncio task looking for a
    speaker that is not there.

    start_music assigns four module globals, so they are restored afterwards.
    Nothing depends on that today - every route test patches `get_controller`
    directly - but leaving a _NullController installed process-wide is the kind
    of residue that makes some future test pass or fail depending on the order
    pytest happened to collect it in.
    """
    from app.music import service

    for name in ("_controller", "_library", "_tokens", "_queues"):
        monkeypatch.setattr(service, name, getattr(service, name), raising=False)
    for key, value in overrides.items():
        monkeypatch.setattr(service.settings, key, value)
    monkeypatch.setattr(service, "HeosController", lambda *a, **k: _NullController())
    service.start_music()


class _NullController:
    def start(self):
        pass


def test_a_speaker_address_with_the_flag_off_says_so(monkeypatch, caplog):
    with caplog.at_level("WARNING"):
        _start_music_with(monkeypatch, music_enabled=False, heos_host="192.168.4.212")

    assert "HOMEDASH_MUSIC_ENABLED" in caplog.text
    # The address is echoed back so the line is self-evidently about *this*
    # deployment rather than generic advice.
    assert "192.168.4.212" in caplog.text


def test_the_flag_on_with_no_speaker_address_says_so(monkeypatch, caplog):
    with caplog.at_level("WARNING"):
        _start_music_with(monkeypatch, music_enabled=True, heos_host="")

    assert "HOMEDASH_HEOS_HOST" in caplog.text


def test_a_panel_with_no_music_at_all_stays_quiet(monkeypatch, caplog):
    """The one case that must not warn: most panels have no speakers."""
    with caplog.at_level("WARNING"):
        _start_music_with(monkeypatch, music_enabled=False, heos_host="")

    assert caplog.text == ""


def test_a_configured_deployment_logs_that_it_is_starting(monkeypatch, caplog):
    """Answers "did the feature start" without waiting on the speakers."""
    with caplog.at_level("INFO"):
        _start_music_with(
            monkeypatch,
            music_enabled=True,
            heos_host="192.168.4.212",
            jellyfin_url="",
            jellyfin_api_key="",
        )

    assert "192.168.4.212" in caplog.text
    assert "transport only" in caplog.text


class TestASpeakerThatHasGoneAway:
    """What the API says when the speakers are not reachable.

    `connected` used to mean "has connected at some point" - pyheos reconnects
    underneath the controller without ever clearing the handle, so a system
    that dropped off wifi during the evening went on answering True. The panel
    switches on exactly that field to decide whether the music UI is healthy,
    so it kept offering buttons whose commands could only fail - and the
    failure was a 500 with a stack trace, because the routes caught nothing
    but KeyError.
    """

    def test_a_dropped_connection_is_not_reported_as_connected(self):
        controller, heos = connected_controller()
        assert controller.connected is True

        heos.connection_state = ConnectionState.DISCONNECTED

        assert controller.connected is False

    def test_reconnecting_is_not_connected_either(self):
        """It resolves itself within seconds, but until it does a command sent
        now will not arrive - and that is the question being asked."""
        controller, heos = connected_controller()
        heos.connection_state = ConnectionState.RECONNECTING

        assert controller.connected is False

    def test_the_players_endpoint_reports_it_rather_than_erroring(self):
        """A read stays a 200 while the speakers are away, which is what lets
        the panel tell 'no speakers right now' from 'no music here at all'."""
        controller, heos = connected_controller()
        client, monkey = make_client(controller=controller)
        heos.connection_state = ConnectionState.DISCONNECTED
        try:
            payload = client.get("/api/music/players").json()
        finally:
            monkey.undo()

        assert payload["connected"] is False

    def test_a_command_to_an_away_speaker_is_503_not_500(self):
        """A write does not get to pretend. 503 rather than an unhandled
        exception, and rather than a 200 the speaker never heard."""
        controller, heos = connected_controller()
        client, monkey = make_client(controller=controller)
        heos.connection_state = ConnectionState.DISCONNECTED
        try:
            response = client.post("/api/music/players/1/transport", json={"action": "play"})
        finally:
            monkey.undo()

        assert response.status_code == 503

    def test_a_refused_command_is_502(self, monkeypatch):
        """The speaker is reachable but will not do it - failing firmware, or a
        verb it does not support. Upstream's fault, so 502; not ours, so not
        500; not the caller's, so not 4xx."""
        controller, _ = connected_controller()

        async def refuse(player_id, action):
            raise CommandFailedError("player/set_play_state", "boom", 1, 2)

        monkeypatch.setattr(controller, "transport", refuse)
        client, monkey = make_client(controller=controller)
        try:
            response = client.post("/api/music/players/1/transport", json={"action": "play"})
        finally:
            monkey.undo()

        assert response.status_code == 502

    def test_an_unknown_player_is_still_404(self):
        """The one failure here that really is the caller's fault."""
        controller, _ = connected_controller()
        client, monkey = make_client(controller=controller)
        try:
            response = client.post("/api/music/players/99/transport", json={"action": "play"})
        finally:
            monkey.undo()

        assert response.status_code == 404
