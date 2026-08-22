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
        return

    if library_configured():
        _library = JellyfinLibrary(
            settings.jellyfin_url, settings.jellyfin_api_key, settings.jellyfin_music_library_id
        )
        _tokens = TokenStore()
        _queues = QueueManager(play_url=_play_url, url_for=_url_for)
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
