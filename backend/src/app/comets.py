"""Bright comets, from the Minor Planet Center's orbital elements.

The one part of the sky this app cannot compute from first principles. Meteor
showers are annual clockwork and the Moon keeps to its own schedule, but a
naked-eye comet is a *discovery* - NEOWISE in 2020, Tsuchinshan-ATLAS in 2024.
Neither existed in any table until it did, so there is nothing to hard-code and
a feed is the only honest way to know.

That makes this the one module here that reaches the network, and it is built
to fail quietly:

* Elements are cached on disk beside the database, so a restart keeps them and
  a failed refresh falls back to the last good copy rather than emptying the
  strip.
* A parse failure on one line skips that line, not the file. The MPC format is
  fixed-column and occasionally gains a comet whose name overflows what was
  expected.
* Every value is range-checked after parsing, so a format change shows up as
  "no comets" rather than as a comet at an impossible distance.

**On trusting the magnitudes.** Comet brightness predictions are unreliable in
a way that orbital positions are not. The position is celestial mechanics; the
brightness depends on how much ice happens to be left and how it behaves near
the Sun, and comets routinely miss their forecast by magnitudes in both
directions. The default cut-off is deliberately conservative for that reason,
and a comet listed here is "worth a look", never a promise.
"""

import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from app.astro import (
    Viewing,
    best_dark_view,
    earth_heliocentric,
    ecliptic_to_equatorial,
    julian_day,
)
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

MPC_ELEMENTS_URL = "https://www.minorplanetcenter.net/iau/MPCORB/CometEls.txt"

#: Gaussian gravitational constant, in radians per day. The one number that
#: turns an orbit's shape into a rate of travel along it.
GAUSS_K = 0.01720209895

#: Eccentricities this close to 1 are treated as exactly parabolic. The
#: elliptical and hyperbolic solvers both degenerate there - the semi-major
#: axis runs away to infinity - while Barker's equation is exact at e = 1 and
#: excellent either side of it. Most long-period comets sit in this band.
PARABOLIC_TOLERANCE = 0.001


@dataclass(frozen=True)
class Comet:
    """One comet's osculating orbital elements, J2000 ecliptic."""

    name: str
    #: Perihelion distance, AU.
    perihelion_distance: float
    eccentricity: float
    #: Instant of perihelion passage.
    perihelion: datetime
    #: Argument of perihelion, longitude of ascending node, inclination; degrees.
    argument_of_perihelion: float
    ascending_node: float
    inclination: float
    #: Absolute magnitude and slope parameter, for the brightness model.
    absolute_magnitude: float
    slope: float


def _solve_elliptical(mean_anomaly: float, eccentricity: float) -> float:
    """Kepler's equation, M = E - e sin E, for the eccentric anomaly."""
    anomaly = mean_anomaly
    # Newton-Raphson. Comets run to e = 0.999, where convergence from M alone
    # is slow, so start from the standard high-eccentricity guess instead.
    if eccentricity > 0.8:
        anomaly = math.pi
    for _ in range(60):
        delta = (anomaly - eccentricity * math.sin(anomaly) - mean_anomaly) / (
            1 - eccentricity * math.cos(anomaly)
        )
        anomaly -= delta
        if abs(delta) < 1e-12:
            break
    return anomaly


def _solve_hyperbolic(mean_anomaly: float, eccentricity: float) -> float:
    """Kepler's equation for a hyperbolic orbit, M = e sinh H - H."""
    anomaly = math.copysign(math.log(2 * abs(mean_anomaly) / eccentricity + 1.8), mean_anomaly)
    for _ in range(60):
        delta = (eccentricity * math.sinh(anomaly) - anomaly - mean_anomaly) / (
            eccentricity * math.cosh(anomaly) - 1
        )
        anomaly -= delta
        if abs(delta) < 1e-12:
            break
    return anomaly


def true_anomaly_and_radius(comet: Comet, jd: float) -> tuple[float, float]:
    """Where the comet is along its orbit: true anomaly in radians, radius in AU.

    Three conic sections, three solvers. The parabolic case is not an
    afterthought - it is the *common* one for the comets anybody notices,
    because a first-time visitor from the Oort cloud arrives on an orbit
    indistinguishable from a parabola.
    """
    q = comet.perihelion_distance
    e = comet.eccentricity
    days = jd - julian_day(comet.perihelion)

    if abs(e - 1.0) < PARABOLIC_TOLERANCE:
        # Barker's equation. s = tan(nu/2) satisfies s + s^3/3 = W, which
        # Cardano solves in closed form - no iteration and no failure mode.
        w = GAUSS_K * days / (math.sqrt(2.0) * q**1.5)
        b = 3.0 * w
        y = (b / 2 + math.sqrt(b * b / 4 + 1)) ** (1 / 3)
        s = y - 1 / y
        return 2 * math.atan(s), q * (1 + s * s)

    if e < 1.0:
        a = q / (1 - e)
        mean_anomaly = GAUSS_K * days / a**1.5
        eccentric = _solve_elliptical(mean_anomaly, e)
        nu = 2 * math.atan2(
            math.sqrt(1 + e) * math.sin(eccentric / 2),
            math.sqrt(1 - e) * math.cos(eccentric / 2),
        )
        return nu, a * (1 - e * math.cos(eccentric))

    a = q / (e - 1)
    mean_anomaly = GAUSS_K * days / a**1.5
    hyperbolic = _solve_hyperbolic(mean_anomaly, e)
    nu = 2 * math.atan2(
        math.sqrt(e + 1) * math.sinh(hyperbolic / 2),
        math.sqrt(e - 1) * math.cosh(hyperbolic / 2),
    )
    return nu, a * (e * math.cosh(hyperbolic) - 1)


