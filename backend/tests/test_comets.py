"""Comet orbits, and the MPC element file they come from.

Two things are being guarded here, and they fail in different ways.

The **orbital mechanics** is checked against facts that are true by
definition - a comet is at its perihelion distance at perihelion, an ellipse
reaches aphelion half a period later, the geocentric distance obeys the
triangle inequality - rather than against a copied ephemeris table, which
would only prove that two numbers were transcribed the same way. The strongest
of them feeds Earth's own orbit in as a comet: if the propagator, the plane
rotation and the Earth-position code all agree, the answer must come out at
zero distance.

The **parser** is checked against fixture lines assembled from the MPC's
documented column positions. Building the fixture by column rather than by
copying a real line is the point: a real line proves the parser matches
whatever that line happened to be, while this proves it matches the format.
"""

import math
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.astro import julian_day
from app.comets import (
    Comet,
    heliocentric,
    load_comet_elements,
    magnitude,
    observed,
    parse_comet_elements,
    parse_comet_line,
    refresh_comet_elements,
    true_anomaly_and_radius,
    visible_comets,
)

UTC = timezone.utc
PACIFIC = ZoneInfo("America/Los_Angeles")
SAN_FRANCISCO = (37.77, -122.42)

PERIHELION = datetime(2026, 1, 1, tzinfo=UTC)


def comet(**overrides) -> Comet:
    base = dict(
        name="C/2026 T1 (Test)",
        perihelion_distance=1.0,
        eccentricity=0.5,
        perihelion=PERIHELION,
        argument_of_perihelion=0.0,
        ascending_node=0.0,
        inclination=0.0,
        absolute_magnitude=5.0,
        slope=4.0,
    )
    base.update(overrides)
    return Comet(**base)


class TestOrbitalMotion:
    @pytest.mark.parametrize(
        "eccentricity",
        [
            0.0,  # circle
            0.5,  # ellipse
            0.967,  # Halley
            0.9999,  # very long period, still bound
            1.0,  # parabola
            1.0005,  # the near-parabolic band most bright comets fall in
            1.5,  # hyperbolic, unbound
        ],
    )
    def test_a_comet_is_at_its_perihelion_distance_at_perihelion(self, eccentricity):
        """True for every conic section, and the sharpest single check on all
        three solvers: it catches a wrong Gaussian constant, a sign error in
        the anomaly, and a mis-derived semi-major axis at once."""
        subject = comet(eccentricity=eccentricity, perihelion_distance=0.587)
        nu, r = true_anomaly_and_radius(subject, julian_day(PERIHELION))

        assert r == pytest.approx(0.587, abs=1e-9)
        assert math.degrees(nu) == pytest.approx(0.0, abs=1e-6)

    def test_an_ellipse_reaches_aphelion_half_a_period_later(self):
        """Kepler's third law and the ellipse geometry have to agree. Halley's
        numbers are used because its 76-year period is widely known, so a
        result of 20 or 200 years would be obviously wrong."""
        q, e = 0.5871, 0.96714
        semi_major = q / (1 - e)
        period_days = semi_major**1.5 * 365.25

        halley = comet(perihelion_distance=q, eccentricity=e)
        _, r = true_anomaly_and_radius(
            halley, julian_day(PERIHELION + timedelta(days=period_days / 2))
        )

        assert period_days / 365.25 == pytest.approx(75.5, abs=1.0)
        assert r == pytest.approx(semi_major * (1 + e), rel=1e-6)

    def test_a_bound_orbit_returns_to_perihelion_after_one_period(self):
        q, e = 1.0, 0.6
        period_days = (q / (1 - e)) ** 1.5 * 365.25
        subject = comet(perihelion_distance=q, eccentricity=e)

        _, r = true_anomaly_and_radius(
            subject, julian_day(PERIHELION + timedelta(days=period_days))
        )
        assert r == pytest.approx(q, rel=1e-6)

    def test_the_orbital_plane_rotation_respects_inclination(self):
        """A comet in the ecliptic never leaves it; one at 90 degrees reaches
        its full distance out of it. A transposed sine and cosine in the
        rotation passes neither."""
        flat = heliocentric(comet(inclination=0.0), julian_day(PERIHELION + timedelta(days=40)))
        assert flat[2] == pytest.approx(0.0, abs=1e-12)

        polar = comet(inclination=90.0, argument_of_perihelion=90.0)
        _, r = true_anomaly_and_radius(polar, julian_day(PERIHELION))
        assert heliocentric(polar, julian_day(PERIHELION))[2] == pytest.approx(r, rel=1e-9)

    def test_geocentric_distance_obeys_the_triangle_inequality(self):
        """Earth is about 1 AU from the Sun, so a comet r AU out must lie
        between r-1 and r+1 from us. Nothing anywhere in the chain can be
        badly wrong and still satisfy this at every point of an orbit."""
        subject = comet(eccentricity=0.9, perihelion_distance=0.5, inclination=30.0)
        for days in range(0, 2000, 37):
            _, _, earth_distance, sun_distance = observed(
                subject, julian_day(PERIHELION + timedelta(days=days))
            )
            assert abs(sun_distance - 1.02) - 1e-6 <= earth_distance
            assert earth_distance <= sun_distance + 1.02 + 1e-6

    def test_coordinates_stay_on_the_sphere(self):
        subject = comet(eccentricity=1.0, perihelion_distance=0.8, inclination=120.0)
        for days in range(-400, 400, 29):
            ra, dec, _, _ = observed(subject, julian_day(PERIHELION + timedelta(days=days)))
            assert 0 <= ra < 360
            assert -90 <= dec <= 90

    def test_earths_own_orbit_comes_out_where_earth_is(self):
        """The end-to-end check.

        Feeding Earth's elements in as a comet must put it exactly where the
        observer already is, so the geocentric distance collapses to zero.
        That can only happen if the Kepler solver, the orbital-plane rotation
        and the independently derived Earth position all agree - and it fails
        loudly for a sign error in any one of them.

        The residual is a few thousandths of an AU, which is the accuracy of
        the round elements used here and of the low-precision solar formula,
        not of the machinery under test.
        """
        earth = comet(
            perihelion_distance=0.98329,
            eccentricity=0.01671,
            inclination=0.0,
            ascending_node=0.0,
            argument_of_perihelion=102.9,
            perihelion=datetime(2026, 1, 3, tzinfo=UTC),
        )
        for days in range(0, 365, 10):
            _, _, earth_distance, _ = observed(
                earth, julian_day(datetime(2026, 1, 3, tzinfo=UTC) + timedelta(days=days))
            )
            assert earth_distance < 0.02, f"{earth_distance} AU adrift after {days} days"


