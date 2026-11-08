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

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

import anyio


class AudioSourceError(RuntimeError):
    """Raised when a source cannot open the audio it was asked for."""


@dataclass(frozen=True)
class Artist:
    id: str
    name: str
    # What the library sorted this artist by, which is not always the name it
    # displays: Jellyfin files "The Beatles" under B. The panel's A-Z rail
    # jumps by position in the list, so it has to index on the same string the
    # list was ordered by or the letters would not run downwards.
    sort_name: str = ""


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
    # The album this track belongs to, which is where its cover art lives.
    # Carried on the track rather than looked up later because a queue can be
    # started from an explicit list of tracks, with no album in the request to
    # go back to. Optional so a library that cannot say keeps working - the
    # panel falls back to its placeholder.
    album_id: str | None = None


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


@dataclass(frozen=True)
class AudioStream:
    """Audio that is open and ready to serve, exactly as the speaker should get it.

    Everything that differs between sources is decided by the source and carried
    here: a proxied file answers 206 with a Content-Range, a transcode answers 200
    with neither, and what has to be released afterwards is a connection for one
    and a process for the other. The route copies these onto a response and has
    no opinion about any of them.
    """

    status_code: int
    media_type: str
    headers: Mapping[str, str]
    chunks: AsyncIterator[bytes]
    close: Callable[[], Awaitable[None]]

    async def body(self) -> AsyncIterator[bytes]:
        """The bytes, releasing the source however the fetch ends.

        A streaming response is pulled long after the handler that opened it
        has returned, so this is the only place left that sees the end of it.

        Shielded because a speaker hanging up mid-track is routine, and arrives
        here as a cancellation that would otherwise also cancel the first await
        inside `close()` and leave the rest of the cleanup undone.
        """
        try:
            async for chunk in self.chunks:
                yield chunk
        finally:
            with anyio.CancelScope(shield=True):
                await self.close()


class AudioSource(Protocol):
    async def open_stream(self, item_id: str, range_header: str | None = None) -> AudioStream:
        """Open one item's audio without reading it, or raise AudioSourceError."""
        ...
