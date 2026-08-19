"""Provider classification decides how much setup work fast sync means,
so a wrong guess sends someone down the wrong path entirely."""

import pytest

from app.calendars.providers import identify


@pytest.mark.parametrize(
    "url,expected_key,expected_adapter",
    [
        ("https://calendar.google.com/calendar/ical/abc%40group.calendar.google.com/private-x/basic.ics", "google", "google"),
        ("https://www.google.com/calendar/ical/xyz/basic.ics", "google", "google"),
        ("webcal://p52-caldav.icloud.com/published/2/MTk3", "icloud", "caldav"),
        ("https://p01-calendars.icloud.com/published/2/abc", "icloud", "caldav"),
        ("https://caldav.fastmail.com/dav/calendars/user/me/abc", "fastmail", "caldav"),
        ("https://cloud.example.org/remote.php/dav/calendars/nick/personal", "nextcloud", "caldav"),
        ("https://outlook.office365.com/owa/calendar/abc/reachcalendar.ics", "outlook", None),
        ("https://school.example.edu/calendar/events.ics", "unknown", None),
    ],
)
def test_identify(url, expected_key, expected_adapter):
    provider = identify(url)
    assert provider.key == expected_key
    assert provider.fast_sync == expected_adapter


def test_hostname_matching_is_not_fooled_by_a_lookalike_domain():
    # Naive substring matching on the whole URL would call this Google.
    provider = identify("https://calendar.google.com.evil.example/feed.ics")
    assert provider.key == "unknown"


def test_userinfo_in_the_url_does_not_confuse_the_host():
    provider = identify("https://user:pw@caldav.fastmail.com/dav/calendars/x")
    assert provider.key == "fastmail"


def test_case_is_ignored():
    assert identify("https://CalDAV.FastMail.COM/dav/x").key == "fastmail"