class TestMagnitude:
    def test_the_absolute_magnitude_is_what_you_see_at_one_au(self):
        """m = H + 5 log(delta) + 2.5 K log(r), so both logs vanish at 1 AU.
        A factor dropped from either term still passes every position test."""
        assert magnitude(comet(absolute_magnitude=7.3), 1.0, 1.0) == pytest.approx(7.3)

    def test_distance_dims_a_comet(self):
        subject = comet(absolute_magnitude=5.0)
        near = magnitude(subject, 1.0, 1.0)
        far = magnitude(subject, 4.0, 4.0)
        assert far > near

    def test_a_degenerate_distance_is_infinitely_faint_rather_than_an_error(self):
        """Guards the filter: a wild orbit must drop out of the list, not take
        the request down with a domain error."""
        assert magnitude(comet(), 0.0, 1.0) == math.inf


# --- the MPC's fixed-column format -------------------------------------------
#
# "Format For Cometary Orbits", 1-indexed columns as the MPC documents them.
_FIELDS = {
    "number": (1, 4),
    "orbit_type": (5, 5),
    "designation": (6, 12),
    "year": (15, 18),
    "month": (20, 21),
    "day": (23, 29),
    "perihelion_distance": (31, 39),
    "eccentricity": (42, 49),
    "argument_of_perihelion": (52, 59),
    "ascending_node": (62, 69),
    "inclination": (72, 79),
    "epoch": (81, 88),
    "absolute_magnitude": (92, 95),
    "slope": (97, 100),
    "name": (103, 158),
    "reference": (160, 168),
}


def mpc_line(**values) -> str:
    """One CometEls.txt record, laid out by documented column position.

    Assembled rather than copied on purpose: a pasted real line only proves
    the parser agrees with that line, while this proves it agrees with the
    published format.
    """
    line = [" "] * 168
    for field, value in values.items():
        start, end = _FIELDS[field]
        text = str(value)
        width = end - start + 1
        assert len(text) <= width, f"{field}={text!r} overflows {width} columns"
        # Names are left-aligned, numbers right-aligned, as in the real file.
        placed = text.ljust(width) if field in ("name", "designation") else text.rjust(width)
        line[start - 1 : end] = list(placed)
    return "".join(line)


