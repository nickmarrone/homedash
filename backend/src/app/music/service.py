"""The process-wide music objects, and the switch that decides they exist.

Mirrors `sse.broadcaster`: module-level singletons other modules import, rather
than something threaded through the app object. The difference is that these
are optional - a panel with no speakers configured never builds them, and every
music route answers 503.

Nothing here is persisted. The speakers hold their own playback state and it is
read back from them; the queue and the token store are in-process, like the
weather cache. A restart therefore stops the music after the current track.
"""

import logging

from app.config import get_settings
from app.music.heos import HeosController
from app.music.jellyfin import JellyfinLibrary
from app.music.queue import QueueManager
from app.music.tokens import TokenStore, stream_url
from app.sse import broadcaster

logger = logging.getLogger(__name__)
settings = get_settings()

_controller: HeosController | None = None
_library: JellyfinLibrary | None = None
_tokens: TokenStore | None = None
_queues: QueueManager | None = None


def music_configured() -> bool:
    """Whether this deployment has speakers at all.

    Both halves are required, and a host without the flag is a common way to
    half-configure it, so say so rather than starting a connection nobody
    asked for.
    """
    return bool(settings.music_enabled and settings.heos_host)


def library_configured() -> bool:
    """Whether there is a library to browse.

    Separate from `music_configured` on purpose: speakers without Jellyfin is a
    perfectly coherent setup - the panel still controls whatever is playing -
    so the browse routes are gated independently of the transport ones.
    """
    return bool(music_configured() and settings.jellyfin_url and settings.jellyfin_api_key)


def start_music() -> None:
    global _controller, _library, _tokens, _queues
    if not music_configured():
        if settings.music_enabled and not settings.heos_host:
            logger.warning(
                "HOMEDASH_MUSIC_ENABLED is set but HOMEDASH_HEOS_HOST is empty; "
                "music is off. Set it to the IP of any one HEOS speaker - the "
                "rest are enumerated over the connection to it."
            )
        elif settings.heos_host and not settings.music_enabled:
            # The other half-configuration, and the one that used to be
            # completely silent. Somebody who has filled in a speaker address
            # has plainly asked for music, so an unset flag is a mistake rather
            # than a preference - and with nothing logged the only evidence was
            # a 503 from a route the panel calls and discards.
            logger.warning(
                "HOMEDASH_HEOS_HOST is set to %s but HOMEDASH_MUSIC_ENABLED is not "
                "true; music is off and every /api/music route will answer 503.",
                settings.heos_host,
            )
        else:
            # Neither is set: an ordinary panel with no speakers. Said once, at
            # DEBUG, so that "is music even meant to be on here?" is answerable
            # without reading the config.
            logger.debug("No music configured; the music routes will answer 503.")
        return

    if library_configured():
        _library = JellyfinLibrary(
            settings.jellyfin_url, settings.jellyfin_api_key, settings.jellyfin_music_library_id
        )
        _tokens = TokenStore()
        _queues = QueueManager(
            play_url=_play_url,
            url_for=_url_for,
            stop_player=_stop_player,
            clear_speaker_queue=_clear_speaker_queue,
            prune_speaker_queue=_prune_speaker_queue,
        )
        if not settings.public_base_url:
            logger.warning(
                "HOMEDASH_JELLYFIN_URL is set but HOMEDASH_PUBLIC_BASE_URL is empty. "
                "The speaker fetches audio from HomeDash itself, so it needs an "
                "address on the LAN it can route to - a container's own address is "
                "not one. Playback will fail until this is set."
            )

    _controller = HeosController(
        settings.heos_host, on_change=_publish_change, on_state=_on_player_state
    )
    # Logged before the connection is attempted, not after it succeeds: the
    # connect runs in a background task that may take a retry cycle or never
    # succeed at all, and "did this feature start" has to be answerable
    # separately from "did the speakers answer".
    logger.info(
        "Music enabled; connecting to HEOS at %s (library: %s)",
        settings.heos_host,
        "Jellyfin" if _library is not None else "none - transport only",
    )
    _controller.start()


async def stop_music() -> None:
    global _controller, _library, _tokens, _queues
    if _controller is not None:
        await _controller.stop()
    _controller = None
    _library = None
    _tokens = None
    _queues = None


def get_controller() -> HeosController | None:
    return _controller


def get_library() -> JellyfinLibrary | None:
    return _library


def get_tokens() -> TokenStore | None:
    return _tokens


def get_queues() -> QueueManager | None:
    return _queues


def _url_for(track) -> str:
    """The short URL a speaker is given for one track.

    Minted per play rather than cached per track: the token store is bounded,
    and a URL that has fallen out of it must not be handed to a speaker as if
    it still resolved.
    """
    assert _tokens is not None
    return stream_url(settings.public_base_url, _tokens.mint(track.id))


async def _play_url(player_id: int, url: str) -> None:
    assert _controller is not None
    await _controller.play_url(player_id, url)


async def _stop_player(player_id: int) -> None:
    """Ends an album that was skipped past or stopped - see queue._end."""
    assert _controller is not None
    await _controller.transport(player_id, "stop")


# Tidying the speaker's own queue is best-effort, and that is a decision rather
# than laziness. HEOS answers an error for `clear_queue` on an empty queue, and
# older firmware need not implement `get_queue` at all - neither of which is a
# reason to fail a stop the user asked for, or to abort the track change that
# was the actual job. It is logged at debug because on the two speakers here it
# is expected to be quiet, and a warning per album would train people to ignore
# the log.


async def _clear_speaker_queue(player_id: int) -> None:
    assert _controller is not None
    try:
        await _controller.clear_queue(player_id)
    except Exception:
        logger.debug("Could not clear player %d's queue", player_id, exc_info=True)


async def _prune_speaker_queue(player_id: int) -> None:
    assert _controller is not None
    try:
        await _controller.prune_queue(player_id)
    except Exception:
        logger.debug("Could not prune player %d's queue", player_id, exc_info=True)


async def _on_player_state(player_id: int, state: str) -> None:
    """Feed speaker state to the queue, which is what advances an album."""
    if _queues is not None:
        await _queues.on_state(player_id, state)


def _publish_change() -> None:
    """Wake the panel after a pushed HEOS event.

    Deliberately carries no payload. The panel re-reads /api/music/players,
    which is one small query against in-memory state, and that keeps a single
    source of truth for the wire shape instead of two that can drift.
    """
    broadcaster.publish("music.updated")
