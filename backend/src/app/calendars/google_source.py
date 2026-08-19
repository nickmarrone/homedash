"""Google Calendar adapter.

Read-only, and deliberately built to hand back VEVENT masters rather than
expanded occurrences, so Google's calendars flow through the same
`recurring_ical_events` expansion as every other kind.

Change detection uses the API's syncToken: one small request answers "nothing
changed", which is the common case on a one-minute poll. Anything else
triggers a full re-list, because rebuilding wholesale keeps a single
well-understood materialization path (see sync.sync_source).
"""

import logging
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from icalendar import Calendar
from icalendar.cal import Event as VEvent

from app.calendars.google_auth import GoogleCredentials

logger = logging.getLogger(__name__)

API_ROOT = "https://www.googleapis.com/calendar/v3/calendars"
PAGE_SIZE = 2500


class GoogleCalendarSource:
    """Polls one Google calendar."""

    def __init__(
        self,
        calendar_id: str,
        credentials: GoogleCredentials,
        window_start: datetime,
        window_end: datetime,
        sync_state: str | None = None,
        timeout: float = 30.0,
        get=None,
    ) -> None:
        self.calendar_id = calendar_id
        self.credentials = credentials
        self.window_start = window_start
        self.window_end = window_end
        self.timeout = timeout
        self._sync_state = sync_state
        self._get = get or self._default_get
        self.changed = False
        self._vevents: list[VEvent] = []

    @property
    def sync_state(self) -> str | None:
        return self._sync_state

    @property
    def events_url(self) -> str:
        return f"{API_ROOT}/{quote(self.calendar_id, safe='')}/events"

    def _default_get(self, url: str, params: dict, headers: dict):
        return httpx.get(url, params=params, headers=headers, timeout=self.timeout)

    def _request(self, params: dict) -> Any:
        headers = {"Authorization": f"Bearer {self.credentials.access_token()}"}
        response = self._get(self.events_url, params, headers)
        if response.status_code == 401:
            # The token looked current but the server disagrees - a revoked
            # grant or clock skew. One forced refresh distinguishes a
            # recoverable blip from a real authorization failure.
            self.credentials.invalidate()
            headers = {"Authorization": f"Bearer {self.credentials.access_token()}"}
            response = self._get(self.events_url, params, headers)
        return response

    def fetch(self, force: bool = False) -> list[VEvent]:
        if not force and self._sync_state:
            unchanged = self._unchanged_since(self._sync_state)
            if unchanged is True:
                self.changed = False
                return self._vevents
            # None means the token was rejected; fall through to a full list,
            # which re-establishes a usable one.

        items, token = self._list_all()
        self._vevents = items_to_vevents(items)
        self._sync_state = token
        self.changed = True
        return self._vevents

    def _unchanged_since(self, token: str) -> bool | None:
        """True if nothing changed, False if something did, None if the token
        was rejected and a full re-list is required."""
        # syncToken cannot be combined with timeMin/timeMax - the API rejects
        # the request outright. The window is reapplied by the full re-list
        # this leads to, and sync.needs_full_resync forces one daily, which is
        # what keeps a token issued against yesterday's window from pinning
        # the horizon in place.
        response = self._request({"syncToken": token, "maxResults": PAGE_SIZE})
        if response.status_code == 410:
            # Documented: an expired sync token requires starting over. Routine
            # rather than exceptional - Google expires them on its own schedule.
            logger.info("Google sync token expired for %s; re-listing", self.calendar_id)
            return None
        if response.status_code != 200:
            logger.warning(
                "Google incremental sync for %s returned %s; re-listing",
                self.calendar_id,
                response.status_code,
            )
            return None
        return not response.json().get("items")

    def _list_all(self) -> tuple[list[dict], str | None]:
        """Every event overlapping the window, following pagination."""
        items: list[dict] = []
        page_token: str | None = None
        sync_token: str | None = None

        while True:
            params = {
                "timeMin": _rfc3339(self.window_start),
                "timeMax": _rfc3339(self.window_end),
                # Masters with their RRULE, not occurrences: expansion belongs
                # to the one path every kind shares.
                "singleEvents": "false",
                # Cancelled occurrences have to be visible, or a cancelled
                # soccer practice keeps showing on the wall.
                "showDeleted": "true",
                "maxResults": PAGE_SIZE,
            }
            if page_token:
                params["pageToken"] = page_token
            response = self._request(params)
            if response.status_code != 200:
                raise GoogleApiError(
                    f"Google events.list for {self.calendar_id} failed: "
                    f"{response.status_code} {response.text[:200]}"
                )
            payload = response.json()
            items.extend(payload.get("items", []))
            page_token = payload.get("nextPageToken")
            if not page_token:
                # Only the final page carries it.
                sync_token = payload.get("nextSyncToken")
                break
        return items, sync_token


