#!/usr/bin/env python3
"""Turns the wall panel's screen on and off on HomeDash's schedule.

Polls GET /api/devices/{id}/screen and applies whatever state comes back. The
server owns the schedule and all the date arithmetic - this agent only asks
"on or off?" and does that. Standard library only, so there is nothing to
pip-install on the Pi.

    screen_agent.py run                 poll forever, applying the schedule
    screen_agent.py probe               try each blanking mechanism, in turn
    screen_agent.py status              print what the server says right now

The Pi 5 makes this harder than the older guides suggest. `vcgencmd
display_power` was removed with the move to Wayland/labwc and now answers
"Command not registered", and a USB-C portable monitor exposes no
/sys/class/backlight node either - so the two mechanisms most kiosk guides
reach for are both unavailable. What is left is asking whatever owns the
display: wlopm or wlr-randr under labwc, and GNOME's own screensaver on a
desktop image, where Mutter implements neither of those protocols.

Portable monitors vary in whether they honour that: some sleep properly, some
show a floating "No Signal" logo, some ignore it entirely. `probe` is how you
find out which one you have, before trusting a schedule to it.
"""

import argparse
import json
import logging
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

logger = logging.getLogger("homedash-screen")

DEFAULT_SERVER = "http://homedash.local:8000"
DEFAULT_DEVICE_ID = 1

# Used when the server is unreachable and its poll_after_seconds is unknown.
FALLBACK_POLL_SECONDS = 30

# Longest gap between polls when the server is down, reached by backing off.
# The panel is a wall display: a few minutes of a stale screen state is a far
# smaller problem than a Pi hammering a server that is already unwell.
MAX_POLL_SECONDS = 300


class Mechanism:
    """One way of asking the display to turn off."""

    def __init__(
        self,
        name: str,
        tool: str,
        off: list[str],
        on: list[str],
        note: str,
        check=None,
    ):
        self.name = name
        self.tool = tool
        self.off = off
        self.on = on
        self.note = note
        # An extra test for mechanisms whose tool being installed says nothing
        # about whether it can work here - see gnome_screensaver_present.
        self.check = check

    def available(self) -> bool:
        if shutil.which(self.tool) is None:
            return False
        return self.check is None or self.check()

    def apply(self, state: str, dry_run: bool = False) -> bool:
        command = self.on if state == "on" else self.off
        if dry_run:
            logger.info("[dry-run] would run: %s", " ".join(command))
            return True
        try:
            subprocess.run(command, check=True, capture_output=True, timeout=10)
            return True
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.decode(errors="replace").strip()
            logger.error("%s failed: %s", " ".join(command), stderr)
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.error("%s failed: %s", " ".join(command), exc)
        return False


