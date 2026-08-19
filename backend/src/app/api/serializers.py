"""The wire shape for an event instance.

/api/agenda and /api/calendar must emit identical items - the frontend uses
one renderer for both - so the shape lives here rather than being spelled out
at each call site where the two could quietly drift apart.
"""

from zoneinfo import ZoneInfo

from app.calendars.localtime import to_local
from app.models import CalendarSource, EventInstance, Member


def serialize_instance(
    instance: EventInstance,
    member: Member | None,
    source: CalendarSource | None,
    tz: ZoneInfo,
    **extra: object,
) -> dict:
    """One agenda/grid item, with times already in the home timezone.

    `member` and `source` are optional because both joins are outer: an
    instance whose event or source has gone missing should still render,
    uncolored, rather than silently vanish from the panel.
    """
    return {
        "id": instance.id,
        "title": instance.title,
        "location": instance.location,
        "all_day": instance.all_day,
        "starts_at": to_local(instance.starts_at, tz, all_day=instance.all_day).isoformat(),
        "ends_at": to_local(instance.ends_at, tz, all_day=instance.all_day).isoformat(),
        "member": (
            {"id": member.id, "name": member.name, "color": member.color} if member else None
        ),
        "calendar": (
            {"id": source.id, "name": source.name, "color": source.color} if source else None
        ),
        **extra,
    }
