"""Reconciling the photos table against a photo source.

Same contract the calendar and device reconcilers have: the source is the truth,
applied on a schedule, and anything it no longer offers is deleted - otherwise a
photo pulled out of the folder would keep appearing on the wall, which is the
one failure mode of a family screensaver that actually matters.
"""

import hashlib
import logging
from pathlib import Path

from sqlmodel import Session, select

from app.config import get_settings
from app.models import Photo
from app.photos.base import PhotoSource, SourcePhoto
from app.photos.derivatives import (
    PANEL_ORIENTATIONS,
    derivative_name,
    orientation_of,
    render_all,
    slot_for,
    target_size,
)
from app.photos.folder import FolderPhotoSource

logger = logging.getLogger(__name__)
settings = get_settings()

# Long enough to identify the problem, short enough that a pathological Pillow
# message cannot bloat every row in the table.
MAX_ERROR_LENGTH = 500

_HASH_CHUNK = 1024 * 1024


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def expected_derivatives(photo: Photo) -> set[str]:
    """The derivative filenames a usable photo should have on disk."""
    if photo.error is not None or not photo.hash:
        return set()
    return {
        derivative_name(
            photo.hash,
            target_size(panel, slot_for(photo.orientation, panel)),
        )
        for panel in PANEL_ORIENTATIONS
    }


def reindex(session: Session, source: PhotoSource | None = None) -> bool:
    """Bring the photos table and the derivative cache in line with the source.

    Returns whether anything the panel would notice changed, so the caller can
    decide whether to publish. A rescan that only re-renders a missing
    derivative is not a change: the playlist is identical.
    """
    if source is None:
        source = FolderPhotoSource(settings.photos_dir)
    cache_dir = settings.photo_cache_dir

    offered = source.list_photos()
    existing = {photo.path: photo for photo in session.exec(select(Photo)).all()}

    changed = False
    for item in offered:
        row = existing.get(item.key)
        if row is not None and row.size == item.size and row.mtime_ns == item.mtime_ns:
            # Unchanged on disk, so nothing to re-read - but the derivatives are
            # still checked, cheaply, because the cache is a separate volume that
            # can be wiped on purpose to force a clean re-render.
            _ensure_derivatives(row, cache_dir)
            continue
        if _index_one(session, item, row, cache_dir):
            changed = True

    offered_keys = {item.key for item in offered}
    for path, row in existing.items():
        if path in offered_keys:
            continue
        session.delete(row)
        changed = True
        logger.info("Photo %s is gone from the source; dropped from the index", path)

    session.commit()
    _sweep_cache(session, cache_dir)
    return changed


def _index_one(
    session: Session, item: SourcePhoto, row: Photo | None, cache_dir: Path
) -> bool:
    """Index one new or modified photo. Returns whether the playlist changed."""
    if row is None:
        row = Photo(path=item.key)
    was_usable = row.id is not None and row.error is None

    try:
        photo_hash = hash_file(item.path)
        width, height = _probe(item.path)
        orientation = orientation_of(width, height)
        render_all(item.path, photo_hash, cache_dir, orientation)
    except Exception as exc:
        # Logged and recorded, never raised: one unreadable file in a folder of
        # two thousand must not stop the other 1999 from reaching the panel.
        # The row is kept - with size and mtime - precisely so the next scan
        # skips it instead of reopening it and failing again, forever.
        logger.warning("Could not index photo %s: %s", item.path, exc)
        row.error = str(exc)[:MAX_ERROR_LENGTH]
        row.size = item.size
        row.mtime_ns = item.mtime_ns
        session.add(row)
        return was_usable

    row.hash = photo_hash
    row.width = width
    row.height = height
    row.orientation = orientation
    row.size = item.size
    row.mtime_ns = item.mtime_ns
    row.error = None
    session.add(row)
    return True


def _probe(path: Path) -> tuple[int, int]:
    # Imported through the module rather than by name so tests can count calls
    # and so the Pillow dependency stays confined to derivatives.py.
    from app.photos import derivatives

    return derivatives.probe(path)


def _ensure_derivatives(row: Photo, cache_dir: Path) -> None:
    if row.error is not None or not row.hash:
        return
    missing = [
        name for name in expected_derivatives(row) if not (cache_dir / name).exists()
    ]
    if not missing:
        return
    source = settings.photos_dir / row.path
    if not source.exists():
        return
    try:
        render_all(source, row.hash, cache_dir, row.orientation)
        logger.info("Re-rendered %d missing derivative(s) for %s", len(missing), row.path)
    except Exception as exc:
        logger.warning("Could not re-render derivatives for %s: %s", row.path, exc)


def _sweep_cache(session: Session, cache_dir: Path) -> None:
    """Delete derivatives no row refers to any more.

    A sweep rather than unlinking alongside each deleted row, because the same
    file legitimately backs several rows: two copies of one photo in different
    folders hash identically and therefore share derivatives. Deleting per row
    would blank the surviving copy. Rebuilding the expected set and removing the
    difference handles that, a photo rewritten in place, and a half-finished
    previous run, without any of them being a special case.
    """
    if not cache_dir.is_dir():
        return
    keep: set[str] = set()
    for photo in session.exec(select(Photo)).all():
        keep |= expected_derivatives(photo)
    for path in cache_dir.glob("*.jpg"):
        if path.name in keep:
            continue
        try:
            path.unlink()
        except OSError:
            logger.warning("Could not delete orphaned derivative %s", path)
