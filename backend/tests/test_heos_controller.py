"""The HEOS controller: what it sends, and what it wakes the panel for.

The connection itself is faked (see fake_heos.py). What is worth testing here
is the layer HomeDash actually owns - the mapping from a panel action to a
speaker command, and the decision about which pushed events are worth an SSE
publish.
"""

import asyncio

import pytest

from app.music import heos as heos_module
from app.music.heos import (
    _PROGRESS_EVENT,
    _STATE_EVENT,
    HeosController,
    HeosSpeaker,
    MusicUnavailable,
)
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


class TestTheSpeakersOwnQueue:
    """`play_url` appends to it, which is the fact this whole pair exists for.

    HEOS's `browse/play_stream` is not a fire-and-forget "play this URL": it
    puts the URL in the player's queue and plays that entry. Driving an album
    one track at a time therefore fills the speaker with dead HomeDash URLs
    unless something takes them out again, and nothing did.
    """

    def test_playing_a_url_lands_in_the_speakers_queue(self):
        """The behaviour the fake reproduces, asserted here so that a fake
        rewritten to be convenient cannot quietly take the bug back."""
        controller, heos = build()
        asyncio.run(controller.play_url(1, "http://homedash/api/music/s/abc"))
        assert [item.song for item in heos.players[1].queue] == [
            "http://homedash/api/music/s/abc"
        ]

    def test_pruning_keeps_only_the_entry_that_is_playing(self):
        controller, heos = build()
        for track in ("a", "b", "c"):
            asyncio.run(controller.play_url(1, f"http://homedash/api/music/s/{track}"))
        assert len(heos.players[1].queue) == 3

        asyncio.run(controller.prune_queue(1))

        assert [item.song for item in heos.players[1].queue] == [
            "http://homedash/api/music/s/c"
        ]

    def test_pruning_sends_nothing_when_there_is_nothing_stale(self):
        """It runs on every track of every album, so the common case must not
        be a command to the speaker for no reason."""
        controller, heos = build()
        asyncio.run(controller.play_url(1, "http://homedash/api/music/s/a"))
        heos.players[1].calls.clear()

        asyncio.run(controller.prune_queue(1))

        assert [call[0] for call in heos.players[1].calls] == ["get_queue"]

    def test_a_speaker_reporting_no_current_entry_is_left_alone(self):
        """The safe reading of "nothing is playing from the queue".

        The other one - that every entry is therefore stale - would delete the
        track the speaker is on, and a speaker on an input or a service with no
        queue of its own reports exactly this.
        """
        controller, heos = build()
        asyncio.run(controller.play_url(1, "http://homedash/api/music/s/a"))
        heos.players[1].now_playing_media.queue_id = None

        asyncio.run(controller.prune_queue(1))

        assert len(heos.players[1].queue) == 1

    def test_a_speaker_with_nothing_playing_at_all_is_left_alone(self):
        controller, heos = build([FakePlayer(now_playing_media=None)])
        asyncio.run(controller.prune_queue(1))
        assert heos.players[1].calls == []

    def test_clearing_empties_the_queue(self):
        controller, heos = build()
        asyncio.run(controller.play_url(1, "http://homedash/api/music/s/a"))

        asyncio.run(controller.clear_queue(1))

        assert heos.players[1].queue == []

    def test_clearing_an_empty_queue_raises_and_is_left_to_the_caller(self):
        """HEOS answers an error rather than a no-op, so somebody has to
        swallow it. `HeosSpeaker` does, because "the queue was already
        empty" must never fail a stop the user asked for - but the controller
        stays a thin mapping onto the protocol and reports what happened."""
        controller, _ = build()
        with pytest.raises(RuntimeError):
            asyncio.run(controller.clear_queue(1))


class TestHeosSpeaker:
    """The queue's adapter, and the one place tidying is allowed to fail.

    This policy used to live in two private functions in `music/service.py`
    that no test reached - only a hand-written copy of them in the test
    helpers did, and the copy had already drifted (it let a failed prune
    through).
    """

    def test_clearing_an_already_empty_queue_is_not_an_error(self):
        controller, heos = build()
        asyncio.run(HeosSpeaker(controller).clear_queue(1))
        assert heos.players[1].calls == [("clear_queue",)]

    def test_a_speaker_that_cannot_list_its_queue_is_not_an_error(self, monkeypatch):
        """Older firmware need not implement `get_queue` at all."""
        controller, heos = build()
        asyncio.run(controller.play_url(1, "http://homedash/api/music/s/a"))

        async def unsupported(*args, **kwargs):
            raise RuntimeError("command not supported")

        monkeypatch.setattr(heos.players[1], "get_queue", unsupported)
        before = list(heos.players[1].queue)

        asyncio.run(HeosSpeaker(controller).prune_queue(1))

        assert heos.players[1].queue == before

    def test_playing_and_stopping_are_not_best_effort(self):
        """Only the tidying is forgiven. A play or a stop that fails must
        reach whoever asked for it, or the button reports a success the
        speaker never heard."""
        controller, _ = build()
        speaker = HeosSpeaker(controller)
        with pytest.raises(KeyError):
            asyncio.run(speaker.play_url(99, "http://homedash/api/music/s/a"))
        with pytest.raises(KeyError):
            asyncio.run(speaker.stop(99))

    def test_stop_and_play_reach_the_speaker(self):
        controller, heos = build()
        speaker = HeosSpeaker(controller)
        asyncio.run(speaker.play_url(1, "http://homedash/api/music/s/a"))
        asyncio.run(speaker.stop(1))
        assert heos.players[1].calls == [
            ("play_url", "http://homedash/api/music/s/a"),
            ("stop",),
        ]