HALLEY = mpc_line(
    number="0001",
    orbit_type="P",
    year="1986",
    month="02",
    day=" 9.4589",
    perihelion_distance=" 0.587104",
    eccentricity="0.967276",
    argument_of_perihelion="111.8657",
    ascending_node=" 58.4201",
    inclination="162.2621",
    epoch="19860219",
    absolute_magnitude=" 4.0",
    slope=" 6.0",
    name="1P/Halley",
    reference="MPC 26255",
)


class TestElementParsing:
    def test_every_field_lands_in_the_right_column(self):
        parsed = parse_comet_line(HALLEY)

        assert parsed is not None
        assert parsed.name == "1P/Halley"
        assert parsed.perihelion_distance == pytest.approx(0.587104)
        assert parsed.eccentricity == pytest.approx(0.967276)
        assert parsed.argument_of_perihelion == pytest.approx(111.8657)
        assert parsed.ascending_node == pytest.approx(58.4201)
        assert parsed.inclination == pytest.approx(162.2621)
        assert parsed.absolute_magnitude == pytest.approx(4.0)
        assert parsed.slope == pytest.approx(6.0)

    def test_the_fractional_perihelion_day_is_a_time_of_day(self):
        """The column counts from day 1, so 9.4589 is the 9th at about 11am -
        not the 10th, and not midnight on the 9th."""
        parsed = parse_comet_line(HALLEY)

        assert parsed is not None
        assert parsed.perihelion.date() == date(1986, 2, 9)
        assert parsed.perihelion.hour == 11

    def test_a_short_or_ragged_line_is_skipped_not_raised(self):
        """One bad record must never cost the panel the whole file."""
        assert parse_comet_line("") is None
        assert parse_comet_line("not a comet") is None
        assert parse_comet_line("x" * 200) is None

    def test_an_impossible_orbit_is_rejected(self):
        """The range checks are what turn a shifted column into 'no comets'
        rather than into a comet at a nonsensical distance."""
        assert parse_comet_line(mpc_line(**{**_halley_values(), "inclination": "999.0000"})) is None
        assert (
            parse_comet_line(mpc_line(**{**_halley_values(), "perihelion_distance": " 0.000000"}))
            is None
        )

    def test_a_file_keeps_the_good_lines_and_drops_the_bad(self):
        text = "\n".join(["", "garbage", HALLEY, "x" * 20, HALLEY])
        assert len(parse_comet_elements(text)) == 2

    def test_an_empty_file_is_simply_no_comets(self):
        assert parse_comet_elements("") == []


def _halley_values() -> dict:
    return dict(
        number="0001",
        orbit_type="P",
        year="1986",
        month="02",
        day=" 9.4589",
        perihelion_distance=" 0.587104",
        eccentricity="0.967276",
        argument_of_perihelion="111.8657",
        ascending_node=" 58.4201",
        inclination="162.2621",
        epoch="19860219",
        absolute_magnitude=" 4.0",
        slope=" 6.0",
        name="1P/Halley",
        reference="MPC 26255",
    )


