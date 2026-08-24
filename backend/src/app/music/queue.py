"""The play queue HomeDash has to own, and why it has to own it.

HEOS can queue, but only content from its own browse tree: `browse/add_to_queue`
takes a source and container id, not a URL. And it will not play an `.m3u` or a
`.pls`, so an album cannot be handed over as one playlist either. Between them
that leaves exactly one way to play a Jellyfin album on a HEOS speaker - send
one track, wait for it to finish, send the next - and the thing doing the
waiting has to be HomeDash.

**This is not gapless.** There is roughly a second between tracks while the
speaker finishes one stream and opens the next. That is inherent to driving it
this way and is not a bug to be fixed; the alternative is routing playback
through a DLNA server that HEOS browses natively, which buys a real queue at
the cost of mapping two id spaces.

State lives in this process, like the weather cache and the token store. A
restart therefore stops the music after the current track. Acceptable for a
wall panel, but it should be stated rather than discovered.

**The speaker has a queue of its own, and `play_url` writes to it.** That is
not what it looks like: `browse/play_stream` reads as "play this URL", but it
appends a queue entry and plays that entry. Sending one track at a time
therefore leaves one dead HomeDash URL in the speaker per track, and a finished
album used to leave the speaker unable to play anything from any source,
because whatever came next landed on top of them. So this module also tidies:
it prunes the speaker's queue to the entry playing whenever a track starts, and
empties it at every one of the three ends an album has - see `_end`.

**Everything that touches one speaker's queue is serialized** - see `_lock`.
That is not defensive tidiness: pyheos dispatches every pushed event as its own
task, so a track ending and a finger on a new album genuinely do run at the
same time, and the two `play_url` commands they each send can reach the speaker
in the order they were not issued in.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from app.music.base import Track

logger = logging.getLogger(__name__)


@dataclass
class PlayerQueue:
    tracks: list[Track]
    index: int = 0
    # True from the moment a track is sent until the speaker reports it is
    # playing. See `on_state` - without it, the speaker's own brief stop
    # between streams reads as "track finished" and the queue races through
    # the whole album in a fraction of a second.
    awaiting_start: bool = True

    @property
    def current(self) -> Track | None:
        if 0 <= self.index < len(self.tracks):
            return self.tracks[self.index]
        return None

    @property
    def remaining(self) -> int:
        return max(0, len(self.tracks) - self.index - 1)


@dataclass
class QueueManager:
    """One queue per speaker, and the rules for moving it along.

    Deliberately knows nothing about HEOS or Jellyfin: it is handed callables
    that drive a speaker and turn a track into a URL. That is what keeps this -
    the part with the actual logic in it - testable without either.

    Typed rather than left as `object`. They were called anyway, so the
    annotation was doing nothing but obliging a reader to go and find
    `music/service.py` to learn the signatures.
    """

    play_url: Callable[[int, str], Awaitable[None]]
    url_for: Callable[[Track], str]
    # Used to end an album, both when it is skipped past and when the panel
    # asks for silence. See `next` and `stop_and_release`.
    stop_player: Callable[[int], Awaitable[None]] | None = None
    # The speaker's *own* queue, which is a different thing from this one and
    # the reason a finished album used to leave the speaker unusable. See
    # `_end` and the `play` branch of `on_state`.
    clear_speaker_queue: Callable[[int], Awaitable[None]] | None = None
    prune_speaker_queue: Callable[[int], Awaitable[None]] | None = None
    queues: dict[int, PlayerQueue] = field(default_factory=dict)
    # One lock per speaker, created on demand. Two speakers must never wait on
    # each other: a stalled command to one would otherwise hold up the album
    # playing in the next room.
    locks: dict[int, asyncio.Lock] = field(default_factory=dict, repr=False)

    def _lock(self, player_id: int) -> asyncio.Lock:
        """The serialization point for one speaker.

        Held across the whole of a queue change *including the command sent to
        the speaker*, which is the part that matters. Picking a second album
        while a track is ending otherwise runs both at once: the ending track
        advances its queue and sends track N+1, the new album replaces the
        queue and sends its own track 1, and whichever command wins the race
        inside pyheos is what actually plays. That is heard as an album
        starting on someone else's song.

        `asyncio.Lock` hands the lock out in the order it was asked for, so
        this also restores the ordering of the pushed events themselves -
        pyheos runs each one as its own task, and without a queue in front of
        them a `stop` and the `play` that followed it can be applied backwards.
        """
        lock = self.locks.get(player_id)
        if lock is None:
            lock = self.locks[player_id] = asyncio.Lock()
        return lock

    def current(self, player_id: int) -> Track | None:
        """The track this speaker was actually sent, or None.

        Read without the lock on purpose - it is one dict lookup answering a
        GET, and blocking a panel refresh behind a track change to hand it
        metadata that is a few milliseconds newer would be a poor trade.

        This exists because the speaker cannot answer it. A HEOS speaker given
        a bare URL has no metadata to report and describes the stream instead,
        so the panel would show a bitrate where the song title goes. HomeDash
        sent the track, so HomeDash is the one that knows what it is.
        """
        queue = self.queues.get(player_id)
        return queue.current if queue is not None else None

    def snapshot(self, player_id: int) -> dict | None:
        """What the panel shows about the queue, or None when there isn't one."""
        queue = self.queues.get(player_id)
        if queue is None:
            return None
        current = queue.current
        return {
            "position": queue.index + 1,
            "length": len(queue.tracks),
            "remaining": queue.remaining,
            "track": {"id": current.id, "title": current.title} if current else None,
        }

    async def start(self, player_id: int, tracks: list[Track]) -> None:
        """Replace whatever this speaker was doing with a new queue.

        Replace, not append: picking an album on the panel means "play this
        now and forget the rest", which is what every other music player does
        with a tapped album.
        """
        if not tracks:
            raise ValueError("cannot start an empty queue")
        async with self._lock(player_id):
            self.queues[player_id] = PlayerQueue(tracks=list(tracks))
            await self._play_current(player_id)

    def clear(self, player_id: int) -> None:
        """Forget the queue for one speaker.

        Called when the panel sends an explicit stop, which is what separates
        "this track ended" from "somebody stopped the music" - the speaker
        reports both as `stop`, and without this the queue would helpfully
        start the next track on somebody who had just asked for silence.

        Synchronous, and therefore *not* serialized: it is safe to call while
        holding the lock, which the queue's own paths do. A caller outside
        this class wants `stop_and_release`, which also hands the speaker back.
        """
        self.queues.pop(player_id, None)

    async def stop_and_release(self, player_id: int) -> bool:
        """Stop this speaker and hand it back. False if it has no queue here.

        The transport route's `stop`, and it does the whole of it now: pressing
        stop has to leave the speaker as usable as it was before HomeDash
        touched it, which means emptying the queue `play_url` filled - see
        `_end`. False falls the route through to HEOS's own stop, which is
        right for a speaker playing from one of its own sources.

        Serialized, and that is not incidental. Clearing without the lock would
        drop the queue while the previous track's successor was still on its
        way to the speaker, so the music would stop and then start again on a
        track nobody asked for. Within the lock the local queue goes first and
        the command second, for the reason that used to be written in the
        route: the speaker reports a deliberate stop and a finished track
        identically, so the queue has to be gone before that event arrives.
        """
        async with self._lock(player_id):
            if self.queues.get(player_id) is None:
                return False
            await self._end(player_id, stop_speaker=True)
            return self.stop_player is not None

    async def _end(self, player_id: int, *, stop_speaker: bool) -> None:
        """End a queue and give the speaker back. Called with the lock held.

        The one place an album stops, reached three ways: the last track
        finishing, a skip off the end, and the panel's stop button. They differ
        only in whether the speaker still needs telling - it has already
        stopped itself in the first case - and they used to differ in far more
        than that, which is how the cleanup came to be missing from two of them.

        **Clearing the speaker's own queue is the point.** `play_url` appends
        to it, so an album leaves one entry per track behind, each pointing at
        a HomeDash stream URL that has already been served. Left there they are
        what the speaker resumes into: the HEOS app, another source, or
        HomeDash's own next album all start on top of a list of dead URLs, and
        the speaker behaves as though it has a mind of its own.
        """
        self.clear(player_id)
        if stop_speaker and self.stop_player is not None:
            await self.stop_player(player_id)
        if self.clear_speaker_queue is not None:
            await self.clear_speaker_queue(player_id)

    async def next(self, player_id: int) -> bool:
        """Skip forward. Returns False when this speaker has no HomeDash queue.

        Skipping has to go through here rather than through HEOS's own
        `play_next`, which has nothing to move to and does nothing at all: the
        entry `play_url` appends is always the last one in the speaker's queue.
        (This module used to say the URL never entered that queue at all. It
        does - see the note at the top - and believing otherwise is what left
        finished albums sitting in the speaker.)
        """
        async with self._lock(player_id):
            queue = self.queues.get(player_id)
            if queue is None:
                return False
            if queue.remaining == 0:
                # Skipping past the last track ends the album, and the speaker
                # has to be told: the last track is still playing right now.
                # Dropping the queue alone left the music running while
                # the panel's queue display vanished and now-playing reverted
                # to the speaker's own bitrate-and-codec description of the
                # stream - a stop button's job done by the skip button, badly.
                #
                # `on_state` hits this same branch and must *not* stop
                # anything, because there the track has genuinely ended and the
                # speaker stopped by itself.
                await self._end(player_id, stop_speaker=True)
                return True
            queue.index += 1
            await self._play_current(player_id)
            return True

    async def previous(self, player_id: int) -> bool:
        async with self._lock(player_id):
            queue = self.queues.get(player_id)
            if queue is None:
                return False
            # Restarts the current track when there is nothing before it, which
            # is what every music player does and what a finger on a wall
            # expects.
            queue.index = max(0, queue.index - 1)
            await self._play_current(player_id)
            return True

    async def on_state(self, player_id: int, state: str) -> None:
        """React to what the speaker says it is doing.

        The only signal available for "the track finished" is the speaker going
        to `stop`, so the gap between two tracks has to be told apart from the
        end of one. `awaiting_start` is what does it: it is set when a track is
        sent and cleared once the speaker reports it is *playing*, so the stop
        that arrives before playback begins is ignored and the one that arrives
        after it is not.

        A track that never starts therefore stalls the queue rather than
        advancing it. That is the right way round: a stall is one silent
        speaker, while the alternative races through an entire album in the
        time it takes to notice.

        The queue is read after the lock, never before. An event about the
        album that was playing a moment ago can be waiting here while somebody
        picks a new one, and it must act on the queue that exists when its turn
        comes rather than the one that did when it arrived.
        """
        async with self._lock(player_id):
            queue = self.queues.get(player_id)
            if queue is None:
                return
            if state == "play":
                queue.awaiting_start = False
                # The earliest moment the speaker can say which of its own
                # queue entries it is on, and therefore the earliest moment the
                # rest of them can safely go. Doing it here rather than after
                # `play_url` is what keeps the speaker's queue at one entry for
                # the length of an album instead of one per track - and it
                # sweeps up whatever an older HomeDash left behind, so a
                # confused speaker fixes itself on the next thing it plays.
                #
                # After `awaiting_start`, never before: a prune that fails must
                # not leave the queue unable to tell a finished track from the
                # gap before one, which would stall the album for good.
                if self.prune_speaker_queue is not None:
                    await self.prune_speaker_queue(player_id)
                return
            # Only `play` counts as having started. A `pause` or an `unknown`
            # arriving before the stream opens says nothing about whether the
            # track we sent is the one the speaker is on.
            if state != "stop":
                return
            if queue.awaiting_start:
                return
            if queue.remaining == 0:
                # The album is over and the speaker stopped itself, so there is
                # nothing to send it - but its queue still holds the entry
                # `play_url` put there, and this is the moment it goes.
                await self._end(player_id, stop_speaker=False)
                return
            queue.index += 1
            await self._play_current(player_id)

    async def _play_current(self, player_id: int) -> None:
        """Send the queue's current track. Always called with the lock held."""
        queue = self.queues[player_id]
        track = queue.current
        if track is None:
            self.clear(player_id)
            return
        try:
            url = self.url_for(track)
        except Exception:
            # A URL that cannot be built is not going to become buildable on
            # the next track, so drop the queue rather than walking the whole
            # album logging the same failure once per track.
            logger.exception("Could not build a stream URL; abandoning the queue")
            self.clear(player_id)
            return
        queue.awaiting_start = True
        await self.play_url(player_id, url)
