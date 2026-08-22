"""Jellyfin as a music library.

Read-only, and deliberately so: the API key is created in Jellyfin's dashboard
and nothing here ever writes. Browsing is three calls that map one-to-one onto
the three panel screens, and the audio itself is opened as a stream rather than
buffered - see `stream()`.

**The key never leaves the server.** The speaker fetches audio from HomeDash,
not from Jellyfin, so no credential is ever put in a URL. That is partly the
rule this project already states for Immich, and partly forced: Jellyfin
deprecated query-parameter auth in 10.11 and removes it entirely in 10.13, so a
URL a speaker fetches directly has nowhere left to carry one.
"""

import logging
from typing import Any

import httpx

from app.music.base import Album, Artist, Track

logger = logging.getLogger(__name__)

# Jellyfin requires `Client` and `Token`; the rest are optional but show up in
# the server's device list, where "HomeDash" is a great deal more useful to
# whoever is reading it than a blank entry.
_CLIENT = "HomeDash"
_DEVICE_ID = "homedash-panel"
_VERSION = "1.0"

# Only what the panel renders. Jellyfin returns a large item object by default
# and this is one of the few places where the response size is worth caring
# about: an artist with fifty albums is fifty of these.
_ALBUM_FIELDS = "ProductionYear,AlbumArtist"
_TRACK_FIELDS = "RunTimeTicks,IndexNumber,AlbumArtist"

# Jellyfin measures time in 100-nanosecond ticks.
_TICKS_PER_MS = 10_000


class JellyfinError(RuntimeError):
    """Raised when Jellyfin cannot be reached or refuses a request."""


class JellyfinLibrary:
    """Browses one Jellyfin server's music.

    `get` is injectable for the same reason the calendar adapters take one:
    it is the seam a test fakes, so no test has to know which module-level
    name httpx happens to be bound to.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        library_id: str = "",
        timeout: float = 20.0,
        get=None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.library_id = library_id
        self.timeout = timeout
        self._get = get or self._default_get

    # -- plumbing ----------------------------------------------------------

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": (
                f'MediaBrowser Client="{_CLIENT}", Device="{_CLIENT}", '
                f'DeviceId="{_DEVICE_ID}", Version="{_VERSION}", Token="{self.api_key}"'
            )
        }

    def _default_get(self, url: str, params: dict, headers: dict):
        return httpx.get(url, params=params, headers=headers, timeout=self.timeout)

    def _request(self, path: str, params: dict) -> Any:
        # Only when the caller has not scoped the request itself. `tracks()`
        # passes the album as parentId, and overriding that with the library
        # root would quietly return the entire library in place of one album.
        if self.library_id and "parentId" not in params:
            params = {**params, "parentId": self.library_id}
        try:
            response = self._get(f"{self.base_url}{path}", params, self.headers)
        except Exception as exc:
            raise JellyfinError(f"could not reach Jellyfin: {exc}") from exc
        if response.status_code == 401:
            raise JellyfinError(
                "Jellyfin rejected the API key. Create one under Dashboard > API Keys "
                "and set HOMEDASH_JELLYFIN_API_KEY."
            )
        if response.status_code >= 400:
            raise JellyfinError(f"Jellyfin returned {response.status_code} for {path}")
        return response.json()

    # -- browsing ----------------------------------------------------------

    def artists(self) -> list[Artist]:
        payload = self._request(
            "/Artists",
            {"sortBy": "SortName", "sortOrder": "Ascending", "startIndex": 0},
        )
        return [
            Artist(id=item["Id"], name=item.get("Name") or "Unknown artist")
            for item in payload.get("Items", [])
            if item.get("Id")
        ]

    def albums(self, artist_id: str | None = None) -> list[Album]:
        params: dict[str, Any] = {
            "includeItemTypes": "MusicAlbum",
            "recursive": "true",
            "sortBy": "PremiereDate,SortName",
            "sortOrder": "Ascending",
            "fields": _ALBUM_FIELDS,
        }
        if artist_id:
            # albumArtistIds, not artistIds: the latter also matches albums the
            # artist merely appears on, so a compilation with one guest track
            # would show up under their name as if it were theirs.
            params["albumArtistIds"] = artist_id
        payload = self._request("/Items", params)
        return [
            Album(
                id=item["Id"],
                name=item.get("Name") or "Unknown album",
                artist=item.get("AlbumArtist"),
                year=item.get("ProductionYear"),
            )
            for item in payload.get("Items", [])
            if item.get("Id")
        ]

    def tracks(self, album_id: str) -> list[Track]:
        # parentId scopes to the album, so the library-wide parentId that
        # _request would otherwise add must not also be applied - hence the
        # explicit value here, which takes precedence.
        payload = self._request(
            "/Items",
            {
                "parentId": album_id,
                "includeItemTypes": "Audio",
                "sortBy": "ParentIndexNumber,IndexNumber,SortName",
                "sortOrder": "Ascending",
                "fields": _TRACK_FIELDS,
            },
        )
        return [
            Track(
                id=item["Id"],
                title=item.get("Name") or "Unknown track",
                artist=item.get("AlbumArtist") or item.get("Artists", [None])[0],
                album=item.get("Album"),
                duration_ms=_ticks_to_ms(item.get("RunTimeTicks")),
                track_number=item.get("IndexNumber"),
            )
            for item in payload.get("Items", [])
            if item.get("Id")
        ]

    # -- media -------------------------------------------------------------

    def stream_url(self, track_id: str) -> str:
        """Where the audio actually lives, for the proxy to open.

        `static=true` asks for the original file rather than a transcode. The
        speakers handle FLAC, ALAC, MP3 and AAC natively, and transcoding on
        the server would burn CPU to produce something worse.
        """
        return f"{self.base_url}/Audio/{track_id}/stream?static=true"

    def art_url(self, item_id: str, max_width: int = 480) -> str:
        """A cover image, at a size a panel can actually use.

        Jellyfin generated these at import time, so asking for a bounded width
        costs nothing and avoids pulling a 3000px scan across the LAN for a
        44px thumbnail.
        """
        return f"{self.base_url}/Items/{item_id}/Images/Primary?maxWidth={max_width}"


def _ticks_to_ms(ticks: Any) -> int | None:
    if not isinstance(ticks, int) or ticks <= 0:
        return None
    return ticks // _TICKS_PER_MS
