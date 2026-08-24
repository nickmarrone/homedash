"""The wire shape for an event instance.

/api/agenda and /api/calendar must agree on every field they share, because
the panel renders both from one TypeScript type - so the shared core lives
here rather than being spelled out at each call site where the two could
quietly drift apart.

Each endpoint then adds what only it can know, through `**extra`: the grid
adds `continues_before`/`continues_after`, the agenda adds `agenda_date`.
Those are additions to the core, never redefinitions of it.
"""

from zoneinfo import ZoneInfo

from app.calendars.localtime import to_local
from app.models import CalendarSource, EventInstance


def serialize_instance(
    instance: EventInstance,
    source: CalendarSource | None,
    tz: ZoneInfo,
    **extra: object,
) -> dict:
    """One agenda/grid item, with times already in the home timezone.

    `source` is optional because the join is outer: an instance whose event or
    source has gone missing should still render, uncolored, rather than
    silently vanish from the panel.

    `**extra` is for the caller's own fields - see the module docstring. It
    comes last on purpose: a caller cannot use it to overwrite a core field
    without that being visible right here.
    """
    return {
        "id": instance.id,
        "title": instance.title,
        "location": instance.location,
        "all_day": instance.all_day,
        "starts_at": to_local(instance.starts_at, tz, all_day=instance.all_day).isoformat(),
        "ends_at": to_local(instance.ends_at, tz, all_day=instance.all_day).isoformat(),
        "calendar": (
            {"id": source.id, "name": source.name, "color": source.color} if source else None
        ),
        **extra,
    }
