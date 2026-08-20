"""Moon phase and upcoming sky events for the panel's coordinates.

Computed rather than fetched. Open-Meteo has no moon or meteor data, and every
service that does would be one more thing that can be down, rate-limited, or
require a key - for numbers that are a few dozen lines of arithmetic and change
on nobody's schedule but the solar system's. Nothing in this module does I/O.

Accuracy targets, chosen for something read across a kitchen:

* Moon phase and the dates of new/full moon come from Meeus, *Astronomical
  Algorithms*, ch. 49, and land within a couple of minutes of the true
  instant. The naive "days since a known new moon, modulo 29.53" that this
  kind of code usually uses drifts up to about 14 hours either way, because
  the Moon's orbit is an ellipse - which is enough to print "Full moon" on the
  wrong day roughly a third of the time.
* Equinoxes and solstices use the mean expressions of ch. 27 without the
  periodic correction table. That is worth about 20 minutes, which cannot move
  the date unless the event falls within 20 minutes of local midnight.

Everything takes `now` as an argument - no function here reads the clock, so
tests pass an instant rather than patching time.
"""

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# Meeus 49.1: the new moon of 2000 January 6, and the mean length of a
# lunation. k counts lunations from that epoch, and is a half-integer at full
# moon.
NEW_MOON_EPOCH_JD = 2451550.09766
SYNODIC_MONTH = 29.530588861

UNIX_EPOCH_JD = 2440587.5

# Phase names only - no glyphs. The panel draws the disc itself (see
# MoonGlyph.svelte) and spells out the rest, because Raspberry Pi OS Lite ships
# no emoji font and every emoji would render as a tofu box on the actual wall
# panel. It also keeps this in step with the app's own design language, which
# is text and inline SVG with no icon font anywhere.
PHASE_NAMES = [
    "New Moon",
    "Waxing Crescent",
    "First Quarter",
    "Waxing Gibbous",
    "Full Moon",
    "Waning Gibbous",
    "Last Quarter",
    "Waning Crescent",
]


def julian_day(moment: datetime) -> float:
    """The Julian day number of an instant."""
    return moment.astimezone(timezone.utc).timestamp() / 86400.0 + UNIX_EPOCH_JD


def _from_julian_day(jd: float) -> datetime:
    return datetime.fromtimestamp((jd - UNIX_EPOCH_JD) * 86400.0, tz=timezone.utc)


def _sin(degrees: float) -> float:
    return math.sin(math.radians(degrees))


def _phase_jde(k: float) -> float:
    """The instant of the new (integer k) or full (k + 0.5) moon numbered k.

    Meeus ch. 49, carrying every periodic term above a minute. The corrections
    are what separate this from a mean-lunation estimate: the first alone,
    -0.407 sin M', is worth up to nine hours.
    """
    t = k / 1236.85
    jde = (
        NEW_MOON_EPOCH_JD
        + SYNODIC_MONTH * k
        + 0.00015437 * t**2
        - 0.000000150 * t**3
        + 0.00000000073 * t**4
    )

    # Eccentricity of the Earth's orbit, which slowly shrinks.
    e = 1 - 0.002516 * t - 0.0000074 * t**2
    # Sun's mean anomaly, Moon's mean anomaly, Moon's argument of latitude,
    # and the longitude of the ascending node.
    m = 2.5534 + 29.10535670 * k - 0.0000014 * t**2 - 0.00000011 * t**3
    mp = (
        201.5643
        + 385.81693528 * k
        + 0.0107582 * t**2
        + 0.00001238 * t**3
        - 0.000000058 * t**4
    )
    f = (
        160.7108
        + 390.67050284 * k
        - 0.0016118 * t**2
        - 0.00000227 * t**3
        + 0.000000011 * t**4
    )
    omega = 124.7746 - 1.56375588 * k + 0.0020672 * t**2 + 0.00000215 * t**3

    is_full = abs(k - math.floor(k) - 0.5) < 1e-9
    lead = -0.40614 if is_full else -0.40720
    solar = 0.17302 if is_full else 0.17241

    correction = (
        lead * _sin(mp)
        + solar * e * _sin(m)
        + 0.01614 * _sin(2 * mp)
        + 0.01043 * _sin(2 * f)
        + 0.00734 * e * _sin(mp - m)
        - 0.00515 * e * _sin(mp + m)
        + 0.00209 * e**2 * _sin(2 * m)
        - 0.00111 * _sin(mp - 2 * f)
        - 0.00057 * _sin(mp + 2 * f)
        + 0.00056 * e * _sin(2 * mp + m)
        - 0.00042 * _sin(3 * mp)
        + 0.00042 * e * _sin(m + 2 * f)
        + 0.00038 * e * _sin(m - 2 * f)
        - 0.00024 * e * _sin(2 * mp - m)
        - 0.00017 * _sin(omega)
        - 0.00007 * _sin(mp + 2 * m)
    )
    return jde + correction