def heliocentric(comet: Comet, jd: float) -> tuple[float, float, float]:
    """The comet's heliocentric ecliptic rectangular coordinates, in AU."""
    nu, r = true_anomaly_and_radius(comet, jd)
    # Argument of latitude: how far round the orbital plane from the node.
    u = nu + math.radians(comet.argument_of_perihelion)
    node = math.radians(comet.ascending_node)
    inclination = math.radians(comet.inclination)

    return (
        r * (math.cos(node) * math.cos(u) - math.sin(node) * math.sin(u) * math.cos(inclination)),
        r * (math.sin(node) * math.cos(u) + math.cos(node) * math.sin(u) * math.cos(inclination)),
        r * (math.sin(u) * math.sin(inclination)),
    )


def observed(comet: Comet, jd: float) -> tuple[float, float, float, float]:
    """Right ascension, declination, distance from Earth, and distance from the
    Sun - the four numbers that decide whether a comet is worth mentioning.

    Light-time is not iterated. It shifts a comet by seconds of arc, which is
    invisible next to the uncertainty in whether it will be bright at all.
    """
    x, y, z = heliocentric(comet, jd)
    earth_x, earth_y, earth_z = earth_heliocentric(jd)

    dx, dy, dz = x - earth_x, y - earth_y, z - earth_z
    delta = math.sqrt(dx * dx + dy * dy + dz * dz)
    sun_distance = math.sqrt(x * x + y * y + z * z)

    longitude = math.degrees(math.atan2(dy, dx)) % 360.0
    latitude = math.degrees(math.asin(max(-1.0, min(1.0, dz / delta)))) if delta else 0.0
    ra, dec = ecliptic_to_equatorial(longitude, latitude)
    return ra, dec, delta, sun_distance


def magnitude(comet: Comet, earth_distance: float, sun_distance: float) -> float:
    """Predicted total visual magnitude.

    m = H + 5 log(delta) + 2.5 K log(r) - the standard comet law. The distance
    from Earth dims it geometrically; the distance from the Sun governs how
    hard it is being cooked, which is the part nobody can predict well. The
    slope parameter K is the MPC's, typically 4.
    """
    if earth_distance <= 0 or sun_distance <= 0:
        return math.inf
    return (
        comet.absolute_magnitude
        + 5 * math.log10(earth_distance)
        + 2.5 * comet.slope * math.log10(sun_distance)
    )


# --- the MPC's fixed-column element file --------------------------------------
#
# Documented at minorplanetcenter.net as "Format For Cometary Orbits". Columns
# are 1-indexed there and sliced 0-indexed here.
_COLUMNS = {
    "perihelion_year": (14, 18),
    "perihelion_month": (19, 21),
    "perihelion_day": (22, 29),
    "perihelion_distance": (30, 39),
    "eccentricity": (41, 49),
    "argument_of_perihelion": (51, 59),
    "ascending_node": (61, 69),
    "inclination": (71, 79),
    "absolute_magnitude": (91, 95),
    "slope": (96, 100),
    "name": (102, 158),
}


def _field(line: str, key: str) -> str:
    start, end = _COLUMNS[key]
    return line[start:end].strip()


def parse_comet_line(line: str) -> Comet | None:
    """One line of CometEls.txt, or None if it is not a usable orbit.

    Deliberately total: a line that does not parse, or that parses into
    something impossible, is dropped with a log line rather than raising. One
    malformed record must never cost the panel the whole file.
    """
    if len(line) < 100:
        return None
    try:
        day = float(_field(line, "perihelion_day"))
        perihelion = datetime(
            int(_field(line, "perihelion_year")),
            int(_field(line, "perihelion_month")),
            1,
            tzinfo=timezone.utc,
        ) + _fractional_days(day)

        comet = Comet(
            name=_field(line, "name") or "Unnamed comet",
            perihelion_distance=float(_field(line, "perihelion_distance")),
            eccentricity=float(_field(line, "eccentricity")),
            perihelion=perihelion,
            argument_of_perihelion=float(_field(line, "argument_of_perihelion")),
            ascending_node=float(_field(line, "ascending_node")),
            inclination=float(_field(line, "inclination")),
            absolute_magnitude=float(_field(line, "absolute_magnitude") or "nan"),
            slope=float(_field(line, "slope") or "nan"),
        )
    except (ValueError, IndexError):
        return None

    if not _plausible(comet):
        return None
    return comet


