"""Grid geometry and event-to-day placement.

Date maths fails silently on a wall panel that nobody is watching, so the
awkward cases are pinned explicitly: DST, week boundaries, year ends, and the
two exclusive-end conventions that decide which day an event lands on.
"""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.calendars.grid import (
    GridItem,
    build_days,
    local_dates_spanned,
    normalize_anchor,
    period_bounds,
    period_title,
    step_anchor,
    week_start_for,
)

NEW_YORK = ZoneInfo("America/New_York")
TOKYO = ZoneInfo("Asia/Tokyo")


class TestWeekStart:
    @pytest.mark.parametrize(
        "day,expected",
        [
            (date(2026, 8, 19), date(2026, 8, 16)),  # Wednesday -> Sunday
            (date(2026, 8, 16), date(2026, 8, 16)),  # Sunday is already the start
            (date(2026, 8, 22), date(2026, 8, 16)),  # Saturday
        ],
    )
    def test_sunday_start(self, day, expected):
        assert week_start_for(day, "sunday") == expected

    @pytest.mark.parametrize(
        "day,expected",
        [
            (date(2026, 8, 19), date(2026, 8, 17)),
            (date(2026, 8, 16), date(2026, 8, 10)),  # Sunday belongs to the week before
            (date(2026, 8, 17), date(2026, 8, 17)),
        ],
    )
    def test_monday_start(self, day, expected):
        assert week_start_for(day, "monday") == expected


class TestNormalizeAnchor:
    def test_month_snaps_to_the_first(self):
        assert normalize_anchor("month", date(2026, 8, 31), "sunday") == date(2026, 8, 1)

    def test_week_snaps_to_the_week_start(self):
        assert normalize_anchor("week", date(2026, 8, 19), "sunday") == date(2026, 8, 16)

    def test_day_is_left_alone(self):
        assert normalize_anchor("day", date(2026, 8, 19), "sunday") == date(2026, 8, 19)

    def test_the_31st_does_not_need_clamping_after_normalising(self):
        """Stepping from the 31st into a 30-day month is the classic
        off-by-one; normalising first removes the question."""
        anchor = normalize_anchor("month", date(2026, 8, 31), "sunday")
        assert step_anchor("month", anchor, 1) == date(2026, 9, 1)


class TestPeriodBounds:
    def test_day_is_a_single_date(self):
        assert period_bounds("day", date(2026, 8, 19), "sunday") == (
            date(2026, 8, 19),
            date(2026, 8, 19),
        )

    def test_week_is_seven_days(self):
        first, last = period_bounds("week", date(2026, 8, 16), "sunday")
        assert (last - first).days == 6

    def test_month_pads_to_whole_weeks(self):
        first, last = period_bounds("month", date(2026, 8, 1), "sunday")
        assert first == date(2026, 7, 26)
        assert last == date(2026, 9, 5)
        assert (last - first).days + 1 == 42

    def test_a_short_month_needs_only_five_rows(self):
        """February 2027 starts on a Monday; with a Sunday week start it
        fits in 35 cells, not 42."""
        first, last = period_bounds("month", date(2027, 2, 1), "sunday")
        assert (last - first).days + 1 == 35

    def test_february_in_a_leap_year(self):
        first, last = period_bounds("month", date(2028, 2, 1), "sunday")
        assert first <= date(2028, 2, 29) <= last

    @pytest.mark.parametrize("year,month", [(y, m) for y in (2026, 2027, 2028) for m in range(1, 13)])
    def test_every_month_is_a_whole_number_of_weeks(self, year, month):
        first, last = period_bounds("month", date(year, month, 1), "sunday")
        assert ((last - first).days + 1) % 7 == 0


class TestStepAnchor:
    def test_month_crosses_the_year_boundary(self):
        assert step_anchor("month", date(2026, 12, 1), 1) == date(2027, 1, 1)
        assert step_anchor("month", date(2026, 1, 1), -1) == date(2025, 12, 1)

    def test_month_steps_into_february(self):
        assert step_anchor("month", date(2026, 1, 1), 1) == date(2026, 2, 1)

    def test_week_and_day(self):
        assert step_anchor("week", date(2026, 8, 16), 1) == date(2026, 8, 23)
        assert step_anchor("day", date(2026, 12, 31), 1) == date(2027, 1, 1)


