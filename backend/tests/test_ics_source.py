"""The ICS adapter's HTTP behaviour.

`force` and the ETag decide whether the sync believes anything changed, so a
mistake here reads as either a panel that never updates or a calendar emptied
by a response that was never a calendar.
"""

import httpx
import pytest

from app.calendars.ics import ICSCalendarSource

BODY = (
    b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//t//EN\r\n"
    b"BEGIN:VEVENT\r\nUID:a\r\nDTSTART:20260824T100000Z\r\nSUMMARY:Dentist\r\nEND:VEVENT\r\n"
    b"END:VCALENDAR\r\n"
)
URL = "https://example.test/a.ics"


@pytest.fixture
def serve(monkeypatch):
    def install(status=200, content=BODY, headers=None):
        seen = {}

        def get(url, **kwargs):
            seen.update(kwargs, url=url)
            return httpx.Response(
                status, content=content, headers=headers or {}, request=httpx.Request("GET", url)
            )

        monkeypatch.setattr("app.calendars.ics.httpx.get", get)
        return seen

    return install


def test_a_new_body_is_parsed_and_reported_changed(serve):
    serve(headers={"ETag": '"v2"'})
    source = ICSCalendarSource(URL)
    events = source.fetch()
    assert [str(e["SUMMARY"]) for e in events] == ["Dentist"]
    assert source.changed is True
    assert source.sync_state == '"v2"'


def test_a_source_starts_out_unchanged(serve):
    assert ICSCalendarSource(URL).changed is False


def test_it_follows_redirects(serve):
    """webcal links and hosted feeds redirect constantly."""
    seen = serve()
    ICSCalendarSource(URL).fetch()
    assert seen["follow_redirects"] is True


def test_a_known_etag_is_sent(serve):
    seen = serve()
    ICSCalendarSource(URL, etag='"v1"').fetch()
    assert seen["headers"] == {"If-None-Match": '"v1"'}


def test_a_forced_fetch_does_not_send_it(serve):
    """A 304 would hand a re-expansion an empty list."""
    seen = serve()
    ICSCalendarSource(URL, etag='"v1"').fetch(force=True)
    assert seen["headers"] == {}


def test_a_304_is_no_change(serve):
    serve(status=304, content=b"")
    source = ICSCalendarSource(URL, etag='"v1"')
    assert source.fetch() == []
    assert source.changed is False
    assert source.sync_state == '"v1"'


@pytest.mark.parametrize("status", [401, 404, 500])
def test_an_error_status_raises_even_with_a_parseable_body(serve, status):
    """So the caller rolls back, instead of replacing the calendar."""
    serve(status=status)
    with pytest.raises(httpx.HTTPStatusError):
        ICSCalendarSource(URL).fetch()
