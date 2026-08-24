"""Reading materialized instances back out for a range of local dates.

There is no repository layer in this app and this module is not the start of
one. It exists because exactly one predicate - "which instances touch these
days?" - is genuinely subtle, was written out twice, and the two copies
disagreed: the agenda filtered on `starts_at` alone, so an event already
running when the range opened was dropped from it while the grid still showed
it. A week-long holiday appeared in the agenda on its first morning and then
vanished for six days.

The subtlety is that `event_instances` holds two different kinds of value in
one column. A timed instance carries a real UTC instant; an all-day instance
carries the *floating* midnight of its calendar date, because a birthday is not
an instant and converting one through a timezone moves it to the wrong day.
See calendars/localtime.py. So every range check here is two range checks, and
that is the part worth having in one place.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_
from sqlmodel import Session, select

from app.models import CalendarSource, Event, EventInstance


def instances_touching(
    session: Session,
    tz: ZoneInfo,
    first: date,
    last: date | None = None,
    pad_days: int = 0,
    limit: int | None = None,
) -> Sequence[tuple[EventInstance, CalendarSource | None]]:
    """Every instance overlapping the local dates [`first`, `last`], with its
    calendar attached.

    **Overlap, not a start-time cutoff.** An event that began before `first`
    and is still running belongs to the range - that is the whole reason this
    function exists.

    `last=None` leaves the range open-ended, which is what a forward-looking
    agenda wants: everything from today onwards, cut off by `limit` rather than
    by a date.

    `pad_days` widens both ends. It is for callers that re-bucket the results
    by exact local date afterwards and only need this query to return a
    superset - an instance whose local date is in range can have a UTC instant
    that is not. Callers that render what they are given must leave it at 0, or
    they will show a day either side of what they asked for.
    """
    start_floating = datetime(first.year, first.month, first.day) - timedelta(days=pad_days)
    start_utc = datetime(first.year, first.month, first.day, tzinfo=tz).astimezone(
        timezone.utc
    ) - timedelta(days=pad_days)

    # Exclusive, and `last` is inclusive, hence the extra day before the pad.
    if last is None:
        end_floating = end_utc = None
    else:
        end_floating = datetime(last.year, last.month, last.day) + timedelta(days=1 + pad_days)
        end_utc = datetime(last.year, last.month, last.day, tzinfo=tz).astimezone(
            timezone.utc
        ) + timedelta(days=1 + pad_days)

    timed = [EventInstance.all_day == False, EventInstance.ends_at >= start_utc]  # noqa: E712
    floating = [EventInstance.all_day == True, EventInstance.ends_at >= start_floating]  # noqa: E712
    if end_utc is not None:
        timed.append(EventInstance.starts_at < end_utc)
        floating.append(EventInstance.starts_at < end_floating)

    statement = (
        select(EventInstance, CalendarSource)
        # Outer: an instance whose event or source has gone missing should
        # still render, uncolored, rather than silently vanish from the panel.
        .join(Event, EventInstance.event_id == Event.id, isouter=True)
        .join(CalendarSource, Event.source_id == CalendarSource.id, isouter=True)
        .where(or_(and_(*timed), and_(*floating)))
        .order_by(EventInstance.starts_at)
    )
    if limit is not None:
        statement = statement.limit(limit)
    return session.exec(statement).all()
