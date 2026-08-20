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
    #: Declination of the radiant, which is what decides whether the shower is
    #: visible from a given latitude at all.
    declination: float
    #: Zenithal hourly rate at peak, under a dark sky with the radiant
    #: overhead. Real counts are always lower; it is a comparative number.
    zhr: int


# The showers worth waking a child up for. Minor ones are left out on purpose:
# a strip that lists a 5-per-hour shower teaches people to ignore it.
METEOR_SHOWERS = [
    MeteorShower("Quadrantids", 1, 3, 49.7, 110),
    MeteorShower("Lyrids", 4, 22, 33.3, 18),
    MeteorShower("Eta Aquariids", 5, 6, -1.0, 50),
    MeteorShower("Delta Aquariids", 7, 30, -16.4, 25),
    MeteorShower("Perseids", 8, 12, 58.0, 100),
    MeteorShower("Orionids", 10, 21, 15.8, 20),
    MeteorShower("Leonids", 11, 17, 21.6, 15),
    MeteorShower("Geminids", 12, 14, 32.4, 150),
    MeteorShower("Ursids", 12, 22, 75.3, 10),
]

# Below this the radiant never climbs far enough out of the haze for the
# shower to be worth announcing - which is how the Perseids correctly vanish
# from a panel in Sydney and the Eta Aquariids stay on it.
MIN_RADIANT_ALTITUDE = 15.0


def radiant_max_altitude(declination: float, latitude: float) -> float:
    """How high the radiant gets at its best, in degrees above the horizon."""
    return 90.0 - abs(latitude - declination)


def meteor_showers_between(
    start: date, end: date, latitude: float
) -> list[tuple[MeteorShower, date]]:
    """Shower peaks in a date window that are actually visible from a latitude."""
    found: list[tuple[MeteorShower, date]] = []
    for year in range(start.year, end.year + 1):
        for shower in METEOR_SHOWERS:
            if radiant_max_altitude(shower.declination, latitude) < MIN_RADIANT_ALTITUDE:
                continue
            peak = date(year, shower.month, shower.day)
            if start <= peak <= end:
                found.append((shower, peak))
    return sorted(found, key=lambda item: item[1])


#: How far ahead the panel looks. Long enough that a shower is announced with
#: time to plan an evening, short enough that the strip stays a handful of
#: items rather than a calendar of its own.
LOOKAHEAD_DAYS = 21

#: Above this illuminated fraction the Moon washes out all but the brightest
#: meteors, which is the difference between a good night and a wasted one.
BRIGHT_MOON = 0.6


def sky_events(moment: datetime, latitude: float, tz: ZoneInfo, days: int = LOOKAHEAD_DAYS) -> list[dict]:
    """What is happening in the sky over the next few weeks, soonest first.

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

    for shower, peak in meteor_showers_between(
        moment.astimezone(tz).date(), horizon.astimezone(tz).date(), latitude
    ):
        # The Moon at the peak, not now - a shower three weeks out is judged by
        # the sky it will actually have.
        peak_moment = datetime(peak.year, peak.month, peak.day, 22, tzinfo=tz)
        moon = moon_phase(peak_moment, latitude)
        washed_out = moon["illumination"] >= BRIGHT_MOON
        events.append(
            {
                "kind": "meteor_shower",
                "name": shower.name,
                "date": peak.isoformat(),
                "detail": (
                    f"~{shower.zhr}/hr peak"
                    + (", washed out by moonlight" if washed_out else "")
                ),
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


def astro_summary(moment: datetime, latitude: float, tz: ZoneInfo) -> dict:
    """Everything the panel's sky strip needs, in one call."""
    return {
        "moon": moon_phase(moment, latitude),
        "events": sky_events(moment, latitude, tz),
    }
