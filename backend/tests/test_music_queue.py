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


def album(count=3, prefix="t"):
    return [
        Track(
            id=f"{prefix}{i}",
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


def build_with_stops(url_for=None):
    """As `build`, and also records which speakers were told to stop."""
    manager, played = build(url_for)
    stopped: list[int] = []

    async def stop_player(player_id):
        stopped.append(player_id)

    manager.stop_player = stop_player
    return manager, played, stopped


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
    assert 1 not in manager.queues


def test_pausing_does_not_advance_the_queue():
    """A pause is reported as `pause`, not `stop`, so this is really a guard
    against anyone later widening the condition to "not playing"."""
    manager, played = build()
    run(manager.start(1, album()))
    run(manager.on_state(1, "play"))
    run(manager.on_state(1, "pause"))
    assert played == [(1, "http://h/t1")]


def test_only_a_play_counts_as_the_track_having_started():
    """`awaiting_start` is cleared by `play` and by nothing else.

    A speaker reports `pause` and `unknown` too, and neither says the track we
    sent is the one it is on. Treating them as "started" would let the very
    next `stop` - which may still be the tail of the previous stream - advance
    the album a track early.
    """
    manager, played = build()
    run(manager.start(1, album()))
    run(manager.on_state(1, "pause"))
    run(manager.on_state(1, "stop"))
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
    assert 1 not in manager.queues


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
    assert 1 not in manager.queues


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


# -- concurrency -----------------------------------------------------------
#
# pyheos dispatches every pushed event as its own task, so a track ending and a
# finger on the panel genuinely do run at the same time. These are the tests
# for that; they are the reason `QueueManager` holds a per-speaker lock across
# the command it sends, and they all fail without it.


def gated():
    """A manager whose `play_url` can be held open for a chosen URL.

    The URL is recorded when the call *completes*, not when it starts, because
    what matters is the order the commands reach the speaker rather than the
    order they were issued in.
    """
    played: list[tuple[int, str]] = []
    holds: dict[str, asyncio.Event] = {}

    async def play_url(player_id, url):
        hold = holds.pop(url, None)
        if hold is not None:
            await hold.wait()
        played.append((player_id, url))

    manager = QueueManager(play_url=play_url, url_for=lambda t: f"http://h/{t.id}")
    return manager, played, holds


def test_a_new_album_is_not_overtaken_by_the_track_the_old_one_was_starting():
    """The out-of-order bug, in one test.

    A track ends and the queue sends the next one; before that command lands,
    somebody picks a different album. Unserialized, both commands are in flight
    at once and the speaker plays whichever arrives last - which is heard as a
    freshly chosen album starting on the previous album's next song.
    """

    async def main():
        manager, played, holds = gated()
        gate = asyncio.Event()
        holds["http://h/a2"] = gate

        await manager.start(1, album(3, prefix="a"))
        await manager.on_state(1, "play")

        ending = asyncio.create_task(manager.on_state(1, "stop"))
        await asyncio.sleep(0)
        picked = asyncio.create_task(manager.start(1, album(3, prefix="b")))
        await asyncio.sleep(0)

        gate.set()
        await asyncio.gather(ending, picked)
        return manager, played

    manager, played = run(main())
    assert played == [(1, "http://h/a1"), (1, "http://h/a2"), (1, "http://h/b1")]
    assert manager.snapshot(1)["track"] == {"id": "b1", "title": "Track 1"}


def test_an_event_about_the_old_album_cannot_advance_the_new_one():
    """The other half: the event arrives first and is applied second.

    A `stop` from the album being replaced must act on the queue that exists
    when its turn comes, not the one that existed when it was dispatched -
    otherwise picking a new album lands on its second track.
    """

    async def main():
        manager, played, holds = gated()
        gate = asyncio.Event()
        holds["http://h/b1"] = gate

        await manager.start(1, album(3, prefix="a"))
        await manager.on_state(1, "play")

        picked = asyncio.create_task(manager.start(1, album(3, prefix="b")))
        await asyncio.sleep(0)  # holds the lock, blocked sending b1
        stale = asyncio.create_task(manager.on_state(1, "stop"))
        await asyncio.sleep(0)

        gate.set()
        await asyncio.gather(picked, stale)
        return manager, played

    manager, played = run(main())
    assert played == [(1, "http://h/a1"), (1, "http://h/b1")]
    assert manager.snapshot(1)["track"] == {"id": "b1", "title": "Track 1"}


def test_stopping_waits_for_a_track_change_already_on_its_way():
    """`stop` is the serialized `clear`.

    Dropping the queue while the next track's command was still in flight
    would stop the music and then let that command start it again.
    """

    async def main():
        manager, played, holds = gated()
        gate = asyncio.Event()
        holds["http://h/t2"] = gate

        await manager.start(1, album(3))
        await manager.on_state(1, "play")

        ending = asyncio.create_task(manager.on_state(1, "stop"))
        await asyncio.sleep(0)
        stopping = asyncio.create_task(manager.stop(1))
        await asyncio.sleep(0)
        assert 1 in manager.queues  # not cleared until the send finishes

        gate.set()
        await asyncio.gather(ending, stopping)
        return manager, played

    manager, played = run(main())
    assert played == [(1, "http://h/t1"), (1, "http://h/t2")]
    assert 1 not in manager.queues


def test_two_speakers_do_not_wait_on_each_other():
    """One stalled command must not hold up the album in the next room."""

    async def main():
        manager, played, holds = gated()
        gate = asyncio.Event()
        holds["http://h/a1"] = gate

        stalled = asyncio.create_task(manager.start(1, album(2, prefix="a")))
        await asyncio.sleep(0)
        await manager.start(2, album(2, prefix="b"))
        assert played == [(2, "http://h/b1")]

        gate.set()
        await stalled
        return played

    assert run(main()) == [(2, "http://h/b1"), (1, "http://h/a1")]


class TestSkippingPastTheEnd:
    """Skipping forward off the end of an album has to end the music.

    The queue's own `remaining == 0` branch is reached two different ways and
    they need opposite behaviour, which is what made this easy to get wrong.
    When `on_state` reaches it the track has genuinely finished and the speaker
    has already stopped itself. When `next` reaches it the speaker is still
    playing the last track right now, and nothing else is going to stop it.
    """

    def test_the_speaker_is_told_to_stop(self):
        """Otherwise the music plays on while the panel's queue display
        vanishes and now-playing reverts to the speaker's own description of
        the stream - a bitrate where the song title goes."""
        manager, _, stopped = build_with_stops()
        run(manager.start(1, album(2)))
        run(manager.next(1))  # to the last track

        run(manager.next(1))  # off the end

        assert stopped == [1]

    def test_the_queue_is_cleared(self):
        manager, _, _ = build_with_stops()
        run(manager.start(1, album(2)))
        run(manager.next(1))

        run(manager.next(1))

        assert manager.current(1) is None

    def test_no_further_track_is_sent(self):
        manager, played, _ = build_with_stops()
        run(manager.start(1, album(2)))
        run(manager.next(1))
        before = len(played)

        run(manager.next(1))

        assert len(played) == before

    def test_skipping_within_the_album_stops_nothing(self):
        """The guard against fixing this by stopping on every skip."""
        manager, _, stopped = build_with_stops()
        run(manager.start(1, album(3)))

        run(manager.next(1))

        assert stopped == []

    def test_a_track_ending_naturally_does_not_send_a_stop(self):
        """The same branch, reached from the speaker rather than from a finger.
        Here it stopped by itself, and sending another one would be a command
        issued for no reason - and, worse, one that arrives after whatever
        somebody has started next."""
        manager, _, stopped = build_with_stops()
        run(manager.start(1, album(1)))
        run(manager.on_state(1, "play"))

        run(manager.on_state(1, "stop"))

        assert stopped == []
        assert manager.current(1) is None
