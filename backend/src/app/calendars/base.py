from typing import Protocol

from icalendar.cal import Event as VEvent


class CalendarSource(Protocol):
    """A pollable source of iCalendar VEVENT components.

    Implementations own their etag/sync-token state internally (e.g. an
    HTTP conditional-GET cache for ICS, a CalDAV sync-token for phase 2)
    so that unchanged sources are cheap to poll.
    """

    def fetch(self) -> list[VEvent]:
        """Return the current VEVENT components for this source.

        If nothing has changed since the last call, implementations may
        return the same list they returned previously rather than
        re-fetching from the network.
        """
        ...
