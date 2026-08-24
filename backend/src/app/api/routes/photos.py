"""The screensaver's playlist, and the derivatives it shows."""

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from sqlmodel import select

from app.api import deps
from app.api.deps import SessionDep
from app.models import Photo
from app.photos.derivatives import (
    PANEL_ORIENTATIONS,
    derivative_path,
    slot_for,
    target_size,
)

router = APIRouter()


def _check_orientation(orientation: str) -> None:
    if orientation not in PANEL_ORIENTATIONS:
        raise HTTPException(
            status_code=400,
            detail=f"orientation must be one of {', '.join(PANEL_ORIENTATIONS)}",
        )


@router.get("/api/photos")
def get_photos(session: SessionDep, orientation: str = Query("landscape")) -> dict:
    """The screensaver's playlist, for one way the panel is mounted.

    The server says what exists and how big it is; the panel owns shuffling,
    pairing and dwell timing. That split keeps the server stateless per panel -
    there is no cursor to resume and no way for a page reload to disagree with
    it - and it puts the slideshow's state where every other panel-local
    preference already lives.

    `slot` is "full" for a photo that agrees with this orientation and "half"
    for one that does not; the panel shows two consecutive halves side by side.

    `v` in each URL is the content hash, which is what lets the image endpoint
    mark its response immutable: a photo replaced in place gets a new URL
    rather than a stale cache entry the panel would keep for a year.
    """
    _check_orientation(orientation)

    photos = session.exec(
        select(Photo)
        .where(Photo.error == None)  # noqa: E711
        .order_by(Photo.id)
        .limit(deps.settings.photo_max_count)
    ).all()

    items = []
    for photo in photos:
        slot = slot_for(photo.orientation, orientation)
        width, height = target_size(orientation, slot)
        items.append(
            {
                "id": photo.id,
                "slot": slot,
                "width": width,
                "height": height,
                "url": (
                    f"/api/photos/{photo.id}/image"
                    f"?orientation={orientation}&v={photo.hash}"
                ),
            }
        )

    return {
        "dwell_seconds": deps.settings.screensaver_dwell_seconds,
        "idle_minutes": deps.settings.screensaver_idle_minutes,
        "photos": items,
    }


@router.get("/api/photos/{photo_id}/image")
def get_photo_image(
    photo_id: int, session: SessionDep, orientation: str = Query("landscape")
) -> FileResponse:
    """One pre-rendered derivative.

    A pure file read - the resize happened at index time. This is the same
    discipline the weather cache states for itself: handlers read what a
    background job prepared, they never do the work on the request.

    The `v` query parameter is deliberately ignored here. It exists to make the
    URL change when the bytes change; validating it would only turn a panel
    holding a slightly stale playlist into a panel showing gaps.
    """
    _check_orientation(orientation)

    photo = session.get(Photo, photo_id)
    if photo is None or photo.error is not None or not photo.hash:
        raise HTTPException(status_code=404, detail=f"no photo with id {photo_id}")

    slot = slot_for(photo.orientation, orientation)
    path = derivative_path(
        deps.settings.photo_cache_dir, photo.hash, target_size(orientation, slot)
    )
    if not path.exists():
        # Indexed but not yet rendered, or the cache was wiped and the next
        # scan has not caught up. 404 and let the panel skip to the next slide;
        # rendering it here would put a multi-second Pillow call on a request
        # the panel makes every few seconds.
        raise HTTPException(status_code=404, detail="derivative not rendered yet")

    return FileResponse(
        path,
        media_type="image/jpeg",
        # Safe to keep forever because the URL carries the content hash. This
        # matters more than usual on a wall panel: the slideshow loops for
        # months, and without it every loop re-fetches the whole library.
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
