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
"""

import logging
from dataclasses import dataclass, field

from app.music.base import Track

logger = logging.getLogger(__name__)


@dataclass
class PlayerQueue:
    tracks: list[Track]
    index: int = 0
    # True from the moment a track is sent until the speaker reports it is
    # doing anything other than stopped. See `on_state` - without it, the
    # speaker's own brief stop between streams reads as "track finished" and
    # the queue races through the whole album in a fraction of a second.
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

    Deliberately knows nothing about HEOS or Jellyfin: it is handed a callable
    that plays a URL, and a callable that turns a track into one. That is what
    keeps this - the part with the actual logic in it - testable without either.
    """

    play_url: object
    url_for: object
    queues: dict[int, PlayerQueue] = field(default_factory=dict)

    def has(self, player_id: int) -> bool:
        return player_id in self.queues

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
        if not tracks:
            raise ValueError("cannot start an empty queue")
        self.queues[player_id] = PlayerQueue(tracks=list(tracks))
        await self._play_current(player_id)

    def clear(self, player_id: int) -> None:
        """Forget the queue for one speaker.

        Called when the panel sends an explicit stop, which is what separates
        "this track ended" from "somebody stopped the music" - the speaker
        reports both as `stop`, and without this the queue would helpfully
        start the next track on somebody who had just asked for silence.
        """
        self.queues.pop(player_id, None)

    async def next(self, player_id: int) -> bool:
        """Skip forward. Returns False when this speaker has no HomeDash queue.

        Skipping has to go through here rather than through HEOS's own
        `play_next`: content sent as a URL never enters the speaker's queue, so
        its next-track command has nothing to move to and does nothing at all.
        """
        queue = self.queues.get(player_id)
        if queue is None:
            return False
        if queue.remaining == 0:
            self.clear(player_id)
            return True
        queue.index += 1
        await self._play_current(player_id)
        return True

    async def previous(self, player_id: int) -> bool:
        queue = self.queues.get(player_id)
        if queue is None:
            return False
        # Restarts the current track when there is nothing before it, which is
        # what every music player does and what a finger on a wall expects.
        queue.index = max(0, queue.index - 1)
        await self._play_current(player_id)
        return True

    async def on_state(self, player_id: int, state: str) -> None:
        """React to what the speaker says it is doing.

        The only signal available for "the track finished" is the speaker going
        to `stop`, so the gap between two tracks has to be told apart from the
        end of one. `awaiting_start` is what does it: it is set when a track is
        sent and cleared as soon as the speaker reports anything else, so the
        stop that arrives before playback begins is ignored and the one that
        arrives after it is not.

        A track that never starts therefore stalls the queue rather than
        advancing it. That is the right way round: a stall is one silent
        speaker, while the alternative races through an entire album in the
        time it takes to notice.
        """
        queue = self.queues.get(player_id)
        if queue is None:
            return
        if state != "stop":
            queue.awaiting_start = False
            return
        if queue.awaiting_start:
            return
        if queue.remaining == 0:
            self.clear(player_id)
            return
        queue.index += 1
        await self._play_current(player_id)

    async def _play_current(self, player_id: int) -> None:
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
