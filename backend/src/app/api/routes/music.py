"""Speakers, the library, and the audio itself.

The only write path in the app, and there is no auth in front of it: the panel
has none, and the household has settled for a LAN-only appliance. Worth knowing
rather than discovering - see the "kid lock" note in CLAUDE.md.
"""

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from app.music.base import AudioSourceError
from app.music.heos import TRANSPORT_ACTIONS
from app.music.jellyfin import JellyfinError, JellyfinLibrary
from app.music.playback import (
    LibraryFailed,
    NothingToPlay,
    Playback,
    PlaybackError,
    SpeakerRefused,
    Unavailable,
    UnknownPlayer,
)
from app.music.service import get_playback, get_tokens
from app.music.tokens import UrlTooLong

router = APIRouter()

LIBRARY_KINDS = ("artists", "albums", "tracks")


# -- request bodies --------------------------------------------------------
#
# Declared rather than pulled out of a `dict` and checked by hand. FastAPI
# rejects a bad body with a 422 naming the field, which is a better answer than
# any of the hand-written 400s these replace, and the shapes are then visible in
# the generated schema instead of being buried in the handlers.


class TransportRequest(BaseModel):
    action: str = Field(description=f"One of: {', '.join(TRANSPORT_ACTIONS)}")


class VolumeRequest(BaseModel):
    # Clamping an out-of-range number would hide a caller bug behind a speaker
    # that quietly went to full volume, which is a bad way to find out about it
    # in a kitchen. `strict` keeps `true` from arriving as 1.
    level: int = Field(strict=True, ge=0, le=100)


class PlayRequest(BaseModel):
    album_id: str | None = None
    track_ids: list[str] | None = None
    # Needed only with track_ids: one album fetch and a filter, rather than a
    # request per track. The panel only ever sends a subset of an album it is
    # already looking at, and it sends that album's id alongside.
    parent_album_id: str | None = None


# -- availability ----------------------------------------------------------
#
# Which source answers for a speaker is decided in `app.music.playback`. What
# is left here is turning its answers into statuses.


def _playback_or_503() -> Playback:
    """The music module, or the reason there isn't one.

    The panel shows none of the 503s - it just hides the music UI - so the
    detail string is written for whoever is reading the logs or curling.
    """
    playback = get_playback()
    if playback is None:
        raise HTTPException(
            status_code=503,
            detail="music is not configured; set HOMEDASH_MUSIC_ENABLED and HOMEDASH_HEOS_HOST",
        )
    return playback


def _library_or_503() -> JellyfinLibrary:
    playback = get_playback()
    if playback is None or playback.library is None:
        raise HTTPException(
            status_code=503,
            detail="no music library; set HOMEDASH_JELLYFIN_URL and HOMEDASH_JELLYFIN_API_KEY",
        )
    return playback.library


# None of these are the caller's fault, so none is a 4xx except the ones that
# genuinely are: a speaker that is not there, and an album with nothing in it.
_STATUS: dict[type[PlaybackError], int] = {
    Unavailable: 503,
    UnknownPlayer: 404,
    NothingToPlay: 404,
    SpeakerRefused: 502,
    LibraryFailed: 502,
}


def _http(exc: PlaybackError) -> HTTPException:
    return HTTPException(status_code=_STATUS.get(type(exc), 500), detail=str(exc))


# -- reads -----------------------------------------------------------------


@router.get("/api/music/players")
def get_music_players() -> dict:
    """Every speaker, with what it is doing right now.

    Always 200 when music is configured, even before the connection is up, so
    the panel can tell "no speakers yet" from "this panel has no music at all"
    without treating an error as the answer. `connected` is what it switches on.
    """
    return _playback_or_503().players()


@router.get("/api/music/library")
def get_music_library(
    kind: str = Query("artists"),
    parent: str | None = Query(None),
) -> dict:
    """One level of the library: artists, then albums, then tracks.

    A level at a time rather than a tree. The panel shows one screen at a time
    and a whole music library is far too much to hand it in one response, so
    each call answers exactly what the screen in front of somebody needs.
    """
    if kind not in LIBRARY_KINDS:
        raise HTTPException(status_code=400, detail="kind must be artists, albums or tracks")
    if kind == "tracks" and not parent:
        raise HTTPException(status_code=400, detail="tracks requires a parent album id")

    library = _library_or_503()
    try:
        if kind == "artists":
            items = [
                {"id": a.id, "name": a.name, "sort_name": a.sort_name or a.name}
                for a in library.artists()
            ]
        elif kind == "albums":
            items = [
                {"id": a.id, "name": a.name, "artist": a.artist, "year": a.year}
                for a in library.albums(parent)
            ]
        else:
            items = [
                {
                    "id": t.id,
                    "title": t.title,
                    "artist": t.artist,
                    "album": t.album,
                    "duration_ms": t.duration_ms,
                    "track_number": t.track_number,
                }
                for t in library.tracks(parent or "")
            ]
    except JellyfinError as exc:
        # 502, not 500: the failure is upstream, and saying so is the
        # difference between "check Jellyfin" and "check HomeDash" for whoever
        # is reading the log.
        raise HTTPException(status_code=502, detail=str(exc)) from None

    return {"kind": kind, "parent": parent, "items": items}


