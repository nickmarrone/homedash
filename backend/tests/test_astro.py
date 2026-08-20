"""Moon phase and sky events.

Checked against published ephemerides rather than against the implementation,
because that is the only thing that can catch a transcription slip in Meeus's
coefficients - arithmetic this dense is wrong silently or not at all, and a
moon a day out is exactly the kind of error nobody reports and everybody
notices.

Tolerances are deliberately tight (minutes, not hours). A naive
"days since a known new moon, modulo 29.53" passes an hours-wide test and
still prints the full moon on the wrong evening a third of the time, so a
loose test here would be worse than none.
"""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.astro import (
    METEOR_SHOWERS,
    astro_summary,
    meteor_showers_between,
    moon_phase,
    next_moon_phases,
    previous_new_moon,
    radiant_max_altitude,
    seasons_between,
    sky_events,
)

UTC = timezone.utc
PACIFIC = ZoneInfo("America/Los_Angeles")


def minutes_apart(a: datetime, b: datetime) -> float:
    return abs((a - b).total_seconds()) / 60.0


class TestMoonInstants:
    @pytest.mark.parametrize(
        "probe, published",
        [
            (datetime(2026, 1, 20, tzinfo=UTC), datetime(2026, 1, 18, 19, 52, tzinfo=UTC)),
            (datetime(2026, 8, 14, tzinfo=UTC), datetime(2026, 8, 12, 17, 37, tzinfo=UTC)),
            (datetime(2000, 1, 8, tzinfo=UTC), datetime(2000, 1, 6, 18, 14, tzinfo=UTC)),
        ],
    )
    def test_new_moons_match_published_times(self, probe, published):
        _, found = previous_new_moon(probe)
        assert minutes_apart(found, published) < 5

    @pytest.mark.parametrize(
        "probe, published",
        [
            (datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 3, 10, 3, tzinfo=UTC)),
            (datetime(2026, 8, 20, tzinfo=UTC), datetime(2026, 8, 28, 4, 19, tzinfo=UTC)),
        ],
    )
    def test_full_moons_match_published_times(self, probe, published):
        full = next(when for name, when in next_moon_phases(probe) if name == "Full Moon")
        assert minutes_apart(full, published) < 5

    def test_the_previous_new_moon_is_always_in_the_past(self):
        """The lunation number is only approximated, then walked to the right
        one. An off-by-one there would put the 'last' new moon in the future
        and make the reported age negative.

        The upper bound is 29.9 rather than the 29.53-day mean because real
        lunations run from about 29.27 to 29.83 days - a bound set at the mean
        fails here roughly once a year, on a genuinely long month.
        """
        moment = datetime(2026, 1, 1, tzinfo=UTC)
        while moment < datetime(2028, 1, 1, tzinfo=UTC):
            _, found = previous_new_moon(moment)
            age = (moment - found).total_seconds() / 86400.0
            assert 0 <= age < 29.9, f"age {age} at {moment}"
            moment += timedelta(days=3)

    def test_the_phase_fraction_stays_inside_one_lunation(self):
        """Dividing the age by the *mean* lunation overshoots 1.0 near the end
        of a long month, which reads a waning crescent as a waxing one."""
        moment = datetime(2026, 1, 1, tzinfo=UTC)
        while moment < datetime(2028, 1, 1, tzinfo=UTC):
            assert 0.0 <= moon_phase(moment)["age_days"] < 29.9
            moment += timedelta(hours=13)

    def test_the_next_phases_are_one_of_each_in_time_order(self):
        found = next_moon_phases(datetime(2026, 8, 20, tzinfo=UTC))
        assert sorted(name for name, _ in found) == ["Full Moon", "New Moon"]
        assert found[0][1] < found[1][1]


class TestMoonPhase:
    def test_a_new_moon_reads_as_new_and_dark(self):
        phase = moon_phase(datetime(2026, 8, 12, 17, 37, tzinfo=UTC))
        assert phase["phase"] == "New Moon"
        assert phase["illumination"] < 0.01

    def test_a_full_moon_reads_as_full_and_lit(self):
        phase = moon_phase(datetime(2026, 8, 28, 4, 19, tzinfo=UTC))
        assert phase["phase"] == "Full Moon"
        assert phase["illumination"] > 0.99

    def test_the_first_quarter_is_half_lit(self):
        """Illumination follows the phase angle, not the age. Reporting the
        age fraction directly would make a first quarter 25% lit."""
        phase = moon_phase(datetime(2026, 8, 20, 12, tzinfo=UTC))
        assert phase["phase"] == "First Quarter"
        assert 0.45 < phase["illumination"] < 0.6

    def test_the_lit_limb_is_reported_for_the_hemisphere(self):
        """A waxing crescent is lit on the right from Seattle and on the left
        from Sydney. The panel draws the disc, so it needs both facts: how
        much is lit, and which side."""
        waxing = datetime(2026, 8, 16, tzinfo=UTC)
        northern = moon_phase(waxing, 47.6)
        southern = moon_phase(waxing, -33.9)

        assert northern["waxing"] is True
        assert northern["southern"] is False
        assert southern["southern"] is True
        # The same moon: only the side that reads as lit differs.
        assert northern["illumination"] == southern["illumination"]
        assert northern["phase"] == southern["phase"]

    def test_waxing_flips_after_the_full_moon(self):
        """Waxing and waning are equally illuminated and mirror images, so
        illumination alone cannot tell the drawing which way round to go."""
        assert moon_phase(datetime(2026, 8, 20, tzinfo=UTC))["waxing"] is True
        assert moon_phase(datetime(2026, 9, 2, tzinfo=UTC))["waxing"] is False

    def test_illumination_never_leaves_its_range(self):
        moment = datetime(2026, 1, 1, tzinfo=UTC)
        while moment < datetime(2026, 3, 1, tzinfo=UTC):
            assert 0.0 <= moon_phase(moment)["illumination"] <= 1.0
            moment += timedelta(hours=7)


