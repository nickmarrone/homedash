import httpx
from icalendar import Calendar
from icalendar.cal import Event as VEvent


class ICSCalendarSource:
    """Polls a single ICS URL over HTTP, using conditional GET (ETag) to
    avoid re-parsing an unchanged feed."""

    def __init__(self, url: str, etag: str | None = None, timeout: float = 20.0) -> None:
        self.url = url
        self.etag = etag
        self.timeout = timeout
        self.changed = False
        self._vevents: list[VEvent] = []

    def fetch(self, force: bool = False) -> list[VEvent]:
        # A forced fetch skips the conditional GET: the caller wants the real
        # body because it is about to re-expand the window, and a 304 would
        # hand it back an empty list.
        headers = {"If-None-Match": self.etag} if self.etag and not force else {}
        response = httpx.get(self.url, headers=headers, timeout=self.timeout, follow_redirects=True)

        if response.status_code == 304:
            self.changed = False
            return self._vevents

        response.raise_for_status()
        calendar = Calendar.from_ical(response.content)
        self._vevents = list(calendar.walk("VEVENT"))
        self.etag = response.headers.get("ETag")
        self.changed = True
        return self._vevents

    @property
    def sync_state(self) -> str | None:
        """The feed's ETag - what a conditional GET resumes from."""
        return self.etag
