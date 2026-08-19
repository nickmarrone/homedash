"""Report who hosts each configured calendar, and how fast it can sync.

    uv run homedash-inspect-calendars [--probe]

ICS feeds are cached by the provider for hours, so a calendar that has to be
current needs a different adapter - and which adapter depends on who hosts it.
Run this before setting up credentials: it says, per calendar, whether fast
sync means an app-specific password or a full OAuth2 flow.

--probe additionally makes one network request per calendar: it fetches the
feed to confirm it is reachable, and for unrecognised hosts checks whether
/.well-known/caldav points at a CalDAV server.
"""

import argparse
import sys
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.calendars.providers import ICS_LATENCY, UNKNOWN, identify
from app.config import get_settings


def _probe_feed(url: str) -> str:
    try:
        response = httpx.get(url, timeout=20.0, follow_redirects=True)
    except Exception as exc:
        return f"unreachable - {type(exc).__name__}: {exc}"
    if response.status_code != 200:
        return f"HTTP {response.status_code}"
    return f"HTTP 200, {response.text.count('BEGIN:VEVENT')} VEVENTs"


def _probe_caldav(url: str) -> str:
    """Check the host's CalDAV well-known location.

    A redirect or a 401 both count as "there is a CalDAV server here" - 401
    is the expected answer to an unauthenticated request, and is a *positive*
    result rather than a failure.
    """
    parts = urlsplit(url)
    well_known = urlunsplit((parts.scheme, parts.netloc, "/.well-known/caldav", "", ""))
    try:
        response = httpx.request(
            "PROPFIND", well_known, timeout=15.0, follow_redirects=False, headers={"Depth": "0"}
        )
    except Exception as exc:
        return f"no CalDAV discovered ({type(exc).__name__})"
    if response.status_code in (301, 302, 307, 308):
        return f"CalDAV likely - redirects to {response.headers.get('location')!r}"
    if response.status_code in (401, 207):
        return f"CalDAV likely - HTTP {response.status_code} (auth required is a good sign)"
    return f"no CalDAV discovered - HTTP {response.status_code}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--probe",
        action="store_true",
        help="make live requests to confirm reachability and detect CalDAV",
    )
    args = parser.parse_args()

    settings = get_settings()
    calendars = settings.calendars
    if not calendars:
        print("No calendars configured. Set HOMEDASH_CALENDARS (see .env.example).")
        return 1

    needs_oauth: list[str] = []
    stays_slow: list[str] = []

    for entry in calendars:
        # A calendar already configured for a fast kind needs no guessing.
        if entry.kind != "ics":
            print(f"\n{entry.name}")
            print(f"  configured: kind {entry.kind!r} - already on the fast path")
            continue
        provider = identify(entry.url)
        host = urlsplit(entry.url).hostname or "(no host)"
        print(f"\n{entry.name}")
        print(f"  host      : {host}")
        print(f"  provider  : {provider.label}")

        if provider.fast_sync:
            print(f"  fast sync : yes, via the '{provider.fast_sync}' adapter")
            print(f"  needs     : {provider.credentials}")
            if provider.fast_sync == "google":
                needs_oauth.append(entry.name)
        else:
            print(f"  fast sync : not available - stays on ICS, {ICS_LATENCY}")
            stays_slow.append(entry.name)
        print(f"  note      : {provider.notes}")

        if args.probe:
            print(f"  feed      : {_probe_feed(entry.url)}")
            if provider is UNKNOWN:
                print(f"  caldav    : {_probe_caldav(entry.url)}")

    print("\n--- summary ---")
    print(f"{len(calendars)} calendar(s) configured.")
    if needs_oauth:
        print(f"OAuth2 setup required for: {', '.join(needs_oauth)}")
    if stays_slow:
        print(f"Staying on ICS (slow): {', '.join(stays_slow)}")
    if not needs_oauth and not stays_slow:
        print("All calendars can use CalDAV with an app-specific password.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