class TestTitles:
    def test_month(self):
        assert period_title("month", date(2026, 8, 1), "sunday") == "August 2026"

    def test_day(self):
        assert period_title("day", date(2026, 8, 19), "sunday") == "Wed, August 19"

    def test_week_within_one_month(self):
        assert period_title("week", date(2026, 8, 16), "sunday") == "Aug 16 - 22, 2026"

    def test_week_crossing_a_month(self):
        assert period_title("week", date(2026, 8, 30), "sunday") == "Aug 30 - Sep 5, 2026"

    def test_week_crossing_a_year(self):
        assert period_title("week", date(2026, 12, 27), "sunday") == (
            "Dec 27 2026 - Jan 2 2027"
        )


class TestLocalDatesSpanned:
    def spans(self, start, end, all_day=False, tz=NEW_YORK):
        return local_dates_spanned(start, end, all_day, tz)

    def test_timed_event_within_one_day(self):
        assert self.spans(datetime(2026, 8, 15, 16, 0), datetime(2026, 8, 15, 17, 0)) == [
            date(2026, 8, 15)
        ]

    def test_timed_event_crossing_local_midnight(self):
        # 11pm-1am New York, stored in UTC.
        assert self.spans(datetime(2026, 8, 16, 3, 0), datetime(2026, 8, 16, 5, 0)) == [
            date(2026, 8, 15),
            date(2026, 8, 16),
        ]

    def test_event_ending_exactly_at_midnight_stays_on_its_own_day(self):
        """A 10pm-midnight event bleeding into tomorrow is the most visible
        version of this bug."""
        assert self.spans(datetime(2026, 8, 16, 2, 0), datetime(2026, 8, 16, 4, 0)) == [
            date(2026, 8, 15)
        ]

    def test_single_all_day_event(self):
        # DTEND is exclusive: the 15th, ending on the 16th.
        assert self.spans(
            datetime(2026, 8, 15), datetime(2026, 8, 16), all_day=True
        ) == [date(2026, 8, 15)]

    def test_multi_day_all_day_event(self):
        assert self.spans(
            datetime(2026, 8, 17), datetime(2026, 8, 20), all_day=True
        ) == [date(2026, 8, 17), date(2026, 8, 18), date(2026, 8, 19)]

    def test_all_day_event_with_no_dtend(self):
        """sync._occurrence_bounds sets end == start when a VEVENT has no
        DTEND; that is one day, not zero."""
        assert self.spans(
            datetime(2026, 8, 15), datetime(2026, 8, 15), all_day=True
        ) == [date(2026, 8, 15)]

    def test_all_day_dates_are_the_same_in_every_timezone(self):
        for tz in (NEW_YORK, TOKYO, ZoneInfo("UTC")):
            assert self.spans(
                datetime(2026, 8, 15), datetime(2026, 8, 16), all_day=True, tz=tz
            ) == [date(2026, 8, 15)]

    def test_dst_spring_forward_day(self):
        """2026-03-08 is 23 hours long in New York; a midnight-to-midnight
        event still covers exactly that one day."""
        spans = self.spans(
            datetime(2026, 3, 8, 5, 0),  # midnight EST
            datetime(2026, 3, 9, 4, 0),  # midnight EDT
        )
        assert spans == [date(2026, 3, 8)]

    def test_dst_fall_back_day(self):
        spans = self.spans(datetime(2026, 11, 1, 4, 0), datetime(2026, 11, 2, 5, 0))
        assert spans == [date(2026, 11, 1)]

    def test_a_backwards_row_does_not_vanish(self):
        assert self.spans(datetime(2026, 8, 15, 12, 0), datetime(2026, 8, 14, 12, 0)) == [
            date(2026, 8, 15)
        ]

    def test_an_absurd_span_is_capped(self):
        """A corrupt row must not make a month view allocate for ever."""
        spans = self.spans(datetime(2026, 8, 15), datetime(2999, 1, 1))
        assert len(spans) == 367


def item(start, end, all_day=False, title="Event", tz=NEW_YORK):
    return GridItem(
        payload={"id": 1, "title": title, "all_day": all_day},
        dates=local_dates_spanned(start, end, all_day, tz),
        all_day=all_day,
        starts_at=start,
    )