class TestRefreshAndCache:
    def test_a_successful_fetch_is_cached_and_reloadable(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.comets.elements_path", lambda: tmp_path / "CometEls.txt")

        assert refresh_comet_elements(get=lambda url: _Response(HALLEY)) is True
        assert [c.name for c in load_comet_elements()] == ["1P/Halley"]

    def test_a_failed_fetch_leaves_the_last_good_copy_alone(self, tmp_path, monkeypatch):
        """The MPC being down on a Saturday must not empty the panel. This is
        the whole reason the elements are cached on disk rather than held in
        memory."""
        monkeypatch.setattr("app.comets.elements_path", lambda: tmp_path / "CometEls.txt")
        refresh_comet_elements(get=lambda url: _Response(HALLEY))

        def explode(url):
            raise OSError("connection refused")

        assert refresh_comet_elements(get=explode) is False
        assert [c.name for c in load_comet_elements()] == ["1P/Halley"]

    def test_an_unparseable_response_is_refused_rather_than_cached(self, tmp_path, monkeypatch):
        """A redirect to an HTML error page parses to zero orbits. Writing
        that over a good file would turn one bad day into a permanent
        outage."""
        monkeypatch.setattr("app.comets.elements_path", lambda: tmp_path / "CometEls.txt")
        refresh_comet_elements(get=lambda url: _Response(HALLEY))

        assert refresh_comet_elements(get=lambda url: _Response("<html>503</html>")) is False
        assert [c.name for c in load_comet_elements()] == ["1P/Halley"]

    def test_no_cache_yet_is_no_comets(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.comets.elements_path", lambda: tmp_path / "CometEls.txt")
        assert load_comet_elements() == []


class _Response:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


class TestVisibleComets:
    # A geometry that genuinely puts the comet in California's evening sky on
    # this date, checked before being written down. Picking the elements at
    # random gives a comet below the horizon all night, which makes every
    # assertion below pass without testing anything.
    EVENING = dict(
        perihelion_distance=0.9,
        eccentricity=1.0,
        inclination=60.0,
        ascending_node=270.0,
        argument_of_perihelion=180.0,
        perihelion=datetime(2026, 3, 20, tzinfo=UTC),
    )
    WHEN = datetime(2026, 3, 20, tzinfo=UTC)

    def test_a_faint_comet_is_left_off_the_panel(self):
        """There are always a thousand comets in the file and essentially
        never one worth seeing. Listing them all is the same as listing
        none."""
        faint = comet(**{**self.EVENING, "absolute_magnitude": 22.0})
        assert visible_comets([faint], self.WHEN, *SAN_FRANCISCO, PACIFIC) == []

    def test_a_bright_comet_is_reported_with_where_and_when(self):
        bright = comet(name="C/2026 B1 (Bright)", **{**self.EVENING, "absolute_magnitude": 2.0})

        found = visible_comets([bright], self.WHEN, *SAN_FRANCISCO, PACIFIC)

        assert len(found) == 1
        entry = found[0]
        assert entry["kind"] == "comet"
        assert entry["name"] == "C/2026 B1 (Bright)"
        assert entry["date"] == "2026-03-19"  # the local evening, not the UTC date
        assert entry["magnitude"] == pytest.approx(2.2, abs=0.3)
        assert "mag 2" in entry["detail"]
        assert "up" in entry["detail"]

    def test_a_comet_below_the_horizon_all_night_is_not_listed(self):
        """Bright is not enough. A comet on the far side of the sky is exactly
        as useful as no comet, and saying otherwise sends somebody outside for
        nothing."""
        hidden = comet(
            **{
                **self.EVENING,
                "ascending_node": 0.0,
                "argument_of_perihelion": 0.0,
                "inclination": 20.0,
                "absolute_magnitude": -5.0,
            }
        )
        assert visible_comets([hidden], self.WHEN, *SAN_FRANCISCO, PACIFIC) == []

    def test_the_magnitude_limit_is_honoured(self):
        subject = comet(**{**self.EVENING, "absolute_magnitude": 2.0})

        generous = visible_comets(
            [subject], self.WHEN, *SAN_FRANCISCO, PACIFIC, magnitude_limit=25.0
        )
        strict = visible_comets(
            [subject], self.WHEN, *SAN_FRANCISCO, PACIFIC, magnitude_limit=0.0
        )

        assert len(generous) == 1
        assert strict == []

    def test_the_brightest_comet_comes_first(self):
        """If two are up at once, the panel should point at the better one."""
        dim = comet(name="Dimmer", **{**self.EVENING, "absolute_magnitude": 6.0})
        bright = comet(name="Brighter", **{**self.EVENING, "absolute_magnitude": 1.0})

        found = visible_comets(
            [dim, bright], self.WHEN, *SAN_FRANCISCO, PACIFIC, magnitude_limit=30.0
        )

        assert [entry["name"] for entry in found] == ["Brighter", "Dimmer"]

    def test_a_wild_orbit_drops_out_rather_than_taking_the_page_down(self):
        """A single corrupt record must not turn /api/weather into a 500."""
        wild = comet(**{**self.EVENING, "perihelion_distance": 1e-12, "eccentricity": 1.0})
        good = comet(name="Fine", **{**self.EVENING, "absolute_magnitude": 2.0})

        found = visible_comets([wild, good], self.WHEN, *SAN_FRANCISCO, PACIFIC)

        assert "Fine" in [entry["name"] for entry in found]

    def test_an_empty_element_file_is_simply_no_comets(self):
        assert visible_comets([], self.WHEN, *SAN_FRANCISCO, PACIFIC) == []
