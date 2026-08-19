from typing import Protocol

from icalendar.cal import Event as VEvent


class CalendarSource(Protocol):
    """A pollable source of iCalendar VEVENT components.

    Implementations own their resume state internally - an HTTP conditional-GET
    ETag for ICS, a sync-token for CalDAV and Google - so that unchanged
    sources are cheap to poll. That state is what makes a one-minute poll
    affordable: an unchanged calendar costs a single small request.

    The protocol deliberately stops at VEVENTs rather than at expanded
    occurrences, so every source keeps flowing through the one
    `recurring_ical_events` expansion in `sync.py` and inherits the same
    rolling-window behaviour.
    """

    #: Whether the last `fetch()` returned different data from the time before.
    changed: bool

    def fetch(self) -> list[VEvent]:
        """Return the current VEVENT components for this source.

        If nothing has changed since the last call, implementations may
        return the same list they returned previously rather than
        re-fetching from the network, and must leave `changed` False.
        """
        ...

    @property
    def sync_state(self) -> str | None:
        """Opaque token to persist and hand back on the next poll."""
        ...
