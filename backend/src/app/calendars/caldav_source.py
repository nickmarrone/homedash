"""CalDAV calendar adapter.

Exists for latency, not for writing: HomeDash never writes back, but a CalDAV
server reports a change the moment it happens, where an ICS feed is a file the
provider regenerates on its own schedule - often hours later.

The whole point is that an unchanged calendar is cheap to poll, since this
runs every minute. Two mechanisms, in order of preference:

  * RFC 6578 sync-collection. One small request returns "nothing changed",
    and that is the common case.
  * A content digest, for servers that do not implement sync-collection. Still
    a full fetch, but it avoids re-expanding and rewriting the database when
    nothing actually moved.

Which one produced the stored token is recorded in the token itself, so the
two can never be confused after a server upgrade changes the answer.
"""

import hashlib
import logging
from datetime import datetime

import caldav
from icalendar import Calendar
from icalendar.cal import Event as VEvent

logger = logging.getLogger(__name__)

TOKEN_PREFIX = "token:"
DIGEST_PREFIX = "digest:"


def _default_calendar_factory(
    url: str, username: str, password: str, timeout: float
) -> caldav.Calendar:
    client = caldav.DAVClient(url=url, username=username, password=password, timeout=timeout)
    return caldav.Calendar(client=client, url=url)


class CalDAVCalendarSource:
    """Polls one CalDAV calendar collection."""

    def __init__(
        self,
        url: str,
        username: str,
        password: str,
        window_start: datetime,
        window_end: datetime,
        sync_state: str | None = None,
        timeout: float = 20.0,
        calendar_factory=_default_calendar_factory,
    ) -> None:
        self.url = url
        self.username = username
        self.password = password
        self.window_start = window_start
        self.window_end = window_end
        self.timeout = timeout
        self._sync_state = sync_state
        self._calendar_factory = calendar_factory
        self.changed = False
        self._vevents: list[VEvent] = []

    @property
    def sync_state(self) -> str | None:
        return self._sync_state

    def fetch(self) -> list[VEvent]:
        calendar = self._calendar_factory(
            self.url, self.username, self.password, self.timeout
        )

        stored = self._sync_state or ""
        if stored.startswith(TOKEN_PREFIX):
            token = stored[len(TOKEN_PREFIX) :]
            if self._unchanged_since(calendar, token):
                self.changed = False
                return self._vevents

        vevents = self._fetch_all(calendar)

        # Re-read the collection's token *after* the full fetch, so the token
        # we store describes the data we actually just materialized. Taking it
        # beforehand would let a change landing mid-fetch be marked as already
        # seen and stay invisible until the next unrelated edit.
        token = self._read_sync_token(calendar)
        if token is not None:
            new_state = f"{TOKEN_PREFIX}{token}"
        else:
            new_state = DIGEST_PREFIX + _digest(vevents)

        if new_state == self._sync_state and stored.startswith(DIGEST_PREFIX):
            # Digest fallback: identical content, so skip the rebuild.
            self.changed = False
            self._vevents = vevents
            return vevents

        self._vevents = vevents
        self._sync_state = new_state
        self.changed = True
        return vevents

    def _unchanged_since(self, calendar: caldav.Calendar, token: str) -> bool:
        """True when the server reports no changes since `token`."""
        try:
            changes = calendar.objects_by_sync_token(sync_token=token, load_objects=False)
            return not any(True for _ in changes)
        except Exception:
            # An expired or server-rejected token is not an error worth
            # failing the sync over - it just means falling back to a full
            # fetch, which re-establishes a fresh token.
            logger.info(
                "CalDAV sync-token check failed for %s; falling back to a full fetch",
                self.url,
                exc_info=True,
            )
            return False

    def _read_sync_token(self, calendar: caldav.Calendar) -> str | None:
        try:
            objects = calendar.objects_by_sync_token(load_objects=False)
            return getattr(objects, "sync_token", None)
        except Exception:
            logger.info(
                "CalDAV server at %s does not support sync-collection; "
                "using a content digest instead",
                self.url,
                exc_info=True,
            )
            return None

    def _fetch_all(self, calendar: caldav.Calendar) -> list[VEvent]:
        """Every VEVENT overlapping the sync window.

        expand=False keeps recurring events as masters carrying their RRULE,
        so they flow through the same `recurring_ical_events` expansion as
        every other kind rather than being expanded server-side.
        """
        results = calendar.search(
            start=self.window_start,
            end=self.window_end,
            event=True,
            expand=False,
        )
        vevents: list[VEvent] = []
        for result in results:
            data = getattr(result, "data", None)
            if not data:
                continue
            vevents.extend(Calendar.from_ical(data).walk("VEVENT"))
        return vevents


def _digest(vevents: list[VEvent]) -> str:
    """Order-independent fingerprint of a calendar's contents.

    Sorted because servers are under no obligation to return objects in a
    stable order, and an order flap would otherwise read as a change and
    trigger a pointless rebuild every minute.
    """
    parts = sorted(vevent.to_ical() for vevent in vevents)
    hasher = hashlib.sha256()
    for part in parts:
        hasher.update(part)
        hasher.update(b"\x00")
    return hasher.hexdigest()
