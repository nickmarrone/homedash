"""Which source answers for a speaker - tested at Playback's own interface.

These used to go through the routes with five getters monkeypatched, because
the rules lived in the route handlers. They live in `Playback` now, so they are
tested against it directly: a FakeHeos speaker, a fake library, a recording
queue, and no web app. The route tests are left with turning these answers
into statuses.
"""

import pytest
from pyheos import CommandFailedError, ConnectionState

from app.music.jellyfin import JellyfinError
from app.music.playback import (
    LibraryFailed,
    NothingToPlay,
    Playback,
    SpeakerRefused,
    Unavailable,
    UnknownPlayer,
)
from music_fakes import (
    FakeLibrary,
    connected_controller,
    queue_manager,
    run_,
    unconnected_controller,
)


def with_library(controller, library=None, queues=None):
    if queues is None:
        queues, _ = queue_manager()
    return Playback(controller, library or FakeLibrary(), queues)


# -- what the panel is told ----------------------------------------------------


def test_while_still_connecting_there_are_simply_no_speakers_yet():
    assert Playback(unconnected_controller()).players() == {
        "connected": False,
        "library": False,
        "players": [],
    }


def test_the_queue_the_speaker_cannot_report_rides_along():
    controller, _ = connected_controller()
    playback = with_library(controller)

    run_(playback.play(1, "b1"))

    body = playback.players()
    assert body["library"] is True
    assert body["players"][0]["queue"] == {
        "position": 1,
        "length": 2,
        "remaining": 1,
        "track": {"id": "t1", "title": "One"},
    }


def test_a_homedash_queue_answers_for_the_speaker_about_what_is_playing():
    """A HEOS speaker handed a bare URL has no metadata for it and describes
    the stream instead, so the panel showed a bitrate where the song title
    goes. HomeDash sent the track, so HomeDash says what it is."""
    controller, heos = connected_controller()
    heos.players[1].now_playing_media.song = "160kbps MP3"
    heos.players[1].now_playing_media.artist = None
    heos.players[1].now_playing_media.album = None
    heos.players[1].now_playing_media.image_url = "http://speaker/station.jpg"
    heos.players[1].now_playing_media.duration = 0
    playback = with_library(controller)

    run_(playback.play(1, "b1"))

    playing = playback.players()["players"][0]["now_playing"]
    assert playing["title"] == "One"
    assert playing["artist"] == "A"
    assert playing["album"] == "B"
    # Proxied through HomeDash, so the Jellyfin API key stays server-side.
    assert playing["image_url"] == "/api/music/art/b1"
    # The speaker reports 0 for a stream it did not choose; Jellyfin knows.
    assert playing["duration_ms"] == 1000
    # ...but the speaker is still the only one who knows how far in it is.
    assert playing["position_ms"] == 12000


def test_a_speaker_playing_its_own_source_keeps_its_own_metadata():
    """The override applies only where HomeDash owns the queue. A speaker
    playing Spotify reports perfectly good metadata, and replacing it would be
    a regression on the transport-only setup."""
    controller, _ = connected_controller()

    for playback in (Playback(controller), with_library(controller)):
        player = playback.players()["players"][0]
        assert player["now_playing"]["title"] == "Weightless"
        assert player["now_playing"]["image_url"] == "http://speaker/art.jpg"
        assert player["queue"] is None


# -- skip and stop: who carries them out -------------------------------------


def test_skipping_moves_homedash_queue_rather_than_the_speakers_own():
    """`play_next` on a URL the speaker was handed has nothing after it to
    move to. If it were sent anyway the skip button would do nothing at all."""
    controller, heos = connected_controller()
    queues, played = queue_manager()
    playback = with_library(controller, queues=queues)
    run_(playback.play(1, "b1"))

    run_(playback.transport(1, "next"))

    assert played[-1] == (1, "http://h/t2")
    assert heos.players[1].calls == []


@pytest.mark.parametrize("action", ["next", "previous"])
def test_skipping_falls_through_to_the_speaker_when_there_is_no_queue(action):
    """A speaker playing from one of its own sources still has a working skip
    button, which is the whole reason the fall-through exists."""
    controller, heos = connected_controller()

    run_(with_library(controller).transport(1, action))

    assert heos.players[1].calls == [(action,)]


def test_stopping_clears_the_queue_so_it_does_not_resume_by_itself():
    """The speaker reports a finished track and a deliberate stop identically.
    Without the clear, pressing stop would start the next track."""
    controller, _ = connected_controller()
    queues, played = queue_manager()
    playback = with_library(controller, queues=queues)
    run_(playback.play(1, "b1"))

    run_(playback.transport(1, "stop"))
    run_(queues.on_state(1, "stop"))

    assert 1 not in queues.queues
    assert played == [(1, "http://h/t1")]


@pytest.mark.parametrize("action", ["play", "pause", "stop"])
def test_without_a_library_every_command_goes_straight_to_the_speaker(action):
    controller, heos = connected_controller()

    run_(Playback(controller).transport(1, action))

    assert heos.players[1].calls == [(action,)]


# -- play --------------------------------------------------------------------


def test_playing_an_album_queues_every_track_and_starts_the_first():
    controller, _ = connected_controller()
    queues, played = queue_manager()

    queued = run_(with_library(controller, queues=queues).play(1, "b1"))

    assert queued == 2
    assert played == [(1, "http://h/t1")]


