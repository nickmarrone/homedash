"""Every HTTP endpoint, assembled from one router per subject.

This was a single 749-line module covering calendar, photos, weather, the SSE
stream and music at once. The split is by what an endpoint is *about*, so the
question "where does the agenda live" has an answer that is not "somewhere in
routes.py".

`router` is still the one thing `main.py` imports, and the paths are unchanged;
this is a move, not a redesign. The pieces that were doing another layer's work
left entirely rather than moving sideways - the Jellyfin proxies are now
`JellyfinLibrary.art`/`open_stream`, and the now-playing wire shape is in
`api/serializers.py` beside the event one.

Settings live in `app.api.deps`, deliberately in one place: five routers with a
module-level `settings` each would mean a test had to know which file an
endpoint happened to land in.
"""

from fastapi import APIRouter

from app.api.routes import calendar, music, photos, system, weather

# Order is presentation only - it decides how the generated docs read, not how
# requests match, since no two of these declare the same path.
router = APIRouter()
router.include_router(system.router)
router.include_router(calendar.router)
router.include_router(weather.router)
router.include_router(photos.router)
router.include_router(music.router)

# `event_stream` is imported directly by test_heartbeat, which drives the
# generator rather than opening a stream that never ends over HTTP.
event_stream = system.event_stream

__all__ = ["router", "event_stream"]
