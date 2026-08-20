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
    BRIGHT_MOON,
    METEOR_SHOWERS,
    altitude_of,
    astro_summary,
    julian_day,
    meteor_showers_between,
    moon_equatorial,
    moon_phase,
    next_moon_phases,
    previous_new_moon,
    seasons_between,
    shower_viewing,
    sky_events,
    sun_equatorial,
)

UTC = timezone.utc
PACIFIC = ZoneInfo("America/Los_Angeles")
SYDNEY = ZoneInfo("Australia/Sydney")
REYKJAVIK = ZoneInfo("Atlantic/Reykjavik")

# (latitude, longitude), east positive.
SAN_FRANCISCO = (37.77, -122.42)
SYDNEY_AT = (-33.87, 151.21)
REYKJAVIK_AT = (64.13, -21.90)


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


class TestSkyPositions:
    """The positional astronomy the local answers are built on.

    Checked against facts that are true by definition rather than a published
    table, because those cannot go stale and cannot be fudged.
    """

    @pytest.mark.parametrize(
        "moment, expected_ra, expected_dec",
        [
            (datetime(2026, 3, 20, 14, 46, tzinfo=UTC), 0.0, 0.0),
            (datetime(2026, 6, 21, 8, 24, tzinfo=UTC), 90.0, 23.44),
            (datetime(2026, 9, 23, 0, 5, tzinfo=UTC), 180.0, 0.0),
            (datetime(2026, 12, 21, 20, 50, tzinfo=UTC), 270.0, -23.44),
        ],
    )
    def test_the_sun_sits_where_each_season_defines_it(self, moment, expected_ra, expected_dec):
        """An equinox *is* the Sun at right ascension 0; a solstice is 90 or
        270. The season code and the position code share nothing, so agreeing
        here means both are right."""
        ra, dec = sun_equatorial(julian_day(moment))
        assert abs(((ra - expected_ra + 180) % 360) - 180) < 0.1
        assert abs(dec - expected_dec) < 0.1

    def test_the_sun_reaches_the_altitude_geometry_demands(self):
        """At the December solstice the Sun's noon altitude is exactly
        90 - latitude - 23.44. A sign error in the hour angle, or a sidereal
        time that drifts, would miss this."""
        latitude, longitude = SAN_FRANCISCO
        noon = datetime(2026, 12, 21, 20, tzinfo=UTC)  # about solar noon in California
        altitude = altitude_of(*sun_equatorial(julian_day(noon)), julian_day(noon), latitude, longitude)
        assert abs(altitude - (90 - latitude - 23.44)) < 1.0

    def test_the_full_moon_is_opposite_the_sun(self):
        """Full means opposite, by definition - which checks the lunar
        position series against the ch. 49 phase code, derived independently
        of it."""
        full = datetime(2026, 8, 28, 4, 19, tzinfo=UTC)
        sun_ra, _ = sun_equatorial(julian_day(full))
        moon_ra, _ = moon_equatorial(julian_day(full))
        assert abs(((moon_ra - sun_ra + 180) % 360) - 180) > 178

    def test_the_new_moon_is_alongside_the_sun(self):
        new = datetime(2026, 8, 12, 17, 37, tzinfo=UTC)
        sun_ra, _ = sun_equatorial(julian_day(new))
        moon_ra, _ = moon_equatorial(julian_day(new))
        assert abs(((moon_ra - sun_ra + 180) % 360) - 180) < 2