class TestPushedEvents:
    """What a speaker's event does. This is the wire the album hangs on: the
    queue only ever advances because a state change arrived through here."""

    def wired(self):
        log = []

        async def on_state(player_id, state):
            log.append(("queue", player_id, state))

        def on_change():
            log.append(("panel",))

        heos = FakeHeos()

        async def connect(host):
            return heos

        controller = HeosController(
            "10.0.0.5", on_change=on_change, on_state=on_state, connect=connect
        )
        asyncio.run(controller._run())
        return controller, heos, log

    def test_connecting_subscribes_to_the_speakers(self):
        _, heos, _ = self.wired()
        assert sum(len(targets) for targets in heos.dispatcher.handlers.values()) == 1

    def test_a_state_change_reaches_the_queue_and_then_the_panel(self):
        """In that order, or the panel flashes 'nothing playing' between tracks."""
        controller, heos, log = self.wired()
        heos.players[1].state = heos_module.PlayState.STOP

        asyncio.run(controller._handle_player_event(1, _STATE_EVENT))

        assert log == [("queue", 1, "stop"), ("panel",)]

    def test_other_events_wake_the_panel_but_leave_the_queue_alone(self):
        controller, _, log = self.wired()
        asyncio.run(controller._handle_player_event(1, "player_volume_changed"))
        assert log == [("panel",)]

    def test_progress_ticks_wake_nothing(self):
        controller, _, log = self.wired()
        asyncio.run(controller._handle_player_event(1, _PROGRESS_EVENT))
        assert log == []

    def test_a_state_change_for_an_unknown_speaker_skips_the_queue(self):
        controller, _, log = self.wired()
        asyncio.run(controller._handle_player_event(404, _STATE_EVENT))
        assert log == [("panel",)]

    def test_a_failing_queue_still_tells_the_panel(self, caplog):
        controller, heos, log = self.wired()

        async def broken(player_id, state):
            raise RuntimeError("boom")

        controller._on_state = broken
        asyncio.run(controller._handle_player_event(1, _STATE_EVENT))

        assert log == [("panel",)]
        assert "Queue failed" in caplog.text

    def test_with_no_listeners_an_event_is_harmless(self):
        controller, _ = build()
        asyncio.run(controller._handle_player_event(1, _STATE_EVENT))


class TestConnectionLifecycle:
    def test_it_keeps_trying_until_the_speakers_answer(self, monkeypatch):
        """They are usually asleep at boot."""
        monkeypatch.setattr(heos_module, "_INITIAL_RETRY_SECONDS", 0)
        heos = FakeHeos()
        attempts = []

        async def connect(host):
            attempts.append(host)
            if len(attempts) < 3:
                raise OSError("no route to host")
            return heos

        controller = HeosController("10.0.0.5", connect=connect)
        asyncio.run(controller._run())

        assert len(attempts) == 3
        assert controller.connected is True

    def test_it_waits_between_attempts(self, monkeypatch):
        waits = []

        async def sleep(seconds):
            waits.append(seconds)

        monkeypatch.setattr(heos_module.asyncio, "sleep", sleep)
        heos = FakeHeos()
        failures = [OSError("down")]

        async def connect(host):
            if failures:
                raise failures.pop()
            return heos

        asyncio.run(HeosController("10.0.0.5", connect=connect)._run())
        assert waits == [heos_module._INITIAL_RETRY_SECONDS]

    def test_stop_abandons_a_connection_still_being_attempted(self):
        async def scenario():
            never = asyncio.Event()

            async def connect(host):
                await never.wait()

            controller = HeosController("10.0.0.5", connect=connect)
            controller.start()
            await asyncio.sleep(0)
            await controller.stop()
            await asyncio.sleep(0)
            return controller._task.cancelled()

        assert asyncio.run(scenario()) is True

    def test_stop_unsubscribes_and_disconnects(self):
        controller, heos = build()
        asyncio.run(controller.stop())
        assert all(targets == [] for targets in heos.dispatcher.handlers.values())
        assert heos.disconnected is True

    def test_a_failed_disconnect_does_not_stop_the_shutdown(self, monkeypatch, caplog):
        controller, heos = build()

        async def fail():
            raise OSError("already gone")

        monkeypatch.setattr(heos, "disconnect", fail)
        asyncio.run(controller.stop())
        assert "Error disconnecting" in caplog.text

    def test_stopping_before_connecting_is_fine(self):
        asyncio.run(HeosController("10.0.0.5").stop())

    def test_the_real_connection_reconnects_and_limits_progress_events(self, monkeypatch):
        """`auto_reconnect` is the difference between a wifi blip and a music
        screen that silently never updates again."""
        seen = {}

        class RealHeos:
            @staticmethod
            async def create_and_connect(host, **kwargs):
                seen.update(host=host, **kwargs)
                return "connected"

        monkeypatch.setattr(heos_module, "Heos", RealHeos)
        assert asyncio.run(heos_module._connect_to_heos("10.0.0.5")) == "connected"
        assert seen == {"host": "10.0.0.5", "auto_reconnect": True, "all_progress_events": False}
