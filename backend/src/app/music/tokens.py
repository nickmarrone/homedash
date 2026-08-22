"""Short opaque URLs for the speaker to fetch audio from.

This module exists because of one number: **HEOS will not fetch a URL longer
than 255 characters**, and reports no error when it declines - the speaker
simply does not play. A Jellyfin stream URL with an item id and parameters is
already long, and it cannot carry a credential anyway (Jellyfin removes
query-parameter auth in 10.13), so the panel hands the speaker a short token
instead and HomeDash resolves it.

The store is in-process, like the weather cache. A restart drops it, which
means a restart mid-album stops the music after the current track - the same
trade the queue itself makes, and worth stating rather than discovering.
"""

import logging
import secrets
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# The hard limit the whole module exists for.
MAX_URL_LENGTH = 255

# 8 characters of base62 is ~48 bits. These are unguessable enough for a LAN
# appliance whose entire API is already unauthenticated, and every character
# spent here comes out of the 255.
_TOKEN_BYTES = 6

# Generous: it has to outlive the longest track anyone owns, plus however long
# the speaker takes to get around to fetching it. It is not a security control,
# so there is nothing to be gained by making it tight.
DEFAULT_TTL_SECONDS = 12 * 60 * 60

# Tokens are minted per track per play, so a household that leaves an album on
# repeat for a week would otherwise grow this without bound.
_MAX_ENTRIES = 2000


class UrlTooLong(ValueError):
    """Raised when a minted URL would exceed what HEOS will fetch."""


@dataclass(frozen=True)
class StreamToken:
    token: str
    track_id: str
    expires_at: float


class TokenStore:
    """Maps short tokens to Jellyfin track ids."""

    def __init__(self, ttl_seconds: float = DEFAULT_TTL_SECONDS, now=time.monotonic) -> None:
        self._ttl = ttl_seconds
        self._now = now
        self._tokens: dict[str, StreamToken] = {}

    def mint(self, track_id: str) -> str:
        self._evict()
        token = secrets.token_urlsafe(_TOKEN_BYTES)
        self._tokens[token] = StreamToken(
            token=token, track_id=track_id, expires_at=self._now() + self._ttl
        )
        return token

    def resolve(self, token: str) -> str | None:
        entry = self._tokens.get(token)
        if entry is None:
            return None
        if entry.expires_at <= self._now():
            del self._tokens[token]
            return None
        return entry.track_id

    def _evict(self) -> None:
        now = self._now()
        expired = [t for t, e in self._tokens.items() if e.expires_at <= now]
        for token in expired:
            del self._tokens[token]
        # Still over after dropping the expired ones: shed the oldest. dicts
        # preserve insertion order, and these are inserted in minting order.
        while len(self._tokens) >= _MAX_ENTRIES:
            del self._tokens[next(iter(self._tokens))]

    def __len__(self) -> int:
        return len(self._tokens)


def stream_url(base_url: str, token: str) -> str:
    """The URL to hand a speaker, checked against the limit it will accept.

    Raising rather than truncating or warning: a URL over the limit produces a
    speaker that accepts the command and plays nothing, which is close to
    undiagnosable from the kitchen. Failing here puts it in the log instead.
    """
    url = f"{base_url.rstrip('/')}/api/music/s/{token}"
    if len(url) > MAX_URL_LENGTH:
        raise UrlTooLong(
            f"stream URL is {len(url)} characters; HEOS will not fetch more than "
            f"{MAX_URL_LENGTH} and reports no error when it declines. Shorten "
            f"HOMEDASH_PUBLIC_BASE_URL ({base_url!r})."
        )
    return url