def _fractional_days(day: float) -> timedelta:
    # The column counts from day 1, so day 1.0 is the start of the first.
    return timedelta(days=day - 1)


def _plausible(comet: Comet) -> bool:
    """Range checks, so a shifted column reads as no data rather than nonsense."""
    return (
        0 < comet.perihelion_distance < 1000
        and 0 <= comet.eccentricity < 20
        and 0 <= comet.argument_of_perihelion <= 360
        and 0 <= comet.ascending_node <= 360
        and 0 <= comet.inclination <= 180
        and math.isfinite(comet.absolute_magnitude)
        and math.isfinite(comet.slope)
    )


def parse_comet_elements(text: str) -> list[Comet]:
    """Every usable orbit in a CometEls.txt file."""
    comets = [c for c in (parse_comet_line(line) for line in text.splitlines()) if c]
    if not comets and text.strip():
        logger.warning("No comet orbits could be parsed; the MPC format may have changed")
    return comets


# --- fetching and caching -----------------------------------------------------


def elements_path() -> Path:
    """Beside the database, so the existing volume already persists it."""
    return settings.db_path.parent / "CometEls.txt"


def refresh_comet_elements(get=None) -> bool:
    """Fetch the MPC element file and cache it. True if it was updated.

    Failure is not exceptional here - it is a Saturday when the MPC is down -
    so it logs and returns False, leaving whatever is on disk in place.
    """
    fetch = get or (lambda url: httpx.get(url, timeout=30.0, follow_redirects=True))
    try:
        response = fetch(MPC_ELEMENTS_URL)
        response.raise_for_status()
        text = response.text
    except Exception:
        logger.warning("Could not refresh comet elements from the MPC", exc_info=True)
        return False

    if not parse_comet_elements(text):
        logger.warning("Refused to cache a comet element file with no usable orbits")
        return False

    path = elements_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


def load_comet_elements() -> list[Comet]:
    """The cached orbits, or an empty list if there are none yet."""
    path = elements_path()
    if not path.exists():
        return []
    try:
        return parse_comet_elements(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        logger.warning("Could not read cached comet elements at %s", path, exc_info=True)
        return []


# --- what to put on the panel -------------------------------------------------


def comet_viewing(
    comet: Comet, night: date, latitude: float, longitude: float, tz: ZoneInfo
) -> Viewing | None:
    """The best moment to look for a comet on one night, from one place."""
    return best_dark_view(
        lambda jd: observed(comet, jd)[:2], night, latitude, longitude, tz
    )


#: A comet fainter than this is not worth sending anybody outside for. Six is
#: roughly the naked-eye limit under a dark sky and generous under a suburban
#: one - deliberately so, given how unreliable the predictions are.
DEFAULT_MAGNITUDE_LIMIT = 6.0

#: How high a comet must climb before it is worth mentioning. Lower than the
#: meteor threshold because a comet is a single object you can go and find,
#: rather than a whole sky to watch.
MIN_COMET_ALTITUDE = 10.0


def visible_comets(
    comets: list[Comet],
    moment: datetime,
    latitude: float,
    longitude: float,
    tz: ZoneInfo,
    magnitude_limit: float = DEFAULT_MAGNITUDE_LIMIT,
) -> list[dict]:
    """Comets bright enough and high enough to be worth a look tonight.

    Brightness is checked before position, because it rejects all but a handful
    of the thousand-odd orbits in the file for the cost of one Kepler solve -
    and the night walk that follows is fifty times more expensive.
    """
    tonight = moment.astimezone(tz).date()
    jd = julian_day(moment)
    found: list[dict] = []

    for comet in comets:
        try:
            _, _, earth_distance, sun_distance = observed(comet, jd)
            predicted = magnitude(comet, earth_distance, sun_distance)
        except (ValueError, OverflowError, ZeroDivisionError):
            # A wild orbit from a bad record; drop it rather than the file.
            continue
        if not math.isfinite(predicted) or predicted > magnitude_limit:
            continue

        viewing = comet_viewing(comet, tonight, latitude, longitude, tz)
        if viewing is None or viewing.altitude < MIN_COMET_ALTITUDE:
            continue

        detail = (
            f"mag {predicted:.1f}, best {_clock(viewing.best_at)}, "
            f"{round(viewing.altitude)}° up"
        )
        if viewing.moonlit:
            detail += ", bright moon"
        found.append(
            {
                "kind": "comet",
                "name": comet.name,
                "date": tonight.isoformat(),
                "detail": detail,
                "magnitude": round(predicted, 1),
            }
        )

    # Brightest first: if two are up, that is the one to point at.
    return sorted(found, key=lambda item: item["magnitude"])


def _clock(moment: datetime) -> str:
    hour = moment.hour % 12 or 12
    suffix = "am" if moment.hour < 12 else "pm"
    return f"{hour}{suffix}" if moment.minute == 0 else f"{hour}:{moment.minute:02d}{suffix}"