def _approximate_k(moment: datetime) -> float:
    """Meeus 49.2: roughly which lunation an instant falls in."""
    reference = datetime(2000, 1, 1, tzinfo=timezone.utc)
    years = (moment - reference).total_seconds() / (365.25 * 86400.0)
    return years * 12.3685


def previous_new_moon(moment: datetime) -> tuple[float, datetime]:
    """The lunation number and instant of the last new moon before `moment`."""
    k = math.floor(_approximate_k(moment))
    # The approximation can land a lunation either side, so walk to the one
    # that actually brackets the instant rather than trusting it.
    while _phase_jde(k) > julian_day(moment):
        k -= 1
    while _phase_jde(k + 1) <= julian_day(moment):
        k += 1
    return k, _from_julian_day(_phase_jde(k))


def moon_phase(moment: datetime, latitude: float = 0.0) -> dict:
    """The Moon's phase now: age, illuminated fraction, name, and orientation."""
    k, last_new = previous_new_moon(moment)
    next_new = _from_julian_day(_phase_jde(k + 1))

    age_days = (moment - last_new).total_seconds() / 86400.0
    # Measured against *this* lunation rather than the mean one. Real lunations
    # run from about 29.27 to 29.83 days, so dividing by the 29.53-day average
    # can push the fraction past 1 near the end of a long month - reporting a
    # moon a few hours short of new as a few hours past it.
    lunation = (next_new - last_new).total_seconds() / 86400.0
    fraction = age_days / lunation

    # Illumination follows the phase angle, not the age, so it is a cosine
    # rather than a triangle: half a lunation in is full, not half-lit.
    illumination = (1 - math.cos(2 * math.pi * fraction)) / 2

    index = int(round(fraction * 8)) % 8

    return {
        "phase": PHASE_NAMES[index],
        "illumination": round(illumination, 3),
        "age_days": round(age_days, 1),
        # The two facts a drawing needs that illumination alone cannot give:
        # which side is lit. Waxing and waning are equally illuminated and are
        # mirror images of each other, and the whole picture flips again south
        # of the equator.
        "waxing": fraction < 0.5,
        "southern": latitude < 0,
    }


def next_moon_phases(moment: datetime) -> list[tuple[str, datetime]]:
    """The next new moon and the next full moon, in time order."""
    k, _ = previous_new_moon(moment)
    found: list[tuple[str, datetime]] = []
    # Two lunations is always enough to bracket one of each, whichever half of
    # the cycle we are currently in.
    for step in (0, 1, 2):
        for name, offset in (("New Moon", 0.0), ("Full Moon", 0.5)):
            when = _from_julian_day(_phase_jde(k + step + offset))
            if when > moment and not any(n == name for n, _ in found):
                found.append((name, when))
    return sorted(found, key=lambda item: item[1])


# --- where things are in *your* sky ------------------------------------------
#
# Everything above answers "when". This answers "where from here", which is
# what turns a date into something worth acting on: a shower whose radiant only
# clears the horizon at noon is not visible tonight however high it gets, and a
# fixed calendar date cannot tell you that. Latitude and longitude both matter -
# latitude sets how high the radiant climbs, longitude sets when.


def _cos(degrees: float) -> float:
    return math.cos(math.radians(degrees))


def _norm(degrees: float) -> float:
    return degrees % 360.0


def greenwich_sidereal_time(jd: float) -> float:
    """Greenwich mean sidereal time in degrees (Meeus 12.4).

    Sidereal time is the bridge between a clock and the sky: it says which
    right ascension is currently overhead at Greenwich, and adding the
    longitude moves that to here.
    """
    t = (jd - 2451545.0) / 36525.0
    return _norm(
        280.46061837
        + 360.98564736629 * (jd - 2451545.0)
        + 0.000387933 * t**2
        - t**3 / 38710000.0
    )


def altitude_of(ra: float, dec: float, jd: float, latitude: float, longitude: float) -> float:
    """How high a fixed point on the sky sits above the horizon, in degrees.

    Negative means below it. East longitude is positive, matching the
    convention Open-Meteo is already configured with.
    """
    hour_angle = _norm(greenwich_sidereal_time(jd) + longitude - ra)
    sin_alt = (
        _sin(dec) * _sin(latitude)
        + _cos(dec) * _cos(latitude) * _cos(hour_angle)
    )
    return math.degrees(math.asin(max(-1.0, min(1.0, sin_alt))))


