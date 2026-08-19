"""Converting stored instants into home-local wall-clock time.

Two things make this less obvious than a bare `astimezone()`:

1. `event_instances.starts_at` is a plain `DATETIME` column, so SQLite hands
   back a *naive* datetime. Calling `.astimezone()` on a naive value makes
   Python interpret it in the **host OS** timezone rather than UTC, so the
   panel's times would shift with the machine's own clock settings - exactly
   the drift `frontend/src/lib/format.ts` goes out of its way to avoid on the
   other side of the wire. Everything is stored in UTC, so say so explicitly
   before converting.

2. All-day instances are not instants at all. `sync._occurrence_bounds` stores
   a date-only VEVENT value at UTC midnight as a placeholder, so converting it
   into a negative-offset zone moves it to the previous calendar date - an
   all-day event on the 15th showing up on the 14th. Its date components are
   already the intended local date, so they are re-anchored at local midnight
   instead of converted.
"""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo


def as_utc(value: datetime) -> datetime:
    """Read a stored instant as UTC, whatever the host clock is set to."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def to_local(value: datetime, tz: ZoneInfo, *, all_day: bool = False) -> datetime:
    """The home-local datetime for a stored instant.

    All-day values keep their calendar date and are re-anchored at local
    midnight; timed values are converted from UTC.
    """
    if all_day:
        return datetime(value.year, value.month, value.day, tzinfo=tz)
    return as_utc(value).astimezone(tz)


def local_date(value: datetime, tz: ZoneInfo, *, all_day: bool = False) -> date:
    """The local calendar date an instant falls on."""
    return to_local(value, tz, all_day=all_day).date()