def gnome_screensaver_present() -> bool:
    """True when there is a GNOME screensaver on the session bus to talk to.

    gdbus ships with glib and exists on machines with no GNOME at all, so
    `which gdbus` alone would make this mechanism look available in a labwc
    session where it can only ever fail. Ask the bus instead.
    """
    if shutil.which("gdbus") is None:
        return False
    try:
        result = subprocess.run(
            [
                "gdbus", "introspect", "--session",
                "--dest", "org.gnome.ScreenSaver",
                "--object-path", "/org/gnome/ScreenSaver",
            ],
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


# Ordered by preference. The GNOME one goes first because its availability
# check is the only exact one: the others test that a binary is installed,
# which on a desktop image says nothing about whether it can work.
#
# wlopm speaks the wlr-output-power-management protocol, which is what labwc
# implements and is the closest thing to a real DPMS off. wlr-randr disables
# the output instead - a bigger hammer, and on some panels the only one that
# actually darkens them, but it can disturb window geometry. Mutter (GNOME)
# implements neither protocol, so on a desktop image both are dead ends no
# matter how they are installed.
MECHANISMS = [
    Mechanism(
        "gnome",
        "gdbus",
        off=[
            "gdbus", "call", "--session",
            "--dest", "org.gnome.ScreenSaver",
            "--object-path", "/org/gnome/ScreenSaver",
            "--method", "org.gnome.ScreenSaver.SetActive", "true",
        ],
        on=[
            "gdbus", "call", "--session",
            "--dest", "org.gnome.ScreenSaver",
            "--object-path", "/org/gnome/ScreenSaver",
            "--method", "org.gnome.ScreenSaver.SetActive", "false",
        ],
        note=(
            "GNOME's own screensaver, over the session bus. Blanks for certain; "
            "whether GNOME then powers the output down is what probe is for."
        ),
        check=gnome_screensaver_present,
    ),
    Mechanism(
        "wlopm",
        "wlopm",
        off=["wlopm", "--off", "*"],
        on=["wlopm", "--on", "*"],
        note="Wayland output power management. labwc only - Mutter does not implement it.",
    ),
    Mechanism(
        "wlr-randr",
        "wlr-randr",
        off=["wlr-randr", "--output", "HDMI-A-1", "--off"],
        on=["wlr-randr", "--output", "HDMI-A-1", "--on"],
        note="Disables the output. labwc only. Check the output name with `wlr-randr`.",
    ),
    Mechanism(
        "xset",
        "xset",
        off=["xset", "dpms", "force", "off"],
        on=["xset", "dpms", "force", "on"],
        note="X11 only - irrelevant under labwc and GNOME/Wayland; a bare-X fallback.",
    ),
]


def find_mechanism(name: str | None) -> Mechanism | None:
    if name and name != "auto":
        for mechanism in MECHANISMS:
            if mechanism.name == name:
                if not mechanism.available():
                    # Say so now rather than letting every poll fail at exec
                    # time with an errno the journal makes look like a bug.
                    logger.warning(
                        "Mechanism %r is named but does not look usable here: "
                        "check that %s is installed, and that this is running "
                        "somewhere it can reach the display (the gnome "
                        "mechanism needs the desktop session's bus)",
                        name,
                        mechanism.tool,
                    )
                return mechanism
        logger.error(
            "Unknown mechanism %r. Known: %s",
            name,
            ", ".join(m.name for m in MECHANISMS),
        )
        return None
    for mechanism in MECHANISMS:
        if mechanism.available():
            return mechanism
    return None


def fetch_state(server: str, device_id: int, timeout: float = 10.0) -> dict | None:
    url = f"{server.rstrip('/')}/api/devices/{device_id}/screen"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        logger.error("%s returned HTTP %s", url, exc.code)
    except (urllib.error.URLError, OSError) as exc:
        logger.error("Cannot reach %s: %s", url, exc)
    except json.JSONDecodeError:
        logger.error("%s did not return JSON", url)
    return None


def run(args: argparse.Namespace) -> int:
    mechanism = find_mechanism(args.mechanism)
    if mechanism is None and not args.dry_run:
        logger.error(
            "No blanking mechanism available. Under GNOME the agent must run "
            "inside the desktop session; under labwc install wlopm or wlr-randr. "
            "Or run with --dry-run to check the schedule without touching the screen."
        )
        return 1
    if mechanism:
        logger.info("Using mechanism: %s", mechanism.name)

    # Nothing is known about the screen's current state at startup - the Pi may
    # have rebooted with the panel dark - so the first poll always applies,
    # rather than being skipped as a no-op.
    applied: str | None = None
    backoff = FALLBACK_POLL_SECONDS

    while True:
        payload = fetch_state(args.server, args.device_id)
        if payload is None:
            # Leave the screen as it is. A network blip should not black out
            # the kitchen calendar, and it should not light it at 3am either.
            logger.warning("Retrying in %ss", backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, MAX_POLL_SECONDS)
            continue

        backoff = FALLBACK_POLL_SECONDS
        state = payload.get("state")
        if state not in ("on", "off"):
            logger.error("Unexpected state %r; leaving the screen alone", state)
        elif state != applied:
            logger.info("Screen -> %s (until %s)", state, payload.get("until"))
            if mechanism is None:
                logger.info("[dry-run] no mechanism configured; nothing applied")
                applied = state
            elif mechanism.apply(state, dry_run=args.dry_run):
                applied = state

        time.sleep(payload.get("poll_after_seconds") or FALLBACK_POLL_SECONDS)


def probe(args: argparse.Namespace) -> int:
    """Try each mechanism so you can watch the panel and see what it does."""
    print("Probing screen blanking mechanisms.")
    print("Watch the panel during each step and note what happens:")
    print("  - goes properly dark        the mechanism works")
    print('  - shows a "No Signal" logo  the Pi stopped sending, the panel stayed lit')
    print("  - nothing at all            the panel ignored it\n")

    available = [m for m in MECHANISMS if m.available()]
    if not available:
        print("Nothing usable here.")
        print("  under GNOME: the agent must run inside the desktop session, so that")
        print("    org.gnome.ScreenSaver is on its bus - check with `systemctl --user`,")
        print("    not `sudo systemctl`.")
        print("  under labwc:  sudo apt install wlopm wlr-randr")
        return 1

    for mechanism in MECHANISMS:
        status = "installed" if mechanism.available() else "NOT INSTALLED"
        print(f"  {mechanism.name:<10} {status:<15} {mechanism.note}")
    print()

    for mechanism in available:
        input(f"Press Enter to turn the screen OFF with {mechanism.name}... ")
        mechanism.apply("off")
        time.sleep(args.pause)
        print(f"  ...turning back on with {mechanism.name}")
        mechanism.apply("on")
        print()

    print("Report which mechanism darkened the panel, and it becomes the default.")
    print(f"Until then, set Mechanism= in the systemd unit or pass --mechanism NAME.")
    return 0


def status(args: argparse.Namespace) -> int:
    payload = fetch_state(args.server, args.device_id)
    if payload is None:
        return 1
    print(json.dumps(payload, indent=2))
    mechanism = find_mechanism(args.mechanism)
    print(f"\nmechanism: {mechanism.name if mechanism else 'none available'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["run", "probe", "status"])
    parser.add_argument("--server", default=DEFAULT_SERVER, help=f"default: {DEFAULT_SERVER}")
    parser.add_argument("--device-id", type=int, default=DEFAULT_DEVICE_ID)
    parser.add_argument(
        "--mechanism",
        default="auto",
        help="auto (default), or one of: " + ", ".join(m.name for m in MECHANISMS),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="log what would be run without touching the screen",
    )
    parser.add_argument(
        "--pause", type=float, default=5.0, help="probe: seconds to hold each state"
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(message)s",
        stream=sys.stdout,
    )

    if args.command == "run":
        return run(args)
    if args.command == "probe":
        return probe(args)
    return status(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
