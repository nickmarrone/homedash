# The wall panel

Turning a Raspberry Pi into a HomeDash display that boots straight into the
calendar, can't be escaped from, and turns its screen off at bedtime.

The Pi is a **thin client**. HomeDash itself runs in Docker somewhere else — a
NAS, a home server, whatever already runs your containers. The Pi runs a
browser and a ~200-line agent, nothing more, so the display can be replaced or
duplicated without touching the server.

---

## What you need

| | |
|---|---|
| Pi | A Pi 4 or 5. This was built against a **Pi 5**. |
| Card | Raspberry Pi OS **Lite (64-bit)**, Bookworm or newer. Not the desktop image. |
| Display | Any HDMI or USB-C monitor. Touch optional but expected. |
| Network | The Pi must be able to reach your HomeDash server. |

Deliberately **no keyboard**. It removes most of the attack surface for free,
and there is nothing to type once this is set up.

---

## Setup

Flash Raspberry Pi OS Lite and enable SSH in the imager (there's no keyboard).
Then, on the Pi:

```bash
git clone https://github.com/nickmarrone/homedash.git
cd homedash
sudo deploy/pi/setup.sh --server http://homedash.local:8000
```

Options:

| flag | meaning |
|---|---|
| `--server URL` | Where HomeDash is. Use the plain IP if mDNS is flaky — see below. |
| `--orientation` | `landscape` (default) or `portrait`. |
| `--user NAME` | Account the kiosk runs as. Defaults to whoever ran `sudo`. |
| `--skip-packages` | Don't `apt install` anything. |

It is idempotent — re-run it to change the server URL or the orientation.

Then start it without rebooting:

```bash
sudo systemctl start homedash-kiosk homedash-screen
```

### If `homedash.local` doesn't resolve

mDNS is the most common "it worked on my laptop" failure. Check it from the Pi:

```bash
curl -sf http://homedash.local:8000/healthz && echo OK
```

If that hangs or fails, use the server's IP address instead and re-run
`setup.sh`. The URL is baked into the Chromium allowlist as well as the units,
so it must be the address the panel will actually use — a policy allowing
`homedash.local` will block a page loaded from `192.168.1.x`.

---

## The screen schedule

The schedule lives on the **server**, in `HOMEDASH_SCREEN_SCHEDULE`, so the Pi
holds no configuration of its own and does no date arithmetic. The agent polls
`/api/devices/1/screen` every 30 seconds and applies whatever it's told.

### You have to find the blanking mechanism first

**The agent ships in `--dry-run` and will not touch your screen until you
change that.** This is not caution for its own sake — on this hardware the two
mechanisms every kiosk guide reaches for are both unavailable:

| The usual advice | What actually happens on a Pi 5 |
|---|---|
| `vcgencmd display_power 0` | **Gone.** Answers `Command not registered`. Retired when Raspberry Pi OS moved to Wayland/labwc. |
| `/sys/class/backlight/*/bl_power` | Only exists for the official DSI panel. An HDMI or USB-C monitor has no such node. |
| `xset dpms force off` | X11 only. labwc is Wayland. |

What's left is asking the compositor, with `wlopm` or `wlr-randr` — and
portable monitors vary in whether they honour it. Some sleep properly, some
show a floating "No Signal" logo, some ignore it entirely.

So find out which one yours is:

```bash
homedash-screen-agent probe
```

It walks through each installed mechanism, turning the screen off and back on
with a pause so you can watch. Note which one **actually darkens the panel**,
then edit the unit:

```bash
sudo systemctl edit --full homedash-screen
#   ExecStart=... run --server URL --dry-run
# becomes
#   ExecStart=... run --server URL --mechanism wlopm
sudo systemctl restart homedash-screen
```

Useful while you're working it out:

```bash
homedash-screen-agent status              # what the server says right now
journalctl -u homedash-screen -f          # what the agent is doing about it
```

If **nothing** darkens the panel, the monitor ignores output power management.
Say so and the panel can fall back to rendering a full-screen black page — not
as good, since the backlight stays on, but it does make the kitchen dark.

---

## Rotation

`--orientation portrait` applies a `transform 90` to the output at startup.
labwc has no output-transform directive in `rc.xml`, so this is done by asking
the compositor via `wlr-randr` from the kiosk script.

**Check that touch rotated with the display.** wlroots normally maps touch
input to the output transform, but this is exactly the sort of thing that
silently doesn't, and a panel whose taps land 90° away is unusable. Tap the
leftmost calendar chip in the legend: it should toggle *that* calendar. If taps
land somewhere rotated, say so — it needs an explicit input mapping.

The layout follows the rotation on its own: the panel uses a CSS
`(orientation: portrait)` media query, so portrait stacks the agenda under the
calendar and collapses the week view to one column with no configuration.

---

## How the lockdown works

Four layers, because any one of them alone can be escaped.

**1. No desktop.** Pi OS Lite, console autologin, labwc started directly from a
systemd unit. There is no panel, no menu, and no file manager to reach.

**2. Browser flags.** `--kiosk`, no error dialogs, no infobars, pinch-zoom and
overscroll-navigation off, on a throwaway profile that is recreated each boot.

**3. Chromium enterprise policy** — `/etc/chromium/policies/managed/homedash.json`.
The strongest layer and the one most guides skip: `URLBlocklist: ["*"]` with
only your HomeDash URL allowed, DevTools disabled, incognito disabled. Even a
successful escape from kiosk mode can't load anything else.

**4. The app itself.** Long-press, text selection, and the context menu are all
suppressed in `frontend/src/routes/+layout.svelte`, and a watchdog reloads the
page if the event stream goes quiet for more than about a minute and a half —
a silently frozen display is the number one kiosk failure mode.

---

## Read-only filesystem — do this last

Once everything works, protect the SD card from power-yank corruption:

```bash
sudo raspi-config    # Performance Options -> Overlay File System
```

**Genuinely last.** Afterwards the root filesystem resets on every reboot, so
any further configuration is lost and `journalctl` history won't survive either
— which makes debugging the screen mechanism much harder. Get the panel fully
working first.

---

## Files

| file | installed as |
|---|---|
| `setup.sh` | — (run in place) |
| `screen_agent.py` | `/usr/local/bin/homedash-screen-agent` |
| `kiosk-start.sh` | `/usr/local/bin/homedash-kiosk-start` |
| `homedash-kiosk.service` | `/etc/systemd/system/` |
| `homedash-screen.service` | `/etc/systemd/system/` |
| `chromium-policy.json` | `/etc/chromium/policies/managed/homedash.json` |

`@PLACEHOLDER@` tokens are substituted by `setup.sh` at install time.

---

## Troubleshooting

**Black screen, no browser.** `journalctl -u homedash-kiosk -b`. If labwc exits
immediately, it usually couldn't take the seat — check `seatd` is running and
the kiosk user is in the `video`, `input`, and `render` groups.

**Chromium starts but the page is blank.** The server is unreachable, or the
policy allowlist doesn't match the URL. Check `curl -sf $URL/healthz` from the
Pi, and that the allowlist entry is the address the panel actually loads.

**The panel is stale but the browser is alive.** The watchdog should reload it
within ~100 seconds. If it doesn't, the heartbeat isn't arriving —
`curl -N $URL/api/events/stream` from the Pi should print a `heartbeat` event
every 30 seconds.

**The screen never turns off.** Expected until you've done the `probe` step
above and removed `--dry-run`. `journalctl -u homedash-screen` will show it
logging what it would have done.
