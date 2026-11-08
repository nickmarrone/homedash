"""Who answers for a speaker, and in what order.

A speaker can be driven by more than one thing at once. HEOS knows what it is
playing from its own sources; HomeDash knows what it sent from Jellyfin, which
the speaker cannot describe and cannot skip through. Every music request has to
decide which of the two is the authority for it, and that decision used to be
written out separately in each route handler. It lives here now, so the next
source - Audiobookshelf sessions, then Apple Music - is one more branch in one
module rather than one more branch in every handler.

The rules, in one place:

- **What is playing.** The speaker reports it, unless HomeDash owns a queue on
  that speaker, in which case HomeDash names the track and the speaker supplies
  only the position. See `serialize_now_playing` for why.
- **Skip.** HomeDash's queue moves when there is one; otherwise the speaker's
  own `play_next` / `play_previous`, so a speaker playing Spotify still skips.
- **Stop.** HomeDash's queue stops and hands the speaker back clean when there
  is one; otherwise a plain stop.
- **Play, pause, volume.** Always the speaker.

Failures come out as the exceptions below rather than as HTTP statuses, so this
module can be exercised without a web app and the routes stay a mapping from
one to the other. `UrlTooLong` passes through untouched: it is configuration,
and the route answers it loudly.
"""

from collections.abc import Callable

from pyheos import HeosError

from app.api.serializers import serialize_now_playing
from app.music.heos import HeosController, MusicUnavailable
from app.music.jellyfin import JellyfinError, JellyfinLibrary
from app.music.queue import QueueManager


class PlaybackError(Exception):
    """Base class; the message is written for whoever reads the log."""


class Unavailable(PlaybackError):
    """Not connected, or no library to play from. Nobody's fault; try later."""


class UnknownPlayer(PlaybackError):
    """No speaker with that id. The one failure that is the caller's."""


class SpeakerRefused(PlaybackError):
    """The speaker is reachable but would not do it."""


class LibraryFailed(PlaybackError):
    """Jellyfin failed. Upstream, not us."""


class NothingToPlay(PlaybackError):
    """The album resolved to no tracks - refused rather than a silent success."""


class Playback:
    """Every speaker, and the sources that can be driving one.

    `library` and `queues` come as a pair or not at all: speakers without
    Jellyfin is a coherent setup in which the panel only controls what the
    speakers are already playing.
    """

    def __init__(
        self,
        controller: HeosController,
        library: JellyfinLibrary | None = None,
        queues: QueueManager | None = None,
    ) -> None:
        self._controller = controller
        self._library = library if queues is not None else None
        self._queues = queues if library is not None else None

    @property
    def library(self) -> JellyfinLibrary | None:
        """For browsing, which involves no speaker and no precedence."""
        return self._library

    # -- reads -------------------------------------------------------------

    def players(self) -> dict:
        """The whole `/api/music/players` body.

        Never raises: speakers that are asleep are an ordinary state, and the
        panel needs `connected: false` to render rather than an error.
        """
        players = self._controller.players()
        for player in players:
            # What HomeDash is holding for this speaker, which the speaker
            # itself cannot report: as far as it knows it was handed one stream.
            player["queue"] = None
            if self._queues is None:
                continue
            player["queue"] = self._queues.snapshot(player["id"])
            track = self._queues.current(player["id"])
            if track is not None:
                player["now_playing"] = serialize_now_playing(track, player.get("now_playing"))
        return {
            "connected": self._controller.connected,
            "library": self._library is not None,
            "players": players,
        }

    # -- commands ----------------------------------------------------------

    async def transport(self, player_id: int, action: str) -> None:
        """Play, pause, stop, next or previous - routed to whoever owns it.

        `play_next` on a speaker given a URL does nothing at all: the entry
        `play_url` appended is always the last in the speaker's queue, so there
        is nothing after it. A skip button that silently did nothing is the
        kind of fault a wall panel hides well, which is why HomeDash's queue is
        asked first.
        """
        self._require_connected()

        async def command() -> None:
            if self._queues is not None:
                if action == "next" and await self._queues.next(player_id):
                    return
                if action == "previous" and await self._queues.previous(player_id):
                    return
                # Stopping is more than a command when HomeDash owns the queue:
                # the speaker's own queue holds whatever `play_url` appended and
                # has to be emptied, and only the queue can hold its lock
                # across all of that.
                if action == "stop" and await self._queues.stop_and_release(player_id):
                    return
            await self._controller.transport(player_id, action)

        await self._speaker(player_id, command)

    async def set_volume(self, player_id: int, level: int) -> None:
        self._require_connected()
        await self._speaker(player_id, lambda: self._controller.set_volume(player_id, level))

    async def play(
        self, player_id: int, album_id: str, track_ids: list[str] | None = None
    ) -> int:
        """Start an album, or the listed tracks from it, on one speaker.

        Returns how many tracks were queued. The tracks are always resolved
        through their album - one fetch and a filter rather than one request
        per track - since the panel only ever plays from an album it is already
        showing.
        """
        self._require_connected()
        if self._library is None or self._queues is None:
            raise Unavailable(
                "no music library; set HOMEDASH_JELLYFIN_URL and HOMEDASH_JELLYFIN_API_KEY"
            )
        # Checked before Jellyfin is asked anything, so a stale player id from
        # the panel costs nothing.
        if player_id not in {p["id"] for p in self._controller.players()}:
            raise UnknownPlayer(f"no player with id {player_id}")

        try:
            tracks = self._library.tracks(album_id)
        except JellyfinError as exc:
            raise LibraryFailed(str(exc)) from None
        if track_ids is not None:
            by_id = {t.id: t for t in tracks}
            tracks = [by_id[t] for t in track_ids if t in by_id]
        if not tracks:
            raise NothingToPlay("nothing to play")

        await self._queues.start(player_id, tracks)
        return len(tracks)

    # -- internals ---------------------------------------------------------

    def _require_connected(self) -> None:
        # Reads degrade to "nothing yet"; writes must not, or a tap on the wall
        # would report success while the speaker never heard it.
        if not self._controller.connected:
            raise Unavailable("not connected to HEOS yet; still retrying")

    async def _speaker(self, player_id: int, command: Callable) -> None:
        """Run a speaker command, sorting its failures into this module's own.

        Only KeyError was handled once, so a speaker that had dropped off wifi
        escaped as a 500 with a stack trace and the button simply did nothing.
        """
        try:
            await command()
        except KeyError:
            raise UnknownPlayer(f"no player with id {player_id}") from None
        except MusicUnavailable as exc:
            raise Unavailable(str(exc) or "not connected to HEOS") from None
        except HeosError as exc:
            raise SpeakerRefused(f"the speaker refused the command: {exc}") from None
