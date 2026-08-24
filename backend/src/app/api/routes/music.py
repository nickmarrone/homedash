"""Speakers, the library, and the audio itself.

The only write path in the app, and there is no auth in front of it: the panel
has none, and the household has settled for a LAN-only appliance. Worth knowing
rather than discovering - see the "kid lock" note in CLAUDE.md.
"""

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from pyheos import HeosError

from app.api.serializers import serialize_now_playing
from app.music.heos import TRANSPORT_ACTIONS, HeosController, MusicUnavailable
from app.music.jellyfin import JellyfinError, JellyfinLibrary
from app.music.service import (
    get_controller,
    get_library,
    get_queues,
    get_tokens,
    library_configured,
    music_configured,
)
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


def _controller_or_503() -> HeosController:
    """The music controller, or a clear reason there isn't one.

    Three different states answer 503, and the panel shows none of them - it
    just hides the music UI - so the detail string is written for whoever is
    reading the logs or curling the endpoint.
    """
    if not music_configured():
        raise HTTPException(
            status_code=503,
            detail="music is not configured; set HOMEDASH_MUSIC_ENABLED and HOMEDASH_HEOS_HOST",
        )
    controller = get_controller()
    if controller is None or not controller.connected:
        raise HTTPException(
            status_code=503, detail="not connected to HEOS yet; still retrying"
        )
    return controller


def _library_or_503() -> JellyfinLibrary:
    if not library_configured():
        raise HTTPException(
            status_code=503,
            detail="no music library; set HOMEDASH_JELLYFIN_URL and HOMEDASH_JELLYFIN_API_KEY",
        )
    library = get_library()
    if library is None:
        raise HTTPException(status_code=503, detail="music library not started")
    return library


def _speaker_command_failed(exc: Exception, player_id: int) -> HTTPException:
    """Turn a failed speaker command into the status that describes it.

    Only KeyError was handled before, so everything else - a speaker that has
    dropped off wifi, a system mid-reconnect, a command the firmware refused -
    escaped as a 500 with a stack trace. The panel hides that well: the button
    simply does nothing.

    None of these are the caller's fault, so none of them is a 4xx except the
    one that genuinely is: asking for a speaker that is not there.
    """
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail=f"no player with id {player_id}")
    if isinstance(exc, MusicUnavailable):
        return HTTPException(status_code=503, detail=str(exc) or "not connected to HEOS")
    return HTTPException(status_code=502, detail=f"the speaker refused the command: {exc}")


# -- reads -----------------------------------------------------------------


@router.get("/api/music/players")
def get_music_players() -> dict:
    """Every speaker, with what it is doing right now.

    Always 200 when music is configured, even before the connection is up, so
    the panel can tell "no speakers yet" from "this panel has no music at all"
    without treating an error as the answer. `connected` is what it switches on.
    """
    if not music_configured():
        raise HTTPException(
            status_code=503,
            detail="music is not configured; set HOMEDASH_MUSIC_ENABLED and HOMEDASH_HEOS_HOST",
        )
    controller = get_controller()
    connected = controller is not None and controller.connected
    queues = get_queues()
    players = controller.players() if controller is not None else []
    for player in players:
        # What HomeDash is holding for this speaker, which the speaker itself
        # cannot report: as far as it knows it was handed one stream.
        player["queue"] = queues.snapshot(player["id"]) if queues is not None else None
        if queues is not None:
            track = queues.current(player["id"])
            if track is not None:
                player["now_playing"] = serialize_now_playing(
                    track, player.get("now_playing")
                )
    return {
        "connected": connected,
        "library": library_configured(),
        "players": players,
    }


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
    library = get_library()
    if tokens is None or library is None:
        raise HTTPException(status_code=503, detail="music library not started")

    track_id = tokens.resolve(token)
    if track_id is None:
        raise HTTPException(status_code=404, detail="unknown or expired stream token")

    try:
        upstream, client = await library.open_stream(track_id, request.headers.get("range"))
    except JellyfinError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None

    async def body():
        # The client outlives this function, so it is closed here rather than
        # in a context manager: returning a StreamingResponse means the bytes
        # are pulled long after the handler has returned.
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    passthrough = {
        name: upstream.headers[name]
        for name in ("content-length", "content-range", "accept-ranges")
        if name in upstream.headers
    }
    return StreamingResponse(
        body(),
        status_code=upstream.status_code,
        media_type=upstream.headers.get("Content-Type", "audio/mpeg"),
        headers=passthrough,
    )


