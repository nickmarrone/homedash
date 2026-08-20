"""Watching the photo folder so a dropped-in photo appears within seconds.

The scheduled rescan in scheduler.py is the backstop and the thing that
guarantees correctness; this is the thing that makes the folder feel live.

Two facts shape it:

- **inotify does not see writes made on the far side of a network mount.** If
  /photos is an SMB or NFS share filled from another machine - a very common way
  to run this - no event ever fires here. That is not a bug to fix, it is why
  the scheduled rescan exists.
- **A sync tool fires hundreds of events for one batch of photos.** Syncthing
  writes a temp file, renames it, and touches the directory, per photo. Running
  a rescan per event would mean a hundred overlapping scans of the same folder,
  so events are debounced: the scan follows the last event of a burst, not each
  one.
"""

import logging
import threading
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from app.photos.folder import IMAGE_SUFFIXES

logger = logging.getLogger(__name__)

# How long the folder must be quiet before a rescan runs. Long enough to
# swallow a sync burst, short enough that dropping in a photo and walking to
# the kitchen still beats it there.
DEBOUNCE_SECONDS = 5.0


class _DebouncedHandler(FileSystemEventHandler):
    def __init__(self, on_change: Callable[[], None], debounce_seconds: float) -> None:
        self._on_change = on_change
        self._debounce_seconds = debounce_seconds
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        # Temp files and sidecars churn constantly in a synced folder and none
        # of them are photos. Filtering here rather than in the rescan keeps a
        # busy folder from resetting the debounce forever and starving the scan.
        paths = [getattr(event, "src_path", ""), getattr(event, "dest_path", "")]
        if not any(Path(str(p)).suffix.lower() in IMAGE_SUFFIXES for p in paths if p):
            return
        self._schedule()

    def _schedule(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._debounce_seconds, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self) -> None:
        try:
            self._on_change()
        except Exception:
            # A timer thread that raises dies silently and takes the fast path
            # with it, leaving only the scheduled rescan and no clue why.
            logger.exception("Photo folder rescan failed")

    def stop(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None


class FolderWatch:
    """Handle for a running observer, so the lifespan can shut it down."""

    def __init__(self, observer: Observer, handler: _DebouncedHandler) -> None:
        self._observer = observer
        self._handler = handler

    def stop(self) -> None:
        self._handler.stop()
        self._observer.stop()
        self._observer.join(timeout=5)


def start_folder_watch(
    root: Path, on_change: Callable[[], None], debounce_seconds: float = DEBOUNCE_SECONDS
) -> FolderWatch | None:
    """Watch `root` recursively, or return None if it cannot be watched.

    Returning None rather than raising: an unmounted folder, a filesystem with
    no inotify support, or an exhausted watch limit are all reasons to fall back
    to the scheduled rescan, and none of them are a reason to refuse to start
    the app. The panel's calendar has nothing to do with any of it.
    """
    if not root.is_dir():
        logger.info("Photo folder %s does not exist; not watching it", root)
        return None

    handler = _DebouncedHandler(on_change, debounce_seconds)
    observer = Observer()
    try:
        observer.schedule(handler, str(root), recursive=True)
        observer.start()
    except Exception:
        logger.exception("Could not watch %s; falling back to the scheduled rescan", root)
        return None

    logger.info("Watching %s for new photos", root)
    return FolderWatch(observer, handler)
