"""The wire shape for an event instance.

/api/agenda and /api/calendar must agree on every field they share, because
the panel renders both from one TypeScript type - so the shared core lives
here rather than being spelled out at each call site where the two could
quietly drift apart.

Each endpoint then adds what only it can know, through `**extra`: the grid
adds `continues_before`/`continues_after`, the agenda adds `agenda_date`.
Those are additions to the core, never redefinitions of it.

`serialize_now_playing` is here for the same reason: it is a wire shape, and
the route that serves it should be turning a call into a status rather than
deciding what a field is called.
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


def serialize_now_playing(track, reported: dict | None) -> dict:
    """What is playing, according to the side that actually knows.

    A speaker handed a bare URL has no metadata for it, so it falls back to
    describing the stream - which is why the panel showed a bitrate and a codec
    where the song title goes, and no cover at all. Whenever HomeDash owns the
    queue it knows exactly which Jellyfin track it sent, so it answers for the
    speaker rather than repeating what the speaker guessed.

    The one thing still taken from the speaker is the position: it is the only
    party that knows how far into the track it is.

    Art is a HomeDash URL rather than a Jellyfin one for the same reason the
    audio is proxied - the API key must never reach the browser.
    """
    return {
        "title": track.title,
        "artist": track.artist,
        "album": track.album,
        "image_url": f"/api/music/art/{track.album_id}" if track.album_id else None,
        # Jellyfin's duration is authoritative; the speaker usually reports 0
        # for a URL stream, which would hide the progress bar entirely.
        "duration_ms": track.duration_ms or (reported or {}).get("duration_ms"),
        "position_ms": (reported or {}).get("position_ms"),
    }