def test_playing_a_subset_of_an_album_keeps_only_the_requested_tracks():
    controller, _ = connected_controller()
    queues, played = queue_manager()

    queued = run_(with_library(controller, queues=queues).play(1, "b1", ["t2", "gone"]))

    assert queued == 1
    assert played == [(1, "http://h/t2")]


def test_playing_on_an_unknown_player_fails_before_the_library_is_asked():
    controller, _ = connected_controller()
    queues, played = queue_manager()
    library = FakeLibrary(error=AssertionError("the library should not be asked"))

    with pytest.raises(UnknownPlayer):
        run_(with_library(controller, library=library, queues=queues).play(99, "b1"))
    assert played == []


def test_an_album_with_no_tracks_is_refused_rather_than_a_silent_success():
    controller, _ = connected_controller()

    with pytest.raises(NothingToPlay):
        run_(with_library(controller, library=FakeLibrary(tracks=[])).play(1, "b1"))


def test_a_jellyfin_failure_while_starting_is_reported_as_the_librarys():
    controller, _ = connected_controller()
    library = FakeLibrary(error=JellyfinError("boom"))

    with pytest.raises(LibraryFailed):
        run_(with_library(controller, library=library).play(1, "b1"))


def test_playing_without_a_library_is_unavailable_not_an_error():
    controller, _ = connected_controller()

    with pytest.raises(Unavailable):
        run_(Playback(controller).play(1, "b1"))


def test_a_library_without_a_queue_is_no_library_at_all():
    """They come as a pair. Half of one must not leave `library: true` on the
    panel with a play button that cannot work."""
    controller, _ = connected_controller()
    playback = Playback(controller, FakeLibrary(), None)

    assert playback.library is None
    assert playback.players()["library"] is False


# -- failures, sorted --------------------------------------------------------


def test_a_command_before_the_connection_is_up_is_refused():
    """Reads degrade to 'nothing yet'; writes must not, or a tap on the wall
    would report success while the speaker never heard it."""
    playback = Playback(unconnected_controller())
    for command in (playback.transport(1, "play"), playback.set_volume(1, 10)):
        with pytest.raises(Unavailable):
            run_(command)


def test_a_dropped_connection_refuses_commands_too():
    controller, heos = connected_controller()
    heos.connection_state = ConnectionState.DISCONNECTED

    with pytest.raises(Unavailable):
        run_(Playback(controller).transport(1, "play"))


def test_an_unknown_speaker_is_unknown_whoever_would_have_handled_it():
    """Falls through an empty HomeDash queue to the speaker, which is where
    the missing id is noticed."""
    controller, _ = connected_controller()

    for playback in (Playback(controller), with_library(controller)):
        with pytest.raises(UnknownPlayer):
            run_(playback.transport(99, "next"))
        with pytest.raises(UnknownPlayer):
            run_(playback.set_volume(99, 10))


def test_a_refused_command_is_the_speakers_fault(monkeypatch):
    controller, _ = connected_controller()

    async def refuse(player_id, action):
        raise CommandFailedError("player/set_play_state", "boom", 1, 2)

    monkeypatch.setattr(controller, "transport", refuse)

    with pytest.raises(SpeakerRefused):
        run_(Playback(controller).transport(1, "play"))


# -- the speaker is handed back usable ---------------------------------------


class TestTheSpeakerIsLeftUsable:
    """With the speaker's own queue in play.

    A recording-only queue is right for most of the above, and is exactly why
    this bug once survived: the speaker's queue was not in the picture. `play_url`
    appends to it, so an album leaves one entry per track behind and the
    speaker will not play anything else until they are gone.
    """

    def test_an_album_played_to_the_end_leaves_nothing_behind(self):
        controller, heos = connected_controller()
        queues, _ = queue_manager(controller)
        run_(with_library(controller, queues=queues).play(1, "b1"))

        for _ in range(2):  # FakeLibrary's album is two tracks
            run_(queues.on_state(1, "play"))
            run_(queues.on_state(1, "stop"))

        assert heos.players[1].queue == []
        assert 1 not in queues.queues

    def test_the_speakers_queue_never_grows_past_the_track_playing(self):
        controller, heos = connected_controller()
        queues, _ = queue_manager(controller)
        run_(with_library(controller, queues=queues).play(1, "b1"))

        run_(queues.on_state(1, "play"))
        run_(queues.on_state(1, "stop"))  # on to track two
        run_(queues.on_state(1, "play"))

        assert len(heos.players[1].queue) == 1

    def test_stopping_empties_the_speakers_queue(self):
        controller, heos = connected_controller()
        queues, _ = queue_manager(controller)
        playback = with_library(controller, queues=queues)
        run_(playback.play(1, "b1"))
        run_(queues.on_state(1, "play"))

        run_(playback.transport(1, "stop"))

        assert heos.players[1].queue == []
        assert ("stop",) in heos.players[1].calls

    def test_a_speaker_left_confused_by_an_older_build_fixes_itself(self):
        """A speaker already holding a stack of dead URLs is cleaned out by
        the next thing HomeDash plays on it."""
        controller, heos = connected_controller()
        for stale in range(5):
            run_(controller.play_url(1, f"http://old/api/music/s/{stale}"))
        queues, _ = queue_manager(controller)
        run_(with_library(controller, queues=queues).play(1, "b1"))
        run_(queues.on_state(1, "play"))

        assert [item.song for item in heos.players[1].queue] == ["http://h/t1"]