OBLIQUITY = 23.4393


def _ecliptic_to_equatorial(longitude: float, latitude: float) -> tuple[float, float]:
    """Ecliptic coordinates to right ascension and declination, in degrees."""
    sin_dec = _sin(latitude) * _cos(OBLIQUITY) + _cos(latitude) * _sin(OBLIQUITY) * _sin(longitude)
    dec = math.degrees(math.asin(max(-1.0, min(1.0, sin_dec))))
    ra = math.degrees(
        math.atan2(
            _sin(longitude) * _cos(OBLIQUITY) - math.tan(math.radians(latitude)) * _sin(OBLIQUITY),
            _cos(longitude),
        )
    )
    return _norm(ra), dec


def sun_equatorial(jd: float) -> tuple[float, float]:
    """The Sun's right ascension and declination (Meeus ch. 25, low accuracy).

    Good to about 0.01 degrees, which is far better than deciding whether the
    sky is dark needs.
    """
    n = jd - 2451545.0
    mean_longitude = _norm(280.460 + 0.9856474 * n)
    anomaly = _norm(357.528 + 0.9856003 * n)
    ecliptic_longitude = _norm(
        mean_longitude + 1.915 * _sin(anomaly) + 0.020 * _sin(2 * anomaly)
    )
    return _ecliptic_to_equatorial(ecliptic_longitude, 0.0)


def moon_equatorial(jd: float) -> tuple[float, float]:
    """The Moon's right ascension and declination.

    The truncated lunar series - the handful of largest periodic terms, good
    to roughly a third of a degree. The full theory runs to hundreds of terms
    and buys nothing here: this only has to answer "is the Moon up, and how
    high", where a third of a degree is invisible.
    """
    t = (jd - 2451545.0) / 36525.0
    mean_longitude = 218.316 + 481267.8813 * t
    sun_anomaly = 357.529 + 35999.0503 * t
    moon_anomaly = 134.963 + 477198.8676 * t
    elongation = 297.850 + 445267.1115 * t
    argument_of_latitude = 93.272 + 483202.0175 * t

    ecliptic_longitude = _norm(
        mean_longitude
        + 6.289 * _sin(moon_anomaly)
        + 1.274 * _sin(2 * elongation - moon_anomaly)
        + 0.658 * _sin(2 * elongation)
        + 0.214 * _sin(2 * moon_anomaly)
        - 0.186 * _sin(sun_anomaly)
        - 0.114 * _sin(2 * argument_of_latitude)
    )
    ecliptic_latitude = (
        5.128 * _sin(argument_of_latitude)
        + 0.281 * _sin(moon_anomaly + argument_of_latitude)
        + 0.278 * _sin(moon_anomaly - argument_of_latitude)
        + 0.173 * _sin(2 * elongation - argument_of_latitude)
    )
    return _ecliptic_to_equatorial(ecliptic_longitude, ecliptic_latitude)


# Meeus ch. 27, table 27.5: mean equinoxes and solstices for 1000-3000. The
# name is the northern-hemisphere one; southern names are swapped below.
_SEASON_TERMS = {
    "March Equinox": (2451623.80984, 365242.37404, 0.05169, -0.00411, -0.00057),
    "June Solstice": (2451716.56767, 365241.62603, 0.00325, 0.00888, -0.00030),
    "September Equinox": (2451810.21715, 365242.01767, -0.11575, 0.00337, 0.00078),
    "December Solstice": (2451900.05952, 365242.74049, -0.06223, -0.00823, 0.00032),
}

# What each event means depending on which side of the equator you are on.
_SEASON_NAMES = {
    "March Equinox": ("Spring Equinox", "Autumn Equinox"),
    "June Solstice": ("Summer Solstice", "Winter Solstice"),
    "September Equinox": ("Autumn Equinox", "Spring Equinox"),
    "December Solstice": ("Winter Solstice", "Summer Solstice"),
}


def _season_instant(key: str, year: int) -> datetime:
    a, b, c, d, e = _SEASON_TERMS[key]
    y = (year - 2000) / 1000.0
    return _from_julian_day(a + b * y + c * y**2 + d * y**3 + e * y**4)


