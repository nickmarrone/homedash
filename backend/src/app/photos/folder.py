"""A PhotoSource backed by a local directory.

Populate the folder however you like - Syncthing, an SMB share, Nextcloud, or
dragging files in once a quarter. This module only reads it, and the Docker
mount is read-only: a bug here must never be able to delete a family photo.
"""

import logging
from pathlib import Path

from app.photos.base import SourcePhoto

logger = logging.getLogger(__name__)

# Formats Pillow decodes reliably without extra plugins. HEIC is deliberately
# absent: it needs pillow-heif, and a silently skipped iPhone photo is more
# confusing than an honest "unsupported". Anything not listed is ignored at the
# walk, so it never reaches the indexer and never produces an error row.
IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".tif"})

# Directories that photo-syncing tools scatter through a share. Descending into
# them indexes thumbnails of photos already indexed, at which point the
# screensaver shows the same picture twice at two different qualities.
SKIP_DIRS = frozenset({"@eadir", ".thumbnails", ".stfolder", ".stversions", "#recycle"})


class FolderPhotoSource:
    """Every image under a directory, recursively."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def list_photos(self) -> list[SourcePhoto]:
        if not self.root.is_dir():
            # Not an error: an unconfigured or not-yet-mounted folder just means
            # no screensaver. Logged at info so it is visible when someone is
            # wondering why the panel never drifts.
            logger.info("Photo folder %s does not exist; no photos to index", self.root)
            return []

        photos: list[SourcePhoto] = []
        for path in sorted(self.root.rglob("*")):
            if not self._is_candidate(path):
                continue
            try:
                stat = path.stat()
            except OSError:
                # Vanished mid-walk, or unreadable. Either way the next scan
                # will settle it; one bad file must not abort the whole walk.
                logger.warning("Could not stat %s; skipping", path)
                continue
            photos.append(
                SourcePhoto(
                    key=path.relative_to(self.root).as_posix(),
                    path=path,
                    size=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                )
            )
        return photos

    def _is_candidate(self, path: Path) -> bool:
        if not path.is_file():
            return False
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            return False
        relative = path.relative_to(self.root)
        # Dotfiles cover macOS resource forks (._IMG_1234.JPG), which are real
        # files with a real .jpg suffix and no image in them.
        return not any(
            part.startswith(".") or part.lower() in SKIP_DIRS for part in relative.parts
        )
