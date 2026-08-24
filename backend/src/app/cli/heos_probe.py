"""Ask the actual speakers what they are and what they will accept.

    uv run homedash-heos-probe [--host IP] [--play URL] [--timeout SECONDS]

The direct analogue of `deploy/pi/screen_agent.py probe`, and it exists for the
same reason: some of this can only be answered by the hardware. Whether a given
HEOS firmware plays a proxied URL, how it reports a track finishing, and how
long the gap between tracks really is are not things the protocol specification
settles - and each one is invisible from a backend test suite that stays green
while the kitchen stays silent.

With no arguments it connects, enumerates every player on the account, and
prints model and firmware for each - along with how many entries are sitting in
each speaker's own queue, which is the diagnostic for the one failure this
feature has that nothing on the panel can show. `--play URL` additionally sends
one `play_stream` to the first available player, which is the single most
useful check before wiring the panel up: it separates "HomeDash cannot reach
the speakers" from "the speakers will not play what HomeDash is serving".

`--clear-queue` empties every player's queue. HomeDash cleans up after itself
now, so this is for a speaker left confused by a version that did not.
"""

import argparse
import asyncio
import sys

from app.config import get_settings

# The HEOS firmware will not fetch a URL longer than this. Repeated here rather
# than imported so the probe stays usable when the rest of the app is
# misconfigured - this file is the thing you run *because* something is wrong.
MAX_URL_LENGTH = 255


async def _probe(host: str, play: str | None, timeout: float, clear_queue: bool) -> int:
    from pyheos import Heos

    print(f"Connecting to {host} ...")
    try:
        heos = await asyncio.wait_for(
            Heos.create_and_connect(host, timeout=timeout), timeout=timeout + 5
        )
    except Exception as exc:
        print(f"  FAILED: {type(exc).__name__}: {exc}")
        print()
        print("  Any one speaker's address will do - the rest are enumerated over")
        print("  the connection to it. Check that port 1255 is reachable from here;")
        print("  note that SSDP discovery does not cross a Docker bridge network,")
        print("  which is why this is configured rather than discovered.")
        return 1

    try:
        players = await heos.get_players(refresh=True)
        if not players:
            print("  Connected, but the account has no players.")
            return 1

        print(f"  Connected. {len(players)} player(s):")
        print()
        for player in players.values():
            flag = "" if player.available else "   (UNAVAILABLE)"
            print(f"  [{player.player_id}] {player.name}{flag}")
            print(f"        model    {player.model}")
            print(f"        firmware {player.version}")
            print(f"        state    {player.state}  volume {player.volume}")
            media = player.now_playing_media
            if media is not None and media.song:
                print(f"        playing  {media.song} - {media.artist}")
            await _report_queue(player)
            print()

        if clear_queue:
            await _clear_queues(players)
        if play is not None:
            return await _probe_play(players, play)
    finally:
        await heos.disconnect()
    return 0


async def _report_queue(player) -> None:
    """Say how much is sitting in the speaker's own queue, and whose it is.

    `browse/play_stream` appends the URL to this queue, so an album played
    before HomeDash learned to tidy up leaves one entry per track behind. That
    is invisible from the panel and from the backend logs, and it is what a
    speaker that "will not play any more" is usually full of.
    """
    try:
        items = await player.get_queue()
    except Exception as exc:
        print(f"        queue    could not be read: {type(exc).__name__}: {exc}")
        return
    homedash = sum(1 for item in items if "/api/music/s/" in (item.song or ""))
    note = f"  ({homedash} from HomeDash)" if homedash else ""
    print(f"        queue    {len(items)} entr{'y' if len(items) == 1 else 'ies'}{note}")
    if len(items) > 1:
        print("                 more than one means something is not being cleaned up;")
        print("                 --clear-queue empties it.")


async def _clear_queues(players: dict) -> None:
    """Empty every queue, tolerating the ones that are already empty.

    HEOS answers an error rather than a no-op for that, which is worth
    swallowing here for the same reason the app swallows it: somebody running
    this has a speaker that is misbehaving, and a stack trace on the one player
    that was fine helps nobody.
    """
    print("  Clearing player queues:")
    for player in players.values():
        try:
            await player.clear_queue()
        except Exception as exc:
            print(f"    [{player.player_id}] {player.name}: {type(exc).__name__}: {exc}")
        else:
            print(f"    [{player.player_id}] {player.name}: cleared")
    print()


async def _probe_play(players: dict, url: str) -> int:
    """Send one URL to the first available player and report what happened.

    The length check comes first and is not a formality: an over-long URL is
    accepted by the protocol and then silently not played, which is the single
    most confusing failure in this whole feature.
    """
    if len(url) > MAX_URL_LENGTH:
        print(f"  REFUSING: that URL is {len(url)} characters.")
        print(f"  HEOS will not play anything over {MAX_URL_LENGTH}. It does not")
        print("  report an error - it just does not play. Shorten it.")
        return 1

    target = next((p for p in players.values() if p.available), None)
    if target is None:
        print("  No available player to test playback on.")
        return 1

    print(f"  Playing on [{target.player_id}] {target.name}:")
    print(f"    {url}  ({len(url)} chars)")
    try:
        await target.play_url(url)
    except Exception as exc:
        print(f"    FAILED: {type(exc).__name__}: {exc}")
        return 1

    await asyncio.sleep(3)
    await target.refresh()
    print(f"    state after 3s: {target.state}")
    if str(target.state) != "play":
        print()
        print("    The command was accepted but the speaker is not playing. Usually")
        print("    that means it could not fetch the URL: check that the address is")
        print("    one the speaker can route to, not a Docker-internal one.")
        return 1
    print("    Playing.")
    return 0


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--host",
        default=settings.heos_host,
        help="a HEOS speaker's IP (default: HOMEDASH_HEOS_HOST)",
    )
    parser.add_argument("--play", metavar="URL", help="send one stream URL to a player")
    parser.add_argument(
        "--clear-queue",
        action="store_true",
        help="empty every player's own queue (for a speaker left confused by an older build)",
    )
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args()

    if not args.host:
        parser.error("no host: pass --host or set HOMEDASH_HEOS_HOST")

    return asyncio.run(_probe(args.host, args.play, args.timeout, args.clear_queue))


if __name__ == "__main__":
    sys.exit(main())
