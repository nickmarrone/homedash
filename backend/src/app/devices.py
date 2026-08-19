"""The wall panel's screen schedule.

The Pi runs a small agent that polls GET /api/devices/{id}/screen and applies
whatever state comes back. All the date arithmetic happens here, on the server,
for the same reason it happens in calendars/grid.py rather than in the browser:
the panel's own clock and timezone are not trusted anywhere in this app, and a
thin client should not be reimplementing DST.
"""

import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlmodel import Session

from app.config import ScreenScheduleConfig, get_settings
from app.models import Device

logger = logging.getLogger(__name__)
settings = get_settings()

# There is exactly one panel, so its row has a fixed id rather than a pairing
# flow. The agent's URL therefore never changes between reinstalls.
PANEL_DEVICE_ID = 1

# How often the agent is told to poll. Also the resolution of the schedule: a
# transition is applied within this long of its scheduled minute.
POLL_AFTER_SECONDS = 30

# last_seen is written at most this often. At a 30-second poll an unthrottled
# write would be ~2900 rows a day rewritten to say almost the same thing.
LAST_SEEN_THROTTLE = timedelta(seconds=60)

# How far ahead to look for the next transition before giving up and calling
# the schedule constant. Eight days covers a weekend override from any weekday.
_TRANSITION_HORIZON_DAYS = 8


def seed_device_from_settings(session: Session) -> Device:
    """Reconcile the devices row against HOMEDASH_SCREEN_SCHEDULE.

    The env var is the source of truth on startup, the same contract calendars
    have. `last_seen` is deliberately left alone - it is observed state, not
    configuration, and rewriting it here would make a restart look like a
    check-in from a panel that may well be unplugged.
    """
    device = session.get(Device, PANEL_DEVICE_ID)
    if device is None:
        device = Device(id=PANEL_DEVICE_ID, name=settings.device_name, screen_schedule="")
    device.name = settings.device_name
    device.screen_schedule = settings.screen_schedule.model_dump_json()
    session.add(device)
    session.commit()
    session.refresh(device)
    return device


def schedule_of(device: Device) -> ScreenScheduleConfig:
    """The device's stored schedule, falling back to the configured one.

    A row written by a future settings UI could hold anything, and a panel that
    goes dark because its schedule failed to parse is a miserable way to find
    out, so an unreadable value logs and yields to config rather than raising.
    """
    if not device.screen_schedule:
        return settings.screen_schedule
    try:
        return ScreenScheduleConfig.model_validate_json(device.screen_schedule)
    except ValueError:
        logger.exception(
            "Device %s has an unreadable screen_schedule; using HOMEDASH_SCREEN_SCHEDULE",
            device.id,
        )
        return settings.screen_schedule


def _combine(day: date, moment: time, tz: ZoneInfo) -> datetime:
    """A local instant for a wall-clock time on a date.

    On DST days a wall-clock time can be ambiguous or absent. Neither is worth
    special-casing: "screen off at 21:30" means the 21:30 the household reads
    off a wall clock, which is exactly what a fold-naive combine gives.
    """
    return datetime.combine(day, moment).replace(tzinfo=tz)


def is_lit(schedule: ScreenScheduleConfig, moment: datetime) -> bool:
    """Whether the screen should be on at a home-local instant."""
    return schedule.window_for(moment.date()).lit_at(moment.time())


def next_transition(
    schedule: ScreenScheduleConfig, moment: datetime, tz: ZoneInfo
) -> datetime | None:
    """When the screen next changes state, or None if it never does.

    Candidate instants are every window boundary over the next few days, tested
    in order until one actually differs from the current state. Testing rather
    than assuming matters because a boundary is not always a transition: a
    weekday window ending at 21:30 followed by a weekend one starting at 08:00
    puts two boundaries in a row that both mean "off".
    """
    lit_now = is_lit(schedule, moment)
    candidates: list[datetime] = []
    for offset in range(_TRANSITION_HORIZON_DAYS):
        day = moment.date() + timedelta(days=offset)
        window = schedule.window_for(day)
        for boundary in (window.on_time, window.off_time):
            candidate = _combine(day, boundary, tz)
            if candidate > moment:
                candidates.append(candidate)

    for candidate in sorted(candidates):
        if is_lit(schedule, candidate) != lit_now:
            return candidate
    return None


def screen_state(device: Device, now: datetime, tz: ZoneInfo) -> dict:
    """The payload the Pi's screen agent polls for.

    `until` is included so the agent never does date arithmetic of its own - it
    applies a state and knows when to stop trusting it.
    """
    schedule = schedule_of(device)
    local_now = now.astimezone(tz)
    upcoming = next_transition(schedule, local_now, tz)
    return {
        "state": "on" if is_lit(schedule, local_now) else "off",
        "until": upcoming.isoformat() if upcoming else None,
        "poll_after_seconds": POLL_AFTER_SECONDS,
    }


def touch_last_seen(session: Session, device: Device, now: datetime) -> None:
    """Record that the panel checked in, at most once per throttle window."""
    previous = device.last_seen
    if previous is not None and previous.tzinfo is None:
        previous = previous.replace(tzinfo=now.tzinfo)
    if previous is not None and now - previous < LAST_SEEN_THROTTLE:
        return
    device.last_seen = now
    session.add(device)
    session.commit()
