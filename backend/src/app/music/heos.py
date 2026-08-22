"""The one connection to the HEOS system.

This is the first long-lived outbound connection in the app. Everything else
reaches the network on an APScheduler interval, fetches, and lets go; HEOS is a
socket that stays open for as long as the container runs and pushes state at us
unprompted. That difference is why it is started as an asyncio task in the
lifespan rather than registered as a job.

Why the HEOS CLI protocol and not DLNA/AVTransport: HEOS pushes change events
down the same socket the commands go up, so a speaker's state arrives without
polling and turns straight into an SSE publish. The DLNA equivalent (GENA)
would have HomeDash hosting a NOTIFY callback endpoint and renewing
subscriptions, or polling GetPositionInfo forever. HEOS also has multiroom
grouping, which DLNA has no concept of at all, and every speaker here is HEOS
so a second generic code path would buy nothing.

`pyheos` owns the protocol itself - the serialized command lock, the heartbeat
keepalive, reconnect with backoff, and demultiplexing unsolicited events from
command responses. Those are exactly the sharp edges that made hand-rolling the
telnet client a bad trade, and it has no transitive dependencies.
"""

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from pyheos import Heos, HeosOptions, HeosPlayer, PlayState, SignalType

logger = logging.getLogger(__name__)

# Actions the panel may ask for. Anything else is rejected at the route rather
# than passed through, so a typo cannot reach the speaker as an unknown command.
TRANSPORT_ACTIONS = ("play", "pause", "stop", "next", "previous")

# Progress ticks once a second per player. It is genuinely useful - it is where
# the position comes from - but it must not become an SSE publish per second per
# speaker, so it updates the snapshot and is not itself a reason to notify.
_PROGRESS_EVENT = "event/player_now_playing_progress"

# The one event the queue turns on: a track finishing is reported as the
# speaker going to `stop`, and there is no more specific signal for it.
_STATE_EVENT = "event/player_state_changed"

# How long to wait before retrying the first connection. pyheos handles
# reconnection once it has connected at least once; this covers the case where
# the speaker is off at boot, which it usually is.
_INITIAL_RETRY_SECONDS = 30.0


class MusicUnavailable(RuntimeError):
    """Raised when the panel asks for music and there is no HEOS connection."""


