"""The play queue, which is the part of this feature with actual logic in it.

HEOS cannot be handed an album, so HomeDash sends one track and waits for the
speaker to report it finished. The only signal for "finished" is the speaker
going to `stop` - which is also what it reports between two tracks, and what it
reports when somebody stops the music. Nearly every test here is about telling
those three apart.
"""

import asyncio

import pytest

from app.music.base import Track
from app.music.queue import QueueManager


def album(count=3):
    return [
        Track(
            id=f"t{i}",
            title=f"Track {i}",
            artist="Artist",
            album="Album",
            duration_ms=180000,
            track_number=i,
        )
        for i in range(1, count + 1)
    ]


def build(url_for=None):
    """A queue manager that records the URLs it sent, per player."""
    played: list[tuple[int, str]] = []

    async def play_url(player_id, url):
        played.append((player_id, url))

    manager = QueueManager(play_url=play_url, url_for=url_for or (lambda t: f"http://h/{t.id}"))
    return manager, played


def run(coro):
    return asyncio.run(coro)


def test_starting_a_queue_plays_the_first_track_immediately():
    manager, played = build()
    run(manager.start(1, album()))
    assert played == [(1, "http://h/t1")]


def test_an_empty_queue_is_rejected_rather_than_starting_silence():
    manager, played = build()
    with pytest.raises(ValueError):
        run(manager.start(1, []))
    assert played == []


def test_the_stop_before_a_track_starts_is_not_mistaken_for_it_finishing():
    """The single most important behaviour here.

    A speaker handed a URL reports `stop` briefly before it opens the stream.
    Reading that as "the track finished" would advance the queue, which would
    produce another `stop`, and the whole album would be consumed in a fraction
    of a second with only the last track ever audible.
    """
    manager, played = build()
    run(manager.start(1, album()))
    run(manager.on_state(1, "stop"))
    assert played == [(1, "http://h/t1")]


def test_a_stop_after_playback_began_advances_to_the_next_track():
    manager, played = build()
    run(manager.start(1, album()))
    run(manager.on_state(1, "play"))
    run(manager.on_state(1, "stop"))
    assert played == [(1, "http://h/t1"), (1, "http://h/t2")]


def test_an_album_plays_all_the_way_through_in_order():
    manager, played = build()
    run(manager.start(1, album(3)))
    for _ in range(2):
        run(manager.on_state(1, "play"))
        run(manager.on_state(1, "stop"))
    assert [url for _, url in played] == ["http://h/t1", "http://h/t2", "http://h/t3"]


def test_the_queue_ends_after_the_last_track_rather_than_repeating_it():
    manager, played = build()
    run(manager.start(1, album(2)))
    for _ in range(2):
        run(manager.on_state(1, "play"))
        run(manager.on_state(1, "stop"))
    assert [url for _, url in played] == ["http://h/t1", "http://h/t2"]
    assert manager.has(1) is False


def test_pausing_does_not_advance_the_queue():
    """A pause is reported as `pause`, not `stop`, so this is really a guard
    against anyone later widening the condition to "not playing"."""
    manager, played = build()
    run(manager.start(1, album()))
    run(manager.on_state(1, "play"))
    run(manager.on_state(1, "pause"))
    assert played == [(1, "http://h/t1")]


def test_a_cleared_queue_does_not_start_the_next_track():
    """Somebody pressing stop and a track ending look identical to the speaker.
    Clearing the queue first is what separates them, and without it stopping
    the music would start the next track instead."""
    manager, played = build()
    run(manager.start(1, album()))
    run(manager.on_state(1, "play"))
    manager.clear(1)
    run(manager.on_state(1, "stop"))
    assert played == [(1, "http://h/t1")]


def test_skipping_forward_plays_the_next_track():
    manager, played = build()
    run(manager.start(1, album()))
    assert run(manager.next(1)) is True
    assert [url for _, url in played] == ["http://h/t1", "http://h/t2"]


def test_skipping_past_the_last_track_ends_the_queue():
    manager, played = build()
    run(manager.start(1, album(1)))
    assert run(manager.next(1)) is True
    assert manager.has(1) is False


def test_skipping_back_restarts_the_first_track_rather_than_underflowing():
    """What every music player does, and what a finger on a wall expects."""
    manager, played = build()
    run(manager.start(1, album()))
    assert run(manager.previous(1)) is True
    assert [url for _, url in played] == ["http://h/t1", "http://h/t1"]


def test_skipping_reports_unhandled_when_this_speaker_has_no_queue():
    """The route falls through to HEOS's own skip in that case, which is right
    for a speaker playing from one of its own sources."""
    manager, _ = build()
    assert run(manager.next(2)) is False
    assert run(manager.previous(2)) is False


def test_two_speakers_keep_separate_queues():
    """Otherwise starting an album in the kitchen would hijack whatever the
    living room was already playing."""
    manager, played = build()
    run(manager.start(1, album(2)))
    run(manager.start(2, album(2)))
    run(manager.on_state(1, "play"))
    run(manager.on_state(1, "stop"))
    assert played == [
        (1, "http://h/t1"),
        (2, "http://h/t1"),
        (1, "http://h/t2"),
    ]


def test_a_url_that_cannot_be_built_abandons_the_queue_rather_than_looping():
    """A URL failure is configuration, not bad luck with one track, so it will
    fail identically for every remaining track. Walking the album to log the
    same error once per track helps nobody."""

    def explode(track):
        raise RuntimeError("no base URL configured")

    manager, played = build(url_for=explode)
    run(manager.start(1, album()))
    assert played == []
    assert manager.has(1) is False


def test_the_snapshot_reports_position_within_the_album():
    manager, _ = build()
    run(manager.start(1, album(3)))
    assert manager.snapshot(1) == {
        "position": 1,
        "length": 3,
        "remaining": 2,
        "track": {"id": "t1", "title": "Track 1"},
    }
    assert manager.snapshot(2) is None
