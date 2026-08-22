"""The music library interface.

There is one implementation - Jellyfin - and unlike `PhotoSource` there is no
particular second one in mind. The seam exists anyway for a smaller reason: it
is what keeps the queue from knowing anything about Jellyfin. `queue.py` needs
a track's id, its title, and a URL a speaker can fetch; it must not learn how
to build a Jellyfin request, or the two would have to change together.

Everything here is a plain dataclass rather than a model, matching the rest of
the app: nothing in this codebase validates its own outbound shapes, and the
API layer builds its dicts by hand.
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Artist:
    id: str
    name: str


@dataclass(frozen=True)
class Album:
    id: str
    name: str
    artist: str | None
    year: int | None


@dataclass(frozen=True)
class Track:
    id: str
    title: str
    artist: str | None
    album: str | None
    # Milliseconds, or None when the server does not know. Used only for
    # display - the queue advances on what the speaker reports, never on a
    # timer, because a duration that disagreed with the file would either cut
    # a track off or leave a silent gap.
    duration_ms: int | None
    track_number: int | None


class MusicLibrary(Protocol):
    def artists(self) -> list[Artist]:
        """Every album artist, in display order."""
        ...

    def albums(self, artist_id: str | None = None) -> list[Album]:
        """Albums, optionally narrowed to one artist."""
        ...

    def tracks(self, album_id: str) -> list[Track]:
        """One album's tracks, in playing order."""
        ...
