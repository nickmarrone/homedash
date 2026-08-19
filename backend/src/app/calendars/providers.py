"""Working out who hosts a calendar feed, and what that means for latency.

An ICS feed's URL is the only clue we have about where a calendar actually
lives, and the answer decides how much work fast sync is: an app-specific
password for CalDAV, or a full OAuth2 consent flow for Google. Classification
is by hostname and is deliberately conservative - a guess that says "unknown"
is more useful than one that sends someone down the wrong setup path.
"""

from dataclasses import dataclass
from urllib.parse import urlsplit

# How quickly a change made on a phone can reach the panel.
ICS_LATENCY = "hours (the provider regenerates the .ics file on its own schedule)"


@dataclass(frozen=True)
class Provider:
    key: str
    label: str
    fast_sync: str | None  # the adapter kind that would give ~1 minute latency
    credentials: str
    notes: str


GOOGLE = Provider(
    key="google",
    label="Google Calendar",
    fast_sync="google",
    credentials="OAuth2 client ID + secret, and a refresh token",
    notes=(
        "Google removed basic auth, so both CalDAV and the Calendar API need a "
        "consent flow. The Calendar API with syncToken is the better target: "
        "cleaner deltas and better documented than Google's CalDAV endpoint."
    ),
)

ICLOUD = Provider(
    key="icloud",
    label="Apple iCloud",
    fast_sync="caldav",
    credentials="Apple ID + an app-specific password",
    notes="Generate the app-specific password at appleid.apple.com.",
)

FASTMAIL = Provider(
    key="fastmail",
    label="Fastmail",
    fast_sync="caldav",
    credentials="username + an app password scoped to CalDAV",
    notes="Best sync-token support of the three CalDAV hosts; the easiest case.",
)

NEXTCLOUD = Provider(
    key="nextcloud",
    label="Nextcloud",
    fast_sync="caldav",
    credentials="username + an app password",
    notes="Recognised by the /remote.php/dav path rather than the hostname.",
)

OUTLOOK = Provider(
    key="outlook",
    label="Outlook / Microsoft 365",
    fast_sync=None,
    credentials="Microsoft Graph app registration",
    notes=(
        "Microsoft retired CalDAV for Outlook.com, so fast sync would mean a "
        "Graph API adapter. Not planned - leave this one on ICS."
    ),
)

UNKNOWN = Provider(
    key="unknown",
    label="Unrecognised host",
    fast_sync=None,
    credentials="unknown",
    notes=(
        "Not a host we recognise. Many providers expose CalDAV at "
        "/.well-known/caldav - re-run with --probe to check."
    ),
)


def identify(url: str) -> Provider:
    """Best guess at the provider behind a calendar URL."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    host = host.lower()
    path = parts.path.lower()

    if host.endswith("google.com") or host.endswith("googleusercontent.com"):
        return GOOGLE
    if host.endswith("icloud.com") or host.endswith("me.com"):
        return ICLOUD
    if host.endswith("fastmail.com") or host.endswith("fastmail.fm"):
        return FASTMAIL
    if "/remote.php/dav" in path or "/remote.php/caldav" in path:
        return NEXTCLOUD
    if host.endswith("office365.com") or host.endswith("outlook.com") or host.endswith("live.com"):
        return OUTLOOK
    return UNKNOWN