# -- writes ----------------------------------------------------------------


@router.post("/api/music/players/{player_id}/transport")
async def post_music_transport(player_id: int, body: TransportRequest) -> dict:
    """Play, pause, stop, next or previous on one speaker.

    Skips are handled by HomeDash's own queue when there is one. Content sent
    to a speaker as a URL never enters the speaker's queue, so HEOS's
    `play_next` has nothing to move to and does nothing at all - a skip button
    that silently did nothing is exactly the kind of fault a wall panel hides
    well. A speaker playing from its own sources still falls through to it.
    """
    if body.action not in TRANSPORT_ACTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"action must be one of {', '.join(TRANSPORT_ACTIONS)}",
        )
    controller = _controller_or_503()
    queues = get_queues()

    try:
        if queues is not None and body.action in ("next", "previous"):
            handled = await (
                queues.next(player_id)
                if body.action == "next"
                else queues.previous(player_id)
            )
            if handled:
                return {"ok": True}
        if queues is not None and body.action == "stop":
            # Before the command, not after: the speaker reports a finished
            # track and a deliberate stop identically, so the queue has to be
            # gone before the resulting `stop` event arrives or it would
            # helpfully start the next track on somebody who asked for silence.
            # `stop` rather than `clear` so that a track change already on its
            # way to the speaker finishes first - see app/music/queue.py.
            await queues.stop(player_id)
        await controller.transport(player_id, body.action)
    except (KeyError, MusicUnavailable, HeosError) as exc:
        raise _speaker_command_failed(exc, player_id) from None
    return {"ok": True}


@router.post("/api/music/players/{player_id}/volume")
async def post_music_volume(player_id: int, body: VolumeRequest) -> dict:
    """Set one speaker's volume, 0-100."""
    controller = _controller_or_503()
    try:
        await controller.set_volume(player_id, body.level)
    except (KeyError, MusicUnavailable, HeosError) as exc:
        raise _speaker_command_failed(exc, player_id) from None
    return {"ok": True}


@router.post("/api/music/players/{player_id}/play")
async def post_music_play(player_id: int, body: PlayRequest) -> dict:
    """Start an album, or an explicit list of tracks, on one speaker.

    HomeDash holds the resulting queue and feeds the speaker one track at a
    time - see app/music/queue.py for why there is no way to hand over the
    whole album at once.
    """
    if body.album_id is None and body.track_ids is None:
        raise HTTPException(status_code=400, detail="pass either album_id or track_ids")

    controller = _controller_or_503()
    library = _library_or_503()
    queues = get_queues()
    if queues is None:
        raise HTTPException(status_code=503, detail="music library not started")
    if player_id not in {p["id"] for p in controller.players()}:
        raise HTTPException(status_code=404, detail=f"no player with id {player_id}")

    try:
        if body.album_id is not None:
            tracks = library.tracks(body.album_id)
        else:
            if body.parent_album_id is None:
                raise HTTPException(
                    status_code=400, detail="track_ids requires parent_album_id"
                )
            by_id = {t.id: t for t in library.tracks(body.parent_album_id)}
            tracks = [by_id[t] for t in (body.track_ids or []) if t in by_id]
    except JellyfinError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None

    if not tracks:
        raise HTTPException(status_code=404, detail="nothing to play")

    try:
        await queues.start(player_id, tracks)
    except UrlTooLong as exc:
        # 500, and loudly: this is configuration, not a bad request, and it is
        # the failure that otherwise presents as a speaker playing silence.
        raise HTTPException(status_code=500, detail=str(exc)) from None

    return {"ok": True, "queued": len(tracks)}