class TestBuildDays:
    def test_every_date_in_range_gets_a_bucket(self):
        days = build_days([], date(2026, 8, 16), date(2026, 8, 22), date(2026, 8, 16), "week", date(2026, 8, 19))
        assert len(days) == 7
        assert days[0]["date"] == "2026-08-16"

    def test_today_is_marked_once(self):
        days = build_days([], date(2026, 8, 16), date(2026, 8, 22), date(2026, 8, 16), "week", date(2026, 8, 19))
        assert [d["date"] for d in days if d["is_today"]] == ["2026-08-19"]

    def test_padding_days_are_marked_outside_the_month(self):
        first, last = period_bounds("month", date(2026, 8, 1), "sunday")
        days = build_days([], first, last, date(2026, 8, 1), "month", date(2026, 8, 19))
        assert days[0]["in_period"] is False   # 26 July
        assert days[-1]["in_period"] is False  # 5 September
        assert sum(1 for d in days if d["in_period"]) == 31

    def test_a_week_view_has_no_padding(self):
        days = build_days([], date(2026, 8, 16), date(2026, 8, 22), date(2026, 8, 16), "week", date(2026, 8, 19))
        assert all(d["in_period"] for d in days)

    def test_a_multi_day_event_appears_on_every_day_it_spans(self):
        events = [item(datetime(2026, 8, 17), datetime(2026, 8, 20), all_day=True, title="Camp")]
        days = build_days(events, date(2026, 8, 16), date(2026, 8, 22), date(2026, 8, 16), "week", date(2026, 8, 19))
        with_camp = [d["date"] for d in days if d["items"]]
        assert with_camp == ["2026-08-17", "2026-08-18", "2026-08-19"]

    def test_continuation_flags_mark_the_middle_and_ends(self):
        events = [item(datetime(2026, 8, 17), datetime(2026, 8, 20), all_day=True)]
        days = {d["date"]: d for d in build_days(events, date(2026, 8, 16), date(2026, 8, 22), date(2026, 8, 16), "week", date(2026, 8, 19))}

        first_day = days["2026-08-17"]["items"][0]
        middle = days["2026-08-18"]["items"][0]
        last_day = days["2026-08-19"]["items"][0]
        assert (first_day["continues_before"], first_day["continues_after"]) == (False, True)
        assert (middle["continues_before"], middle["continues_after"]) == (True, True)
        assert (last_day["continues_before"], last_day["continues_after"]) == (True, False)

    def test_an_event_already_in_progress_shows_on_the_opening_day(self):
        """A start-time cutoff would drop this from the view entirely."""
        events = [item(datetime(2026, 8, 14), datetime(2026, 8, 20), all_day=True)]
        days = build_days(events, date(2026, 8, 16), date(2026, 8, 22), date(2026, 8, 16), "week", date(2026, 8, 19))
        opening = days[0]
        assert opening["items"]
        assert opening["items"][0]["continues_before"] is True

    def test_all_day_events_sort_above_timed_ones(self):
        events = [
            item(datetime(2026, 8, 19, 13, 0), datetime(2026, 8, 19, 14, 0), title="Timed"),
            item(datetime(2026, 8, 19), datetime(2026, 8, 20), all_day=True, title="Banner"),
        ]
        days = build_days(events, date(2026, 8, 19), date(2026, 8, 19), date(2026, 8, 19), "day", date(2026, 8, 19))
        assert [i["title"] for i in days[0]["items"]] == ["Banner", "Timed"]

    def test_timed_events_sort_by_clock(self):
        events = [
            item(datetime(2026, 8, 19, 20, 0), datetime(2026, 8, 19, 21, 0), title="Later"),
            item(datetime(2026, 8, 19, 13, 0), datetime(2026, 8, 19, 14, 0), title="Earlier"),
        ]
        days = build_days(events, date(2026, 8, 19), date(2026, 8, 19), date(2026, 8, 19), "day", date(2026, 8, 19))
        assert [i["title"] for i in days[0]["items"]] == ["Earlier", "Later"]

    def test_events_outside_the_range_are_not_included(self):
        events = [item(datetime(2026, 9, 15, 13, 0), datetime(2026, 9, 15, 14, 0))]
        days = build_days(events, date(2026, 8, 16), date(2026, 8, 22), date(2026, 8, 16), "week", date(2026, 8, 19))
        assert all(not d["items"] for d in days)