class HeosController:
    """Owns the HEOS connection and exposes the little of it the panel needs.

    Deliberately not a general HEOS client: it answers "what are the speakers
    doing" and forwards five transport verbs, a volume, and a URL. Browsing
    HEOS's own music services is not in scope - the library is Jellyfin's.
    """

    def __init__(
        self,
        host: str,
        on_change: Callable[[], None] | None = None,
        connect: Callable[[str], Any] | None = None,
        on_state: Callable[[int, str], Any] | None = None,
    ) -> None:
        self._host = host
        self._on_change = on_change
        # Awaited on every state change. This is what advances an album: the
        # only signal that a track has finished is the speaker going to `stop`.
        self._on_state = on_state
        # Injected in tests. The real one is pyheos' own connect-and-retry
        # constructor; a fake needs only `players` and `disconnect`.
        self._connect = connect or _connect_to_heos
        self._heos: Heos | None = None
        self._task: asyncio.Task | None = None
        self._unsubscribe: Callable[[], None] | None = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Begin connecting, in the background.

        Never awaited by the lifespan. A speaker that is unplugged, asleep, or
        on a flaky network must not be able to delay the calendar coming up -
        the panel's whole job is to be on the wall showing today.
        """
        self._task = asyncio.create_task(self._run(), name="heos-connect")

    async def _run(self) -> None:
        while True:
            try:
                self._heos = await self._connect(self._host)
                self._subscribe()
                # Connecting does NOT populate `heos.players` - pyheos loads
                # them lazily, and `players` stays an empty dict until asked.
                # Without this the panel connects successfully and then shows
                # no speakers at all, which looks exactly like a deployment
                # with no music configured.
                #
                # Once here, reconnects take care of themselves: pyheos
                # re-loads players on reconnect only if they were ever loaded,
                # so this one call is what arms that too.
                players = await self._heos.get_players()
                logger.info(
                    "Connected to HEOS at %s; %d player(s)", self._host, len(players)
                )
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "Could not reach HEOS at %s; retrying in %.0fs",
                    self._host,
                    _INITIAL_RETRY_SECONDS,
                    exc_info=True,
                )
                await asyncio.sleep(_INITIAL_RETRY_SECONDS)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
        if self._unsubscribe is not None:
            self._unsubscribe()
        if self._heos is not None:
            try:
                await self._heos.disconnect()
            except Exception:
                logger.exception("Error disconnecting from HEOS")

    @property
    def connected(self) -> bool:
        return self._heos is not None

    # -- events ------------------------------------------------------------

    def _subscribe(self) -> None:
        assert self._heos is not None
        self._unsubscribe = self._heos.dispatcher.connect(
            SignalType.PLAYER_EVENT, self._handle_player_event
        )

    async def _handle_player_event(self, player_id: int, event: str) -> None:
        """Turn a pushed HEOS event into an SSE publish.

        pyheos has already applied the event to its own player objects by the
        time this runs, so there is nothing to fetch - the snapshot the panel
        will ask for is current. This only decides whether to wake it up.
        """
        if event == _PROGRESS_EVENT:
            return

        # The queue runs first and the panel is told afterwards, so a track
        # that ends is already replaced by the time the panel refetches. The
        # other order shows a visible flash of "nothing playing" between every
        # two tracks of an album.
        if event == _STATE_EVENT and self._on_state is not None:
            player = self._heos.players.get(player_id) if self._heos else None
            if player is not None:
                try:
                    await self._on_state(player_id, _state_of(player))
                except Exception:
                    # A queue that fails must not take down the event handler,
                    # or every later state change is lost too and the panel
                    # silently stops tracking the speakers.
                    logger.exception("Queue failed handling a state change")

        if self._on_change is not None:
            self._on_change()

    # -- reads -------------------------------------------------------------

    def players(self) -> list[dict]:
        """A snapshot of every speaker, in the wire shape the panel expects."""
        if self._heos is None:
            return []
        return [_serialize_player(p) for p in self._heos.players.values()]

    # -- commands ----------------------------------------------------------

    def _player(self, player_id: int) -> HeosPlayer:
        if self._heos is None:
            raise MusicUnavailable("not connected to HEOS")
        player = self._heos.players.get(player_id)
        if player is None:
            raise KeyError(player_id)
        return player

    async def transport(self, player_id: int, action: str) -> None:
        player = self._player(player_id)
        if action == "play":
            await player.play()
        elif action == "pause":
            await player.pause()
        elif action == "stop":
            await player.stop()
        elif action == "next":
            await player.play_next()
        elif action == "previous":
            await player.play_previous()
        else:
            raise ValueError(f"unknown transport action {action!r}")

    async def set_volume(self, player_id: int, level: int) -> None:
        await self._player(player_id).set_volume(level)

    async def play_url(self, player_id: int, url: str) -> None:
        """Play one stream URL.

        The URL must be 255 characters or fewer - a HEOS firmware limit, not a
        HomeDash one, and one that presents as the speaker simply ignoring the
        command. It is checked where the URL is built (`music.tokens`), which
        is the only place that can do anything about it.
        """
        await self._player(player_id).play_url(url)


def _state_of(player: HeosPlayer) -> str:
    """The speaker's play state as a plain string.

    Shared by the serializer and the queue on purpose: the queue decides an
    album has moved on by comparing against "stop", so the two must not be able
    to disagree about how an enum spells itself.
    """
    return player.state.value if isinstance(player.state, PlayState) else str(player.state)


async def _connect_to_heos(host: str) -> Heos:
    """Connect, with pyheos owning reconnection from here on.

    `auto_reconnect` matters more than it looks: speakers drop off wifi, and
    without it a single blip would leave the panel showing a music screen that
    silently never updates again. `all_progress_events=False` keeps the
    once-a-second position tick to the player actually playing.
    """
    return await Heos.create_and_connect(
        host,
        auto_reconnect=True,
        all_progress_events=False,
    )


def _serialize_player(player: HeosPlayer) -> dict:
    """One speaker, flattened for the panel.

    `state` is normalized to the strings the frontend switches on rather than
    passed through as an enum, and a player that has never played anything has
    no now_playing_media fields worth sending.
    """
    media = player.now_playing_media
    return {
        "id": player.player_id,
        "name": player.name,
        "model": player.model,
        "version": player.version,
        "available": player.available,
        "state": _state_of(player),
        "volume": player.volume,
        "muted": player.is_muted,
        "group_id": player.group_id,
        "now_playing": {
            "title": media.song,
            "artist": media.artist,
            "album": media.album,
            "image_url": media.image_url,
            "duration_ms": media.duration,
            "position_ms": media.current_position,
        }
        if media is not None
        else None,
    }
