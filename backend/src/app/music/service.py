"""The process-wide music controller, and the switch that decides it exists.

Mirrors `sse.broadcaster`: a module-level singleton other modules import,
rather than something threaded through the app object. The difference is that
this one is optional - a panel with no speakers configured never builds it, and
every music route answers 503 instead.

Nothing here is persisted. The speakers hold their own state and we read it
back from them, so there is no row to reconcile and no way for a stored volume
to disagree with the knob on the wall.
"""

import logging

from app.config import get_settings
from app.music.heos import HeosController
from app.sse import broadcaster

logger = logging.getLogger(__name__)
settings = get_settings()

_controller: HeosController | None = None


def music_configured() -> bool:
    """Whether this deployment has music at all.

    Both halves are required, and a host without the flag is a common way to
    half-configure it, so say so rather than starting a connection nobody
    asked for.
    """
    return bool(settings.music_enabled and settings.heos_host)


def start_music() -> None:
    global _controller
    if not music_configured():
        if settings.music_enabled and not settings.heos_host:
            logger.warning(
                "HOMEDASH_MUSIC_ENABLED is set but HOMEDASH_HEOS_HOST is empty; "
                "music is off. Set it to the IP of any one HEOS speaker - the "
                "rest are enumerated over the connection to it."
            )
        return
    _controller = HeosController(settings.heos_host, on_change=_publish_change)
    _controller.start()


async def stop_music() -> None:
    global _controller
    if _controller is not None:
        await _controller.stop()
        _controller = None


def get_controller() -> HeosController | None:
    return _controller


def _publish_change() -> None:
    """Wake the panel after a pushed HEOS event.

    Deliberately carries no payload. The panel re-reads /api/music/players,
    which is one small query against in-memory state, and that keeps a single
    source of truth for the wire shape instead of two that can drift.
    """
    broadcaster.publish("music.updated")
