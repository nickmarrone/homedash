"""Building the day, lookahead, week, and month views.

Every view is the same thing at a different width - a list of day buckets,
each holding the events that touch that day - so they share one response
shape and the frontend renders one array with different CSS. Adding the
3- and 5-day lookaheads therefore cost a row in LOOKAHEAD_DAYS and three
branches, not a rendering path.

This lives on the server rather than in the browser, not because the date
maths is hard, but because the frontend was deliberately built to do no
timezone work at all: `frontend/src/lib/format.ts` reads wall-clock digits
straight out of an ISO string rather than going through Date/Intl, so that
the panel's own OS clock can never shift what it displays. The backend
already owns the home timezone. Handing back prev/next anchors keeps
navigation on this side of that line too.

Every function takes its timezone, week start, and "today" explicitly rather
than reading the settings singleton, so the awkward cases - DST, week
boundaries, year ends - can be driven directly from tests.
"""

import calendar as calendar_module
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from app.calendars.localtime import to_local

View = Literal["day", "next3", "next5", "week", "month"]
WeekStart = Literal["sunday", "monday"]

VIEWS: tuple[str, ...] = ("day", "next3", "next5", "week", "month")

# The rolling lookaheads: a fixed number of days starting at the anchor, which
# defaults to today. They are deliberately *not* snapped to a week boundary the
# way `week` is - "the next three days" answers a different question from "this
# week", and on a Sunday a snapped three-day view would be almost entirely in
# the past. That difference is the whole feature, so it lives in
# `normalize_anchor` rather than in a special case at the call site.
LOOKAHEAD_DAYS: dict[str, int] = {"next3": 3, "next5": 5}

MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
WEEKDAY_SHORT = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def week_start_for(day: date, week_starts_on: WeekStart) -> date:
    """The first day of the week `day` falls in."""
    # date.weekday() is Monday=0 .. Sunday=6.
    offset = day.weekday() if week_starts_on == "monday" else (day.weekday() + 1) % 7
    return day - timedelta(days=offset)


def normalize_anchor(view: View, anchor: date, week_starts_on: WeekStart) -> date:
    """Snap an anchor to the canonical first day of its period.

    Doing this up front removes a whole class of bug: an anchor left on the
    31st and then stepped into a 30-day month has to be clamped somewhere,
    and every place that forgets is an off-by-one. After normalising, prev
    and next are unambiguous.
    """
    if view == "month":
        return anchor.replace(day=1)
    if view == "week":
        return week_start_for(anchor, week_starts_on)
    # `day` and the lookaheads start exactly where they are pointed, so their
    # anchor is already canonical.
    return anchor


def period_bounds(view: View, anchor: date, week_starts_on: WeekStart) -> tuple[date, date]:
    """The inclusive range of local dates a view renders.

    A month is padded out to whole weeks, so the grid is always rectangular;
    the padding days are marked `in_period: False`.
    """
    if view == "day":
        return anchor, anchor
    if view == "week":
        return anchor, anchor + timedelta(days=6)
    if view in LOOKAHEAD_DAYS:
        return anchor, anchor + timedelta(days=LOOKAHEAD_DAYS[view] - 1)

    last_day = calendar_module.monthrange(anchor.year, anchor.month)[1]
    first = week_start_for(anchor, week_starts_on)
    last = week_start_for(anchor.replace(day=last_day), week_starts_on) + timedelta(days=6)
    return first, last


def step_anchor(view: View, anchor: date, direction: int) -> date:
    """The anchor one period earlier (-1) or later (+1)."""
    if view == "day":
        return anchor + timedelta(days=direction)
    if view == "week":
        return anchor + timedelta(days=7 * direction)
    if view in LOOKAHEAD_DAYS:
        # A whole window at a time, so paging never re-shows a day just seen.
        return anchor + timedelta(days=LOOKAHEAD_DAYS[view] * direction)

    month = anchor.month + direction
    year = anchor.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    return date(year, month, 1)


def period_title(view: View, anchor: date, week_starts_on: WeekStart) -> str:
    if view == "month":
        return f"{MONTH_NAMES[anchor.month - 1]} {anchor.year}"
    if view == "day":
        return f"{WEEKDAY_SHORT[anchor.weekday()]}, {MONTH_NAMES[anchor.month - 1]} {anchor.day}"

    first, last = period_bounds(view, anchor, week_starts_on)
    left = f"{MONTH_NAMES[first.month - 1][:3]} {first.day}"
    # Only repeat the month or year when the span actually crosses one.
    if first.year != last.year:
        return f"{left} {first.year} - {MONTH_NAMES[last.month - 1][:3]} {last.day} {last.year}"
    if first.month != last.month:
        return f"{left} - {MONTH_NAMES[last.month - 1][:3]} {last.day}, {last.year}"
    return f"{left} - {last.day}, {last.year}"


def local_dates_spanned(
    starts_at: datetime, ends_at: datetime, all_day: bool, tz: ZoneInfo
) -> list[date]:
    """Every local date an instance touches, inclusive.

    Two conventions have to be respected here:

    * An all-day VEVENT's DTEND is *exclusive* - a single-day event on the
      15th carries DTEND of the 16th - so the last rendered day is one before
      it. `sync._occurrence_bounds` sets end == start when a VEVENT has no
      DTEND at all, which is why that case is handled separately.
    * A timed event ending exactly at local midnight belongs to the day it
      started, not to the next one. A 10pm-midnight event bleeding into
      tomorrow is the single most noticeable version of this bug.
    """
    first = to_local(starts_at, tz, all_day=all_day).date()

    if all_day:
        end_date = to_local(ends_at, tz, all_day=True).date()
        last = end_date - timedelta(days=1) if end_date > first else first
    else:
        local_end = to_local(ends_at, tz)
        last = local_end.date()
        midnight = local_end.hour == 0 and local_end.minute == 0 and local_end.second == 0
        if midnight and last > first:
            last -= timedelta(days=1)

    if last < first:
        last = first
    # Guard against a corrupt row spanning years and blowing up a month view.
    span = min((last - first).days, 366)
    return [first + timedelta(days=offset) for offset in range(span + 1)]


@dataclass
class GridItem:
    """One instance placed in the grid, with the dates it covers."""

    payload: dict
    dates: list[date]
    all_day: bool
    starts_at: datetime


def build_days(
    items: list[GridItem],
    first: date,
    last: date,
    anchor: date,
    view: View,
    today: date,
) -> list[dict]:
    """Bucket items into one entry per local date in the range."""
    by_date: dict[date, list[GridItem]] = {}
    for item in items:
        for day in item.dates:
            if first <= day <= last:
                by_date.setdefault(day, []).append(item)

    days: list[dict] = []
    current = first
    while current <= last:
        bucket = by_date.get(current, [])
        # All-day events read as banners across the top of a day; timed ones
        # follow in clock order.
        bucket.sort(key=lambda item: (not item.all_day, item.starts_at))
        days.append(
            {
                "date": current.isoformat(),
                "day_of_month": current.day,
                "weekday_short": WEEKDAY_SHORT[current.weekday()],
                "in_period": _in_period(current, anchor, view),
                "is_today": current == today,
                "items": [
                    {
                        **item.payload,
                        "continues_before": item.dates[0] < current,
                        "continues_after": item.dates[-1] > current,
                    }
                    for item in bucket
                ],
            }
        )
        current += timedelta(days=1)
    return days


def _in_period(day: date, anchor: date, view: View) -> bool:
    """False for the padding days a month grid needs to stay rectangular."""
    if view != "month":
        return True
    return day.month == anchor.month and day.year == anchor.year