def seasons_between(start: datetime, end: datetime, latitude: float) -> list[tuple[str, datetime]]:
    """Equinoxes and solstices falling in a window, named for the hemisphere."""
    found: list[tuple[str, datetime]] = []
    for year in range(start.year, end.year + 1):
        for key in _SEASON_TERMS:
            when = _season_instant(key, year)
            if start < when <= end:
                northern, southern = _SEASON_NAMES[key]
                found.append((northern if latitude >= 0 else southern, when))
    return sorted(found, key=lambda item: item[1])


@dataclass(frozen=True)
class MeteorShower:
    name: str
    month: int
    day: int
    #: Radiant position, J2000. Declination sets how high it can climb from a
    #: given latitude; right ascension sets *when* it is up, which is why both
    #: are needed and why longitude matters as much as latitude.
    right_ascension: float
    declination: float
    #: Zenithal hourly rate at peak, under a dark sky with the radiant
    #: overhead. Real counts are always lower; it is a comparative number.
    zhr: int


# Every shower on the IMO working list that is worth going outside for, with
# its radiant position from that list.
#
# This is deliberately not "all of them". The IAU Meteor Data Center holds
# well over a hundred established showers and several hundred more on its
# working list, but the great majority were found by radar or camera networks
# and produce a meteor an hour or less - they are real, and invisible. The
# useful cut is the visual working list, which is what this is.
#
# The low-rate entries here earn their place for reasons a ZHR does not carry:
# the Taurids are slow, dramatic fireballs spread over weeks, the Alpha
# Capricornids are the brightest of the summer, and the Draconids are the one
# shower that occasionally storms.
METEOR_SHOWERS = [
    MeteorShower("Quadrantids", 1, 3, 230.1, 49.5, 110),
    MeteorShower("Lyrids", 4, 22, 271.4, 33.6, 18),
    MeteorShower("Eta Aquariids", 5, 6, 338.0, -1.0, 50),
    MeteorShower("Alpha Capricornids", 7, 30, 307.0, -10.0, 5),
    MeteorShower("Southern Delta Aquariids", 7, 30, 340.0, -16.4, 25),
    MeteorShower("Perseids", 8, 12, 46.2, 58.0, 100),
    MeteorShower("Draconids", 10, 8, 262.0, 54.0, 10),
    MeteorShower("Orionids", 10, 21, 95.2, 15.8, 20),
    MeteorShower("Southern Taurids", 11, 5, 52.0, 15.0, 5),
    MeteorShower("Northern Taurids", 11, 12, 58.0, 22.0, 5),
    MeteorShower("Leonids", 11, 17, 152.3, 21.6, 15),
    MeteorShower("Geminids", 12, 14, 112.3, 32.5, 150),
    MeteorShower("Ursids", 12, 22, 217.0, 75.4, 10),
]

#: Below this the radiant is too low for the shower to be worth announcing -
#: meteors near the horizon are few and dimmed by the thickness of air.
MIN_RADIANT_ALTITUDE = 15.0

#: The Sun this far below the horizon is dark enough to watch meteors.
#: Astronomical twilight (-18) is the textbook answer, but at high latitudes
#: the Sun never gets there for months, which would silently drop every summer
#: shower rather than admit the sky is merely good rather than perfect.
DARK_SUN_ALTITUDE = -12.0

#: Sampling interval across the night. Fifteen minutes is finer than any
#: advice that comes out of it - "best around 2am" - and keeps a whole night
#: to about fifty position calculations.
_NIGHT_STEP = timedelta(minutes=15)


@dataclass(frozen=True)
class Viewing:
    """When and how well a shower can actually be seen from one place."""

    #: Local time the radiant is highest while the sky is dark.
    best_at: datetime
    #: Its altitude then, in degrees.
    altitude: float
    #: The Moon's altitude and illuminated fraction at that moment. A bright
    #: Moon above the horizon is the difference between a good night and a
    #: wasted one, and it is the single most common reason a widely
    #: advertised shower disappoints.
    moon_altitude: float
    moon_illumination: float

    @property
    def moonlit(self) -> bool:
        return self.moon_altitude > 0 and self.moon_illumination >= BRIGHT_MOON


