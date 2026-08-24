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
        stopping = asyncio.create_task(manager.stop_and_release(1))
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


class TestGivingTheSpeakerBack:
    """The speaker keeps a queue of its own, and `play_url` writes to it.

    This is the thing the feature shipped believing the opposite of. HEOS's
    `browse/play_stream` does not merely play a URL - it appends it to the
    player's queue and plays that entry, so an album leaves one entry per track
    behind, each pointing at a HomeDash stream that has already been served and
    a token that will eventually stop resolving. Nothing took them out again.

    Heard in the kitchen: an album finishes, and from then on the speaker will
    not play - not from the panel, not from the HEOS app, not from anything -
    because whatever it is asked for lands on top of a stack of dead URLs.

    `play_next` doing nothing is the same fact seen from the other side, and it
    is what made the wrong conclusion look confirmed: the entry HomeDash just
    appended is always the *last* one, so there is never anything after it to
    skip to.
    """

    def build_speaker(self):
        """A manager wired to a recorder that keeps every call in order.

        One list rather than a list per verb, because most of what is worth
        asserting here is ordering - a queue cleared before the stop it was
        meant to follow is a different bug from one that is never cleared.
        """
        events: list[tuple] = []

        async def play_url(player_id, url):
            events.append(("play_url", url))

        async def stop_player(player_id):
            events.append(("stop_speaker",))

        async def clear_speaker_queue(player_id):
            events.append(("clear_speaker_queue",))

        async def prune_speaker_queue(player_id):
            events.append(("prune_speaker_queue",))

        manager = QueueManager(
            play_url=play_url,
            url_for=lambda t: f"http://h/{t.id}",
            stop_player=stop_player,
            clear_speaker_queue=clear_speaker_queue,
            prune_speaker_queue=prune_speaker_queue,
        )
        return manager, events

    def test_an_album_that_finishes_empties_the_speakers_queue(self):
        """The reported bug, in one test. The speaker has stopped itself, so
        there is nothing to send it - but the entry it stopped on is still in
        its queue, and leaving it there is what makes the speaker unusable."""
        manager, events = self.build_speaker()
        run(manager.start(1, album(1)))
        run(manager.on_state(1, "play"))

        run(manager.on_state(1, "stop"))

        assert ("clear_speaker_queue",) in events
        assert 1 not in manager.queues

    def test_a_finished_album_is_not_also_sent_a_stop(self):
        """It stopped by itself. A command issued now would arrive after
        whatever somebody has started next."""
        manager, events = self.build_speaker()
        run(manager.start(1, album(1)))
        run(manager.on_state(1, "play"))

        run(manager.on_state(1, "stop"))

        assert ("stop_speaker",) not in events

    def test_skipping_off_the_end_stops_the_speaker_and_then_empties_its_queue(self):
        """Both, and in that order: here the last track is still playing, so
        the music has to be stopped before the queue it is playing from goes."""
        manager, events = self.build_speaker()
        run(manager.start(1, album(1)))

        run(manager.next(1))

        assert events[-2:] == [("stop_speaker",), ("clear_speaker_queue",)]

    def test_the_panels_stop_button_empties_the_speakers_queue_too(self):
        """The case the question was actually about: after stopping the music
        from the wall, the speakers have to be usable from anything else."""
        manager, events = self.build_speaker()
        run(manager.start(1, album(3)))
        run(manager.on_state(1, "play"))

        assert run(manager.stop_and_release(1)) is True

        assert events[-2:] == [("stop_speaker",), ("clear_speaker_queue",)]
        assert 1 not in manager.queues

    def test_stopping_forgets_the_queue_before_telling_the_speaker(self):
        """The ordering that used to live in the transport route.

        A speaker reports a deliberate stop and a finished track identically,
        so the queue has to be gone before the command that produces that event
        goes out - otherwise stopping the music starts the next track.
        """
        manager, events = self.build_speaker()
        run(manager.start(1, album(3)))
        run(manager.on_state(1, "play"))
        events.clear()

        run(manager.stop_and_release(1))
        run(manager.on_state(1, "stop"))

        assert events == [("stop_speaker",), ("clear_speaker_queue",)]

    def test_stopping_a_speaker_with_no_queue_here_is_not_handled(self):
        """It falls the route through to HEOS's own stop, which is right for a
        speaker playing from one of its own sources - and means HomeDash never
        empties a queue it did not fill."""
        manager, events = self.build_speaker()
        assert run(manager.stop_and_release(2)) is False
        assert events == []

    def test_a_track_starting_prunes_the_speakers_queue_to_the_one_playing(self):
        """What keeps an album from leaving twelve entries behind.

        On `play` and not on `play_url`, because until the speaker says it is
        playing there is no way to know which entry to keep - and pruning to
        the wrong one deletes the track that is about to start.
        """
        manager, events = self.build_speaker()
        run(manager.start(1, album(3)))
        assert ("prune_speaker_queue",) not in events

        run(manager.on_state(1, "play"))

        assert events == [("play_url", "http://h/t1"), ("prune_speaker_queue",)]

    def test_nothing_is_pruned_before_the_speaker_says_it_is_playing(self):
        """A `pause` or a `stop` says nothing about which entry it is on."""
        manager, events = self.build_speaker()
        run(manager.start(1, album(3)))

        run(manager.on_state(1, "pause"))
        run(manager.on_state(1, "stop"))

        assert ("prune_speaker_queue",) not in events

    def test_a_prune_that_fails_does_not_stall_the_album(self):
        """`awaiting_start` is cleared first, on purpose.

        Tidying the speaker's queue is best-effort - old firmware need not
        implement `get_queue` at all - and the failure it must never cause is
        the queue losing its ability to tell a finished track from the gap
        before one, which stops the music for good.
        """

        async def explode(player_id):
            raise RuntimeError("get_queue not supported")

        manager, events = self.build_speaker()
        manager.prune_speaker_queue = explode
        run(manager.start(1, album(3)))

        with pytest.raises(RuntimeError):
            run(manager.on_state(1, "play"))
        run(manager.on_state(1, "stop"))

        assert [url for verb, url in events if verb == "play_url"] == [
            "http://h/t1",
            "http://h/t2",
        ]

    def test_a_manager_with_no_speaker_callables_still_works(self):
        """The transport-only wiring, and every test above this class. Tidying
        the speaker's queue is something HomeDash gained, not something the
        queue's own logic depends on."""
        manager, played = build()
        run(manager.start(1, album(1)))
        run(manager.on_state(1, "play"))
        run(manager.on_state(1, "stop"))
        assert 1 not in manager.queues
        assert run(manager.stop_and_release(1)) is False
