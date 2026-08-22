"""The music endpoints, including the app's first write path.

Most of these are about the difference between the three ways music can be
unavailable, because the panel has to tell them apart: a deployment with no
speakers at all should hide the music UI entirely, while one whose speakers are
merely asleep should keep it and show nothing playing.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import routes as routes_module
from app.api.routes import router
from app.music.heos import HeosController
from fake_heos import FakeHeos, FakePlayer


def make_client(controller=None, configured=True):
    monkey = pytest.MonkeyPatch()
    monkey.setattr(routes_module, "music_configured", lambda: configured)
    monkey.setattr(routes_module, "get_controller", lambda: controller)
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
        assert response.json() == {"connected": False, "players": []}
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