class TestMeteorShowers:
    def test_the_perseids_never_rise_in_sydney(self):
        """Radiant declination +58 never clears the horizon at latitude -34.
        Listing it would send somebody outside to look at nothing."""
        assert meteor_showers_between(
            date(2026, 8, 1), date(2026, 8, 31), *SYDNEY_AT, SYDNEY
        ) == []

    def test_the_perseids_are_high_and_late_from_california(self):
        found = meteor_showers_between(
            date(2026, 8, 1), date(2026, 8, 31), *SAN_FRANCISCO, PACIFIC
        )
        assert [shower.name for shower, _, _ in found] == ["Perseids"]
        _, _, viewing = found[0]
        # The radiant climbs all night, so the best moment is in the small
        # hours rather than the evening.
        assert viewing.best_at.hour < 7
        assert viewing.altitude > 50

    def test_an_arctic_summer_night_is_never_dark_enough(self):
        """The old latitude-only test put the Perseids on a panel in
        Reykjavik, because it never asked whether the radiant was up at a time
        anyone could see it. In August there the sky never gets dark at all."""
        assert meteor_showers_between(
            date(2026, 8, 1), date(2026, 8, 31), *REYKJAVIK_AT, REYKJAVIK
        ) == []

    def test_the_geminids_are_visible_but_low_from_sydney(self):
        """Latitude decides how good, not only whether. Declination +32 seen
        from latitude -34 tops out low, and saying so is the difference
        between useful and merely true."""
        northern = meteor_showers_between(
            date(2026, 12, 10), date(2026, 12, 18), *SAN_FRANCISCO, PACIFIC
        )
        southern = meteor_showers_between(
            date(2026, 12, 10), date(2026, 12, 18), *SYDNEY_AT, SYDNEY
        )
        assert [s.name for s, _, _ in northern] == ["Geminids"]
        assert [s.name for s, _, _ in southern] == ["Geminids"]
        assert northern[0][2].altitude > 80
        assert southern[0][2].altitude < 30

    def test_longitude_changes_when_not_whether(self):
        """Two places on the same parallel see a shower equally high but at
        different clock times. That is what longitude is for, and what a
        latitude-only model cannot express at all."""
        geminids = next(s for s in METEOR_SHOWERS if s.name == "Geminids")
        west = shower_viewing(geminids, date(2026, 12, 14), 40.0, -120.0, PACIFIC)
        east = shower_viewing(geminids, date(2026, 12, 14), 40.0, 0.0, ZoneInfo("UTC"))

        assert west is not None and east is not None
        assert abs(west.altitude - east.altitude) < 3
        # Same wall-clock hour locally, eight hours apart as instants.
        assert abs((west.best_at - east.best_at).total_seconds()) > 7 * 3600

    def test_the_draconids_are_an_evening_shower(self):
        """Almost every shower is best before dawn, because that is when your
        side of the Earth faces into its orbit. The Draconids are the famous
        exception - a circumpolar radiant highest at dusk - and getting that
        right is the clearest sign the answer comes from the sky rather than
        from a rule of thumb about early mornings.
        """
        draconids = next(s for s in METEOR_SHOWERS if s.name == "Draconids")
        viewing = shower_viewing(draconids, date(2026, 10, 8), *SAN_FRANCISCO, PACIFIC)

        assert viewing is not None
        assert viewing.best_at.hour >= 18

    def test_a_shower_under_a_risen_bright_moon_is_flagged(self):
        ursids = next(s for s in METEOR_SHOWERS if s.name == "Ursids")
        viewing = shower_viewing(ursids, date(2026, 12, 22), *SAN_FRANCISCO, PACIFIC)

        assert viewing is not None
        assert viewing.moon_illumination > BRIGHT_MOON
        assert viewing.moon_altitude > 0
        assert viewing.moonlit is True

    def test_an_equatorial_radiant_is_visible_from_both_hemispheres(self):
        may = (date(2026, 5, 1), date(2026, 5, 31))
        north = meteor_showers_between(*may, *SAN_FRANCISCO, PACIFIC)
        south = meteor_showers_between(*may, *SYDNEY_AT, SYDNEY)
        assert "Eta Aquariids" in [s.name for s, _, _ in north]
        assert "Eta Aquariids" in [s.name for s, _, _ in south]

    def test_a_window_spanning_new_year_finds_the_quadrantids(self):
        found = meteor_showers_between(
            date(2026, 12, 28), date(2027, 1, 18), *SAN_FRANCISCO, PACIFIC
        )
        assert [s.name for s, _, _ in found] == ["Quadrantids"]

    def test_every_shower_in_the_table_is_a_real_date_and_position(self):
        for shower in METEOR_SHOWERS:
            date(2026, shower.month, shower.day)
            assert 0 <= shower.right_ascension < 360, shower.name
            assert -90 <= shower.declination <= 90, shower.name
            assert shower.zhr > 0, shower.name

    def test_the_whole_visual_working_list_is_present(self):
        """The showers a person can actually watch - not the IAU catalogue of
        several hundred, most of which were found by radar and produce a
        meteor an hour."""
        assert {s.name for s in METEOR_SHOWERS} == {
            "Quadrantids",
            "Lyrids",
            "Eta Aquariids",
            "Alpha Capricornids",
            "Southern Delta Aquariids",
            "Perseids",
            "Draconids",
            "Orionids",
            "Southern Taurids",
            "Northern Taurids",
            "Leonids",
            "Geminids",
            "Ursids",
        }


class TestSkyEvents:
    def test_events_come_back_in_date_order(self):
        events = sky_events(datetime(2026, 12, 5, tzinfo=UTC), *SAN_FRANCISCO, PACIFIC)
        assert [e["date"] for e in events] == sorted(e["date"] for e in events)

    def test_nothing_lands_outside_the_lookahead(self):
        now = datetime(2026, 12, 5, tzinfo=UTC)
        horizon = (now + timedelta(days=21)).date().isoformat()
        for event in sky_events(now, *SAN_FRANCISCO, PACIFIC):
            assert now.date().isoformat() <= event["date"] <= horizon

    def test_a_shower_under_a_bright_moon_says_so(self):
        """The Ursids peak on 22 December 2026 with the Moon two days off
        full and above the horizon. Announcing a 10-per-hour shower into a
        washed-out sky without saying so is how a panel loses trust."""
        events = sky_events(datetime(2026, 12, 15, tzinfo=UTC), *SAN_FRANCISCO, PACIFIC)
        ursids = next(e for e in events if e["name"] == "Ursids")
        assert "bright moon" in ursids["detail"]

    def test_the_geminids_are_not_marked_moonlit(self):
        """Same fortnight, six days after new moon - the check has to judge
        the sky at each peak rather than the sky tonight."""
        events = sky_events(datetime(2026, 12, 5, tzinfo=UTC), *SAN_FRANCISCO, PACIFIC)
        geminids = next(e for e in events if e["name"] == "Geminids")
        assert "bright moon" not in geminids["detail"]

    def test_a_shower_detail_says_when_and_how_high(self):
        """The part that needs coordinates. A date alone is a calendar; a
        time and an altitude are an instruction."""
        events = sky_events(datetime(2026, 12, 5, tzinfo=UTC), *SAN_FRANCISCO, PACIFIC)
        geminids = next(e for e in events if e["name"] == "Geminids")
        assert "best" in geminids["detail"]
        assert "up" in geminids["detail"]

    def test_dates_are_local_not_utc(self):
        """A full moon at 16:00 Pacific is the 27th there and the 28th in UTC.
        The panel plans evenings in local time."""
        events = sky_events(datetime(2026, 8, 20, tzinfo=UTC), *SAN_FRANCISCO, PACIFIC)
        full = next(e for e in events if e["name"] == "Full Moon")
        assert full["date"] == "2026-08-27"

    def test_a_summary_carries_the_moon_and_the_events(self):
        summary = astro_summary(datetime(2026, 12, 5, tzinfo=UTC), *SAN_FRANCISCO, PACIFIC)
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