@router.get("/api/music/art/{item_id}")
async def get_music_art(item_id: str, size: int = Query(480)) -> Response:
    """One cover image, proxied.

    Proxied rather than linked for the same reason the audio is: the Jellyfin
    API key must never reach the browser. The fetch itself lives on the library
    object; this only turns its answer into a status.
    """
    if not 32 <= size <= 1920:
        raise HTTPException(status_code=400, detail="size must be between 32 and 1920")
    library = _library_or_503()

    try:
        found = await library.art(item_id, size)
    except JellyfinError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    if found is None:
        # 404 rather than passing the upstream status through: a missing cover
        # is an ordinary thing for the panel to handle, and it already falls
        # back to a placeholder.
        raise HTTPException(status_code=404, detail="no cover art")

    content, media_type = found
    return Response(
        content=content,
        media_type=media_type,
        # Not immutable: unlike the photo derivatives, this URL carries no
        # content hash, so replacing a cover in Jellyfin has to be able to win
        # eventually. An hour is long enough that scrolling a library does not
        # refetch, and short enough that a fix shows up the same afternoon.
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/api/music/s/{token}")
async def get_music_stream(token: str, request: Request) -> StreamingResponse:
    """The audio itself. **This endpoint is fetched by the speaker, not the panel.**

    It exists so that no Jellyfin credential ever has to travel in a URL, and
    so that the URL stays under the 255 characters HEOS will fetch. Everything
    about its shape follows from that.
    """
    tokens = get_tokens()
    playback = get_playback()
    library = playback.library if playback is not None else None
    if tokens is None or library is None:
        raise HTTPException(status_code=503, detail="music library not started")

    track_id = tokens.resolve(token)
    if track_id is None:
        raise HTTPException(status_code=404, detail="unknown or expired stream token")

    try:
        stream = await library.open_stream(track_id, request.headers.get("range"))
    except AudioSourceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None

    return StreamingResponse(
        stream.body(),
        status_code=stream.status_code,
        media_type=stream.media_type,
        headers=dict(stream.headers),
    )


# -- writes ----------------------------------------------------------------


@router.post("/api/music/players/{player_id}/transport")
async def post_music_transport(player_id: int, body: TransportRequest) -> dict:
    """Play, pause, stop, next or previous on one speaker.

    Whether HomeDash's queue or the speaker itself carries it out is
    `Playback.transport`'s call - see there.
    """
    if body.action not in TRANSPORT_ACTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"action must be one of {', '.join(TRANSPORT_ACTIONS)}",
        )
    try:
        await _playback_or_503().transport(player_id, body.action)
    except PlaybackError as exc:
        raise _http(exc) from None
    return {"ok": True}


@router.post("/api/music/players/{player_id}/volume")
async def post_music_volume(player_id: int, body: VolumeRequest) -> dict:
    """Set one speaker's volume, 0-100."""
    try:
        await _playback_or_503().set_volume(player_id, body.level)
    except PlaybackError as exc:
        raise _http(exc) from None
    return {"ok": True}


@router.post("/api/music/players/{player_id}/play")
async def post_music_play(player_id: int, body: PlayRequest) -> dict:
    """Start an album, or an explicit list of tracks, on one speaker.

    HomeDash holds the resulting queue and feeds the speaker one track at a
    time - see app/music/queue.py for why there is no way to hand over the
    whole album at once.
    """
    if body.album_id is not None:
        album_id, track_ids = body.album_id, None
    elif body.track_ids is not None:
        if body.parent_album_id is None:
            raise HTTPException(status_code=400, detail="track_ids requires parent_album_id")
        album_id, track_ids = body.parent_album_id, body.track_ids
    else:
        raise HTTPException(status_code=400, detail="pass either album_id or track_ids")

    try:
        queued = await _playback_or_503().play(player_id, album_id, track_ids)
    except PlaybackError as exc:
        raise _http(exc) from None
    except UrlTooLong as exc:
        # 500, and loudly: this is configuration, not a bad request, and it is
        # the failure that otherwise presents as a speaker playing silence.
        raise HTTPException(status_code=500, detail=str(exc)) from None

    return {"ok": True, "queued": queued}
