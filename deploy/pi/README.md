# The wall panel

Turning a Raspberry Pi into a HomeDash display that boots straight into the
calendar and turns its screen off at bedtime.

The Pi is a **thin client**. HomeDash itself runs in Docker somewhere else — a
NAS, a home server, whatever already runs your containers. The Pi runs a
browser and a ~250-line agent, nothing more, so the display can be replaced or
duplicated without touching the server.

---

## What you need

| | |
|---|---|
| Pi | A Pi 4 or 5. This was built against a **Pi 5**. |
| OS | **Ubuntu Desktop** or Raspberry Pi OS **Lite (64-bit)**. Both work; they are provisioned differently and `setup.sh` works out which you have. |
| Display | Any HDMI or USB-C monitor. Touch optional but expected. |
| Network | The Pi must be able to reach your HomeDash server. |

---

## Two axes

Everything here varies along exactly two lines, and `setup.sh` decides the
first for you.

**Session** — who owns the display. Detected; override with `--session`.

| | `gnome` | `console` |
|---|---|---|
| image | Ubuntu Desktop, or any image with GDM | Pi OS Lite, Ubuntu Server |
| compositor | GNOME's, already running | labwc, started by HomeDash on tty1 |
| units | `systemctl --user`, tied to the desktop session | system units, tied to `multi-user.target` |
| autologin | GDM | `getty@tty1` |