class TestSeasons:
    @pytest.mark.parametrize(
        "published",
        [
            datetime(2026, 3, 20, 14, 46, tzinfo=UTC),
            datetime(2026, 6, 21, 8, 24, tzinfo=UTC),
            datetime(2026, 9, 23, 0, 5, tzinfo=UTC),
            datetime(2026, 12, 21, 20, 50, tzinfo=UTC),
        ],
    )
    def test_each_2026_season_lands_within_the_hour(self, published):
        window = seasons_between(published - timedelta(days=2), published + timedelta(days=2), 40.0)
        assert len(window) == 1
        assert minutes_apart(window[0][1], published) < 60

    def test_seasons_are_named_for_the_hemisphere(self):
        june = datetime(2026, 6, 21, 8, 24, tzinfo=UTC)
        start, end = june - timedelta(days=2), june + timedelta(days=2)
        assert seasons_between(start, end, 40.0)[0][0] == "Summer Solstice"
        assert seasons_between(start, end, -33.9)[0][0] == "Winter Solstice"

    def test_a_window_spanning_new_year_finds_januarys_events(self):
        """The search runs per calendar year, so a window crossing December 31
        has to look at both or it silently returns nothing."""
        found = seasons_between(
            datetime(2026, 12, 15, tzinfo=UTC), datetime(2027, 1, 15, tzinfo=UTC), 40.0
        )
        assert [name for name, _ in found] == ["Winter Solstice"]


class TestMeteorShowers:
    def test_the_perseids_are_invisible_from_sydney(self):
        """Radiant declination +58 never rises at latitude -34. Listing it
        would send somebody outside to look at nothing."""
        assert radiant_max_altitude(58.0, -33.9) < 0
        found = meteor_showers_between(date(2026, 8, 1), date(2026, 8, 31), -33.9)
        assert found == []

    def test_the_perseids_are_visible_from_california(self):
        found = meteor_showers_between(date(2026, 8, 1), date(2026, 8, 31), 37.7)
        assert [shower.name for shower, _ in found] == ["Perseids"]

    def test_an_equatorial_radiant_is_visible_from_both_hemispheres(self):
        may = (date(2026, 5, 1), date(2026, 5, 31))
        for latitude in (37.7, -33.9):
            found = meteor_showers_between(*may, latitude)
            assert [shower.name for shower, _ in found] == ["Eta Aquariids"]

    def test_a_window_spanning_new_year_finds_the_quadrantids(self):
        found = meteor_showers_between(date(2026, 12, 28), date(2027, 1, 18), 37.7)
        assert [shower.name for shower, _ in found] == ["Quadrantids"]

    def test_every_shower_in_the_table_is_a_real_date(self):
        for shower in METEOR_SHOWERS:
            date(2026, shower.month, shower.day)


class TestSkyEvents:
    def test_events_come_back_in_date_order(self):
        events = sky_events(datetime(2026, 12, 5, tzinfo=UTC), 37.7, PACIFIC)
        assert [e["date"] for e in events] == sorted(e["date"] for e in events)

    def test_nothing_lands_outside_the_lookahead(self):
        now = datetime(2026, 12, 5, tzinfo=UTC)
        horizon = (now + timedelta(days=21)).date().isoformat()
        for event in sky_events(now, 37.7, PACIFIC):
            assert now.date().isoformat() <= event["date"] <= horizon

    def test_a_shower_under_a_bright_moon_says_so(self):
        """The Ursids peak on 22 December 2026 with the Moon two days off
        full. Announcing a 10-per-hour shower into a washed-out sky without
        saying so is how a panel loses trust."""
        events = sky_events(datetime(2026, 12, 15, tzinfo=UTC), 37.7, PACIFIC)
        ursids = next(e for e in events if e["name"] == "Ursids")
        assert "washed out" in ursids["detail"]

    def test_the_geminids_are_not_marked_washed_out(self):
        """Same fortnight, six days after new moon - the check has to be
        judging the sky at each peak rather than the sky tonight."""
        events = sky_events(datetime(2026, 12, 5, tzinfo=UTC), 37.7, PACIFIC)
        geminids = next(e for e in events if e["name"] == "Geminids")
        assert "washed out" not in geminids["detail"]

    def test_dates_are_local_not_utc(self):
        """A full moon at 16:00 Pacific is the 27th there and the 28th in UTC.
        The panel plans evenings in local time."""
        events = sky_events(datetime(2026, 8, 20, tzinfo=UTC), 37.7, PACIFIC)
        full = next(e for e in events if e["name"] == "Full Moon")
        assert full["date"] == "2026-08-27"

    def test_a_summary_carries_the_moon_and_the_events(self):
        summary = astro_summary(datetime(2026, 12, 5, tzinfo=UTC), 37.7, PACIFIC)
        assert summary["moon"]["phase"] in {
            "New Moon",
            "Waxing Crescent",
            "First Quarter",
            "Waxing Gibbous",
            "Full Moon",
            "Waning Gibbous",
            "Last Quarter",
            "Waning Crescent",
        }
        assert any(event["kind"] == "meteor_shower" for event in summary["events"])