class GoogleApiError(RuntimeError):
    pass


def _rfc3339(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_point(point: dict) -> tuple[date | datetime | None, bool]:
    """One end of a Google event: either a date (all-day) or a dateTime."""
    if "date" in point:
        return date.fromisoformat(point["date"]), True
    raw = point.get("dateTime")
    if not raw:
        return None, False
    moment = datetime.fromisoformat(raw)
    zone = point.get("timeZone")
    if zone:
        try:
            # Keep recurring events in their own zone rather than normalising
            # to UTC: a weekly 9am would otherwise shift by an hour across a
            # DST boundary, because the RRULE is expanded against DTSTART.
            moment = moment.astimezone(ZoneInfo(zone))
        except (ZoneInfoNotFoundError, ValueError):
            logger.warning("Unknown Google timeZone %r; keeping the given offset", zone)
    return moment, False


def items_to_vevents(items: list[dict]) -> list[VEvent]:
    """Convert Google's JSON events into VEVENT components.

    Recurring events arrive as a master plus separate entries for any occurrence
    that was moved or cancelled. Those are reattached to their master as
    RECURRENCE-ID overrides and EXDATEs respectively, which is what the
    recurrence expander understands.
    """
    masters = [i for i in items if not i.get("recurringEventId")]
    overrides = [i for i in items if i.get("recurringEventId")]

    uid_by_google_id: dict[str, str] = {}
    components: list[VEvent] = []

    for item in masters:
        if item.get("status") == "cancelled":
            continue
        uid = item.get("iCalUID") or item.get("id")
        if not uid:
            continue
        component = _build_component(item, uid)
        if component is None:
            continue
        if item.get("id"):
            uid_by_google_id[item["id"]] = uid
        components.append(component)

    exdates: dict[str, list] = {}
    for item in overrides:
        master_id = item["recurringEventId"]
        uid = uid_by_google_id.get(master_id)
        if uid is None:
            # The master fell outside the window; its occurrences are not
            # being rendered either, so there is nothing to correct.
            continue
        original, _ = _parse_point(item.get("originalStartTime") or {})
        if item.get("status") == "cancelled":
            if original is not None:
                exdates.setdefault(uid, []).append(original)
            continue
        component = _build_component(item, uid, recurrence_id=original)
        if component is not None:
            components.append(component)

    if exdates:
        components = _apply_exdates(components, exdates)
    return components


def _build_component(item: dict, uid: str, recurrence_id=None) -> VEvent | None:
    start, start_is_date = _parse_point(item.get("start") or {})
    if start is None:
        return None
    end, _ = _parse_point(item.get("end") or {})

    event = VEvent()
    event.add("UID", uid)
    event.add("SUMMARY", item.get("summary") or "")
    if item.get("location"):
        event.add("LOCATION", item["location"])
    event.add("DTSTART", start)
    if end is not None:
        event.add("DTEND", end)
    if recurrence_id is not None:
        event.add("RECURRENCE-ID", recurrence_id)

    recurrence = item.get("recurrence") or []
    if not recurrence:
        return event

    # Recurrence rules are re-parsed from their raw lines rather than
    # constructed property by property: RRULE, EXDATE and RDATE each have
    # their own parameter grammar, and icalendar already implements it.
    return _reparse_with_recurrence(event, recurrence)


def _reparse_with_recurrence(event: VEvent, lines: list[str]) -> VEvent | None:
    text = event.to_ical().decode("utf-8").rstrip("\r\n")
    body = text.replace("END:VEVENT", "\r\n".join(lines) + "\r\nEND:VEVENT")
    wrapper = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//HomeDash//Google//EN\r\n"
        f"{body}\r\nEND:VCALENDAR\r\n"
    )
    try:
        parsed = list(Calendar.from_ical(wrapper).walk("VEVENT"))
    except Exception:
        logger.warning("Could not parse recurrence %r; keeping the event non-recurring", lines)
        return event
    return parsed[0] if parsed else event


def _apply_exdates(components: list[VEvent], exdates: dict[str, list]) -> list[VEvent]:
    """Attach cancelled occurrences to their master as EXDATEs."""
    for component in components:
        uid = str(component.get("UID"))
        # Only the master carries the rule; an override entry with the same
        # UID must not absorb the exclusions.
        if uid not in exdates or component.get("RECURRENCE-ID") is not None:
            continue
        for value in exdates[uid]:
            component.add("EXDATE", value)
    return components