The two are not interchangeable. Installing the console path onto a desktop
image is the single most confusing way to break this: HomeDash starts a second
compositor that loses a fight with GDM for the seat every five seconds, and
sets the default target to `multi-user`, so the Pi boots to a text console
instead of a desktop. If you have done that, `setup.sh` now undoes it — see
[Recovery](#recovery).

**Mode** — how locked down. Choose with `--mode`.

| | `simple` (default) | `locked` |
|---|---|---|
| browser | `--start-fullscreen` | `--kiosk` |
| escapable | yes, it's a normal window | no |
| desktop reachable | yes | no |
| enterprise policy | none | `URLBlocklist: ["*"]` bar HomeDash |

**Start with `simple`.** A wall panel you cannot get out of is also a wall
panel you cannot debug, and the enterprise policy applies to *every* Chromium
on the machine — including one you launch by hand to check something, which
then looks broken for no visible reason. Move to `locked` once the panel is
hung on the wall and behaving.

---

## Setup

On the Pi:

```bash
git clone https://github.com/nickmarrone/homedash.git
cd homedash
sudo deploy/pi/setup.sh --server http://homedash.local:8000
```

Options:

| flag | meaning |
|---|---|
| `--server URL` | Where HomeDash is. Use the plain IP if mDNS is flaky — see below. |
| `--mode` | `simple` (default) or `locked`. |
| `--session` | `auto` (default), `gnome`, or `console`. |
| `--orientation` | `landscape` (default) or `portrait`. |
| `--frame-top PX` / `--frame-bottom PX` | Pixels a physical frame hides along the top and bottom of the screen (default 0). The panel lays itself out inside what is left. |
| `--user NAME` | Account the kiosk runs as. Defaults to whoever ran `sudo`. |
| `--skip-packages` | Don't install anything. |
| `--uninstall` | Remove all of it and stop. |

It is idempotent — re-run it to change the server URL, the mode, the
orientation, or the frame margins (see
[Fitting the panel to a frame](#fitting-the-panel-to-a-frame) for a panel
that is already installed). Check the summary it prints: it names the session
it detected and the browser it resolved, which is where a surprise shows up
first.

Then start it without rebooting:

```bash
systemctl --user start homedash-kiosk homedash-screen   # gnome session
sudo systemctl start homedash-kiosk homedash-screen     # console session
```

The user units must be started **as the kiosk user from the desktop session**,
not over `sudo` — a user unit started from a root shell has no session to
attach to.

### Ubuntu's Chromium is a snap, and it matters

`apt install chromium` on Ubuntu gives you a snap, and the snap's `home`
interface allows non-hidden paths under `$HOME` and nothing else. Any
`--user-data-dir` outside that — `/tmp`, `~/.config`, anywhere — is refused,
and Chromium exits in milliseconds. Restarted on a timer, that reads exactly
like a crash loop rather than a rejected flag.

So `setup.sh` resolves the browser once and records whether it is confined;
a confined browser is given no profile directory at all and uses the one
inside its own confinement. If you ever launch Chromium by hand here to
reproduce something, leave `--user-data-dir` off for the same reason.

### If `homedash.local` doesn't resolve

mDNS is the most common "it worked on my laptop" failure. Check it from the Pi:

```bash
curl -sf http://homedash.local:8000/healthz && echo OK
```

If that hangs or fails, use the server's IP address instead and re-run
`setup.sh`. In `locked` mode the URL is baked into the Chromium allowlist as
well as the units, so it must be the address the panel will actually use — a
policy allowing `homedash.local` will block a page loaded from `192.168.1.x`.

---

## Fitting the panel to a frame

A frame over the monitor hides a strip of the screen along the top and bottom.
Tell the panel how much, and it lays itself out inside what is left: the
masthead moves down, the now-playing bar stops above the bottom edge, and the
music screen fits between the two. Photos stay full-bleed, since a frame over
a photograph is just a mat.

The two margins are query parameters on the panel's URL, in CSS pixels — on
this 1080p monitor at 100% scaling, those are screen pixels.

### 1. Find the numbers

Nothing needs installing to try values. On the panel, or in any browser
pointed at the server, open:

```
http://homedash.local:8000/?frame_top=24&frame_bottom=24
```

Change the numbers and reload until the date at the top and the bar at the
bottom sit just clear of the wood. A value that is not a number is ignored,
and anything over 400 is capped there. Stick to whole numbers: `setup.sh`
accepts nothing else.

The bottom margin is easiest to judge with music playing, because that is
when the bar is on screen. With nothing playing and a music library
configured, the **Music** button at the bottom right does the same job.

### 2. Make them stick

Re-run `setup.sh` with the same options as last time, plus the two new ones.
Leaving an option out does not keep its old value; it puts it back to the
default. If you don't remember what you used, the installed script records
it:

```bash
grep -E '^(SERVER_URL|ORIENTATION|SESSION|MODE|FRAME_TOP|FRAME_BOTTOM)=' \
  /usr/local/bin/homedash-kiosk-start
```

`SESSION` only matters if you passed `--session` yourself. The default,
`auto`, detects the same answer again. Then, from a checkout that includes
this change:

```bash
cd homedash && git pull
sudo deploy/pi/setup.sh --server http://homedash.local:8000 \
  --orientation portrait --mode simple \
  --frame-top 24 --frame-bottom 24
```

Check the summary's `frame:` line, then restart the kiosk at once. The
browser is still running the old copy of the start script, and keeps its old
URL until it restarts:

```bash
systemctl --user restart homedash-kiosk       # gnome session, as the kiosk user, from the desktop
sudo systemctl restart homedash-kiosk         # console session
```

To confirm, the kiosk logs the URL it opened:

```bash
journalctl --user -u homedash-kiosk -n 5      # gnome session
sudo journalctl -u homedash-kiosk -n 5        # console session
```

Look for `starting chromium at http://homedash.local:8000/?frame_top=24&frame_bottom=24`.

A panel installed **before** these flags existed must go through `setup.sh`.
Its start script has no `FRAME_TOP` line and nothing that builds the URL, so
there is nothing to edit by hand.

### Nudging the numbers later

On a panel installed with these flags, the margins are two lines near the top
of `/usr/local/bin/homedash-kiosk-start`:

```bash
FRAME_TOP="24"      # CSS px a physical frame hides, top and bottom
FRAME_BOTTOM="24"
```

Editing them with `sudo` and restarting `homedash-kiosk` is the quick way to
move a few pixels. The next `setup.sh` run overwrites that file, though, so
pass the final values to `setup.sh` as well, or the edit is lost. Setting both
to `0` removes the margins.

On a `--mode locked` panel the Chromium allowlist already admits any query
string on the server's URL, so the margins need no policy change.

---

## Recovery

Something wrong, or the Pi booting to a console after an early run of this
script:

```bash
sudo deploy/pi/setup.sh --uninstall
sudo reboot
```

That removes both sets of units, the scripts, the Chromium policy from all
three directories it might live in, the tty1 autologin, and puts the GDM
config back as it was. It also restores `graphical.target` as the default, so
a desktop image comes back up as a desktop.

Re-running `setup.sh` normally repairs the same damage without uninstalling —
if it finds a desktop image booting to `multi-user.target`, it says so and puts
it back.

---

## The screen schedule

The schedule lives on the **server**, in `HOMEDASH_SCREEN_SCHEDULE`, so the Pi
holds no configuration of its own and does no date arithmetic. The agent polls
`/api/devices/1/screen` every 30 seconds and applies whatever it's told.

### You have to find the blanking mechanism first

**The agent ships in `--dry-run` and will not touch your screen until you
change that.** This is not caution for its own sake — on this hardware every
mechanism the usual guides reach for is unavailable:

| The usual advice | What actually happens here |
|---|---|
| `vcgencmd display_power 0` | **Gone.** Answers `Command not registered`. Retired in the move to Wayland. |
| `/sys/class/backlight/*/bl_power` | Only exists for the official DSI panel. An HDMI or USB-C monitor has no such node. |
| `xset dpms force off` | X11 only. Both sessions here are Wayland. |
| `wlopm` / `wlr-randr` | Speak wlr-output-power-management. **labwc implements it; Mutter does not** — so on Ubuntu Desktop these are dead ends however you install them. |

What is left depends on the session: `wlopm`/`wlr-randr` under labwc, and
GNOME's own screensaver (`org.gnome.ScreenSaver` on the session bus) under
GNOME. And portable monitors vary in whether they honour any of it — some
sleep properly, some show a floating "No Signal" logo, some ignore it.

So find out which yours is:

```bash
homedash-screen-agent probe
```

It walks through each usable mechanism, turning the screen off and back on with
a pause so you can watch. Note which one **actually darkens the panel**, then
edit the unit:

```bash
systemctl --user edit --full homedash-screen   # or sudo systemctl, console session
#   ExecStart=... run --server URL --dry-run
# becomes
#   ExecStart=... run --server URL --mechanism gnome
systemctl --user restart homedash-screen
```

If `probe` reports nothing usable on a desktop image, the agent is running
outside the desktop session and cannot see `org.gnome.ScreenSaver`. That is a
`systemctl --user` / `sudo systemctl` mix-up, not a missing package.

Useful while you're working it out:

```bash
homedash-screen-agent status                     # what the server says right now
journalctl --user -u homedash-screen -f          # what the agent is doing about it
```

If **nothing** darkens the panel, the monitor ignores output power management.
That is survivable: the panel already renders a full-screen black page whenever
the schedule says the screen should be off, so the kitchen goes dark either way.
The backlight staying on is the only difference, which is a power and
panel-lifetime question rather than a "the calendar is glowing at 2am" one.

That fallback needs no configuration and is not a mode you switch on — it
follows the same `HOMEDASH_SCREEN_SCHEDULE` the agent does, over the SSE
heartbeat, so the browser and the agent can never disagree about bedtime. It is
also deliberately not dismissable by touch.

### GNOME's own idle blanking

Left alone, GNOME dims, blanks and locks the panel on a schedule of its own,
which looks precisely like the screen agent misbehaving. `kiosk-start.sh` turns
all three off at startup — `idle-delay`, `lock-enabled`,
`sleep-inactive-ac-type` — and re-applies them on every start, so a poke at
Settings cannot quietly undo it.

---

## Rotation

**Under GNOME**, `--orientation portrait` rotates the panel at startup with
`gdctl`. It turns 270°; for the opposite mount, choose 90° once in Settings →
Displays and startup will leave it alone. It only acts on a single monitor,
because `gdctl set` turns off anything it is not told about.

Two one-time steps finish the job, both run in the desktop session. Save the
rotation, tapping **Keep Changes** when GNOME asks; otherwise the browser opens
while the screen is still turning and does not end up fullscreen:

    gdctl set --persistent --logical-monitor --primary --monitor HDMI-2 --transform 270

And bind the touchscreen to the monitor if taps land as though the screen were
not rotated, using the IDs from `/proc/bus/input/devices` and `gdctl show`
(these are the InnoView's):

    gsettings set org.gnome.desktop.peripherals.touchscreen:/org/gnome/desktop/peripherals/touchscreens/222a:0001/ \
      output "['YCT', 'InnoView', '0x00000000']"

**Under labwc**, `--orientation portrait` applies a `transform 90` to the
output at startup via `wlr-randr`, because labwc has no output-transform
directive in `rc.xml`.

Either way, **check that touch rotated with the display.** This is exactly the
sort of thing that silently doesn't, and a panel whose taps land 90° away is
unusable. Tap the leftmost calendar chip in the legend: it should toggle *that*
calendar.

The layout follows the rotation on its own: the panel uses a CSS
`(orientation: portrait)` media query, so portrait stacks the agenda under the
calendar and collapses the week view to one column with no configuration.

---

## How the lockdown works

In `--mode locked`, four layers, because any one of them alone can be escaped.
In `simple` you get layer 4 only, which is the point.

**1. No way out of the session.** On a console image: Pi OS Lite, console
autologin, labwc from a systemd unit. There is no panel, no menu, and no file
manager to reach. On a desktop image this layer does not exist — GNOME is still
underneath — so a desktop panel is locked by layers 2 and 3 alone.

**2. Browser flags.** `--kiosk`, no error dialogs, no infobars, pinch-zoom and
overscroll-navigation off.

**3. Chromium enterprise policy** — `URLBlocklist: ["*"]` with only your
HomeDash URL allowed, DevTools disabled, incognito disabled. Even a successful
escape from kiosk mode can't load anything else. `setup.sh` writes it to
`/etc/chromium/policies/managed/`, `/etc/chromium-browser/policies/managed/`,
and `/var/snap/chromium/current/policies/managed/`, because which one a given
build reads is not worth guessing at — **confirm it at `chrome://policy`.**

**4. The app itself.** Long-press, text selection, and the context menu are all
suppressed in `frontend/src/routes/+layout.svelte`, and a watchdog reloads the
page if the event stream goes quiet for more than about a minute and a half —
a silently frozen display is the number one kiosk failure mode.

---

## Read-only filesystem — do this last

Once everything works, protect the SD card from power-yank corruption:

```bash
sudo raspi-config          # Pi OS: Performance Options -> Overlay File System
sudo apt install overlayroot && sudo nano /etc/overlayroot.conf   # Ubuntu
```

Ubuntu has no `raspi-config`; `overlayroot` is the equivalent, switched on by
setting `overlayroot="tmpfs"` in that file.

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
| `homedash-kiosk.user.service` | `~/.config/systemd/user/homedash-kiosk.service` (gnome) |
| `homedash-screen.user.service` | `~/.config/systemd/user/homedash-screen.service` (gnome) |
| `homedash-kiosk.service` | `/etc/systemd/system/` (console) |
| `homedash-screen.service` | `/etc/systemd/system/` (console) |
| `chromium-policy.json` | `.../policies/managed/homedash.json` (locked mode only) |

`@PLACEHOLDER@` tokens are substituted by `setup.sh` at install time.

---

## Troubleshooting

**The Pi boots to a text console.** An early run of this script set the default
target to `multi-user` on a desktop image. See [Recovery](#recovery).

**The browser restarts over and over.** Look for the backoff message —
`kiosk-start.sh` gives up hammering after five failures in a minute and prints
the exact command line it tried. Run that by hand and the real error appears.
On Ubuntu the usual cause is a `--user-data-dir` the snap cannot open.

**Black screen, no browser** (console session). `journalctl -u homedash-kiosk -b`.
If labwc exits immediately it usually couldn't take the seat — check `seatd` is
running and the kiosk user is in the `video`, `input`, and `render` groups. If
this is a desktop image, you want `--session gnome` instead.

**Chromium starts but the page is blank.** The server is unreachable, or (in
`locked` mode) the policy allowlist doesn't match the URL. Check
`curl -sf $URL/healthz` from the Pi, and that the allowlist entry is the address
the panel actually loads.

**The panel is stale but the browser is alive.** The watchdog should reload it
within ~100 seconds. If it doesn't, the heartbeat isn't arriving —
`curl -N $URL/api/events/stream` from the Pi should print a `heartbeat` event
every 30 seconds.

**The screen never turns off.** Expected until you've done the `probe` step
above and removed `--dry-run`. `journalctl --user -u homedash-screen` will show
it logging what it would have done.