def shower_viewing(
    shower: MeteorShower, peak: date, latitude: float, longitude: float, tz: ZoneInfo
) -> Viewing | None:
    """The best moment to watch a shower from one place, or None if it never
    rises far enough into a dark sky there.

    This is the whole reason the panel needs coordinates rather than a
    calendar. A radiant that transits at noon is useless however high it
    climbs, and the old "90 - |latitude - declination|" test could not see
    that: it measured the best altitude over a whole day, including the half
    of it spent in daylight.

    The night is walked from evening to the following dawn, keeping the
    darkest-sky moment at which the radiant is highest.
    """
    start = datetime(peak.year, peak.month, peak.day, 12, tzinfo=tz)
    moment = start
    end = start + timedelta(hours=24)

    best: Viewing | None = None
    while moment < end:
        jd = julian_day(moment)
        sun_ra, sun_dec = sun_equatorial(jd)
        if altitude_of(sun_ra, sun_dec, jd, latitude, longitude) <= DARK_SUN_ALTITUDE:
            radiant = altitude_of(
                shower.right_ascension, shower.declination, jd, latitude, longitude
            )
            if best is None or radiant > best.altitude:
                moon_ra, moon_dec = moon_equatorial(jd)
                best = Viewing(
                    best_at=moment,
                    altitude=radiant,
                    moon_altitude=altitude_of(moon_ra, moon_dec, jd, latitude, longitude),
                    moon_illumination=moon_phase(moment, latitude)["illumination"],
                )
        moment += _NIGHT_STEP

    if best is None or best.altitude < MIN_RADIANT_ALTITUDE:
        return None
    return best


def meteor_showers_between(
    start: date, end: date, latitude: float, longitude: float, tz: ZoneInfo
) -> list[tuple[MeteorShower, date, Viewing]]:
    """Shower peaks in a date window that can actually be seen from here."""
    found: list[tuple[MeteorShower, date, Viewing]] = []
    for year in range(start.year, end.year + 1):
        for shower in METEOR_SHOWERS:
            peak = date(year, shower.month, shower.day)
            if not (start <= peak <= end):
                continue
            viewing = shower_viewing(shower, peak, latitude, longitude, tz)
            if viewing is not None:
                found.append((shower, peak, viewing))
    return sorted(found, key=lambda item: item[1])


#: How far ahead the panel looks. Long enough that a shower is announced with
#: time to plan an evening, short enough that the strip stays a handful of
#: items rather than a calendar of its own.
LOOKAHEAD_DAYS = 21

#: Above this illuminated fraction the Moon washes out all but the brightest
#: meteors, which is the difference between a good night and a wasted one.
BRIGHT_MOON = 0.6


def _clock(moment: datetime) -> str:
    """A bare local time, "2am" or "11:30pm", for a one-line detail string."""
    hour = moment.hour % 12 or 12
    suffix = "am" if moment.hour < 12 else "pm"
    return f"{hour}{suffix}" if moment.minute == 0 else f"{hour}:{moment.minute:02d}{suffix}"


def sky_events(
    moment: datetime,
    latitude: float,
    longitude: float,
    tz: ZoneInfo,
    days: int = LOOKAHEAD_DAYS,
) -> list[dict]:
    """What is happening in *this* sky over the next few weeks, soonest first.

    Dates are local calendar dates, because "the 12th" is how a family plans an
    evening. The instants behind them are UTC throughout; only the final
    formatting is local.
    """
    horizon = moment + timedelta(days=days)
    events: list[dict] = []

    for name, when in next_moon_phases(moment):
        if when > horizon:
            continue
        events.append(
            {
                "kind": "moon",
                "name": name,
                "date": when.astimezone(tz).date().isoformat(),
                "detail": None,
            }
        )

    for shower, peak, viewing in meteor_showers_between(
        moment.astimezone(tz).date(), horizon.astimezone(tz).date(), latitude, longitude, tz
    ):
        # Everything after the rate is specific to these coordinates: when the
        # radiant is highest in a dark sky here, how high that is, and whether
        # the Moon is up to spoil it.
        detail = f"~{shower.zhr}/hr, best {_clock(viewing.best_at)}, radiant {round(viewing.altitude)}\u00b0 up"
        if viewing.moonlit:
            detail += ", bright moon"
        events.append(
            {
                "kind": "meteor_shower",
                "name": shower.name,
                "date": peak.isoformat(),
                "detail": detail,
            }
        )

    for name, when in seasons_between(moment, horizon, latitude):
        events.append(
            {
                "kind": "season",
                "name": name,
                "date": when.astimezone(tz).date().isoformat(),
                "detail": None,
            }
        )

    return sorted(events, key=lambda event: event["date"])


def astro_summary(moment: datetime, latitude: float, longitude: float, tz: ZoneInfo) -> dict:
    """Everything the panel's sky strip needs, in one call."""
    return {
        "moon": moon_phase(moment, latitude),
        "events": sky_events(moment, latitude, longitude, tz),
    }
