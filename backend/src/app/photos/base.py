"""The photo source interface.

There is one implementation today - a local folder - but Immich is the intended
second one, so the seam exists from the start the way `CalendarSource` did in
Phase 1 when ICS was the only calendar adapter.

An Immich source fetches an album, downloads each asset's preview derivative to
a staging path, and fills in the same fields. Nothing downstream changes: the
indexer only ever sees a key, a readable local path, and the two numbers it
needs to decide whether the file has changed.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class SourcePhoto:
    """One image a source is offering, before it has been indexed."""

    # Stable identity within the source, and the primary key the index
    # reconciles on. For a folder this is the path relative to its root, so
    # remounting the folder somewhere else does not orphan every row.
    key: str
    # A file the indexer can open. May be outside the source's own root if a
    # future source stages downloads.
    path: Path
    size: int
    mtime_ns: int


class PhotoSource(Protocol):
    def list_photos(self) -> list[SourcePhoto]:
        """Every photo currently on offer.

        Returns the complete set rather than a delta: the index reconciles
        against it, and anything missing from it is deleted. A source that
        cannot enumerate should raise rather than return a short list, or a
        transient failure would empty the screensaver.
        """
        ...
