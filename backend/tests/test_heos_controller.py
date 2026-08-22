"""The HEOS controller: what it sends, and what it wakes the panel for.

The connection itself is faked (see fake_heos.py). What is worth testing here
is the layer HomeDash actually owns - the mapping from a panel action to a
speaker command, and the decision about which pushed events are worth an SSE
publish.
"""

import asyncio

import pytest

from app.music.heos import MusicUnavailable, HeosController
from fake_heos import FakeHeos, FakePlayer


def build(players=None, on_change=None):
    """A controller wired to a fake system, already 'connected'."""
    heos = FakeHeos(players)

    async def connect(host):
        return heos

    controller = HeosController("10.0.0.5", on_change=on_change, connect=connect)
    asyncio.run(controller._run())
    return controller, heos


def test_a_disconnected_controller_reports_no_players_rather_than_raising():
    """The panel asks for players on every reload, including during the window
    where the speakers are still asleep. Raising here would turn an ordinary
    cold start into an error on the wall."""
    controller = HeosController("10.0.0.5")
    assert controller.connected is False
    assert controller.players() == []


def test_commanding_a_disconnected_controller_is_an_error_not_a_silent_no_op():
    """The opposite of the case above: a *read* before connection is normal,
    but a play command that quietly does nothing would look like broken
    hardware rather than a backend that is still connecting."""
    controller = HeosController("10.0.0.5")
    with pytest.raises(MusicUnavailable):
        asyncio.run(controller.transport(1, "play"))


def test_a_player_snapshot_carries_what_the_panel_renders():
    controller, _ = build()
    (player,) = controller.players()

    assert player["id"] == 1
    assert player["name"] == "Kitchen"
    assert player["state"] == "stop"
    assert player["volume"] == 20
    assert player["now_playing"]["title"] == "Weightless"
    assert player["now_playing"]["artist"] == "Marconi Union"
    assert player["now_playing"]["duration_ms"] == 480000


def test_a_player_that_has_never_played_anything_reports_no_now_playing():
    """pyheos leaves now_playing_media unset on a speaker that has been idle
    since boot. Serializing that as an empty track would put a blank now-playing
    bar on the panel with nothing playing."""
    controller, _ = build([FakePlayer(now_playing_media=None)])
    (player,) = controller.players()
    assert player["now_playing"] is None


@pytest.mark.parametrize(
    "action,expected",
    [
        ("play", ("play",)),
        ("pause", ("pause",)),
        ("stop", ("stop",)),
        ("next", ("next",)),
        ("previous", ("previous",)),
    ],
)
def test_each_transport_action_reaches_the_speaker_as_its_own_command(action, expected):
    """Every one of these is a separate pyheos call, so a mis-wired branch
    would present as one button doing another button's job - which is the kind
    of thing nobody notices until it is on the wall."""
    controller, heos = build()
    asyncio.run(controller.transport(1, action))
    assert heos.players[1].calls == [expected]


def test_an_unknown_action_never_reaches_the_speaker():
    controller, heos = build()
    with pytest.raises(ValueError):
        asyncio.run(controller.transport(1, "eject"))
    assert heos.players[1].calls == []


def test_commands_for_an_unknown_player_raise_rather_than_hitting_another_one():
    """player_id comes off a URL the panel builds from a list that may be one
    reconnect out of date. Falling back to 'the first player' would start music
    in the wrong room."""
    controller, _ = build()
    for call in (
        lambda: controller.transport(99, "play"),
        lambda: controller.set_volume(99, 10),
        lambda: controller.play_url(99, "http://x/1"),
    ):
        with pytest.raises(KeyError):
            asyncio.run(call())


def test_setting_volume_forwards_the_level_unchanged():
    controller, heos = build()
    asyncio.run(controller.set_volume(1, 35))
    assert heos.players[1].calls == [("volume", 35)]


def test_a_state_change_wakes_the_panel():
    changes = []
    controller, _ = build(on_change=lambda: changes.append(1))
    asyncio.run(controller._handle_player_event(1, "event/player_state_changed"))
    assert changes == [1]


def test_progress_ticks_do_not_wake_the_panel():
    """HEOS emits a progress event every second for the playing speaker. Each
    one is a legitimate state update, but publishing them would put one SSE
    message per second per speaker onto a panel that only needs to know the
    track changed - and the position rides along on the next real update."""
    changes = []
    controller, _ = build(on_change=lambda: changes.append(1))
    asyncio.run(controller._handle_player_event(1, "event/player_now_playing_progress"))
    assert changes == []


def test_stopping_disconnects_the_underlying_connection():
    controller, heos = build()
    asyncio.run(controller.stop())
    assert heos.disconnected is True


def test_connecting_loads_the_player_list():
    """The bug this whole feature shipped with.

    `pyheos.Heos.players` is empty until `get_players()` is called - connecting
    does not fill it. The controller used to read `.players` straight after
    connecting, so against real speakers it reported none: the panel hid the
    music UI entirely while `homedash-heos-probe`, which calls `get_players`
    itself, cheerfully listed all three.
    """
    controller, heos = build([FakePlayer(player_id=1), FakePlayer(player_id=2)])

    assert heos.load_count == 1, "connecting must ask for the player list"
    assert [p["id"] for p in controller.players()] == [1, 2]
