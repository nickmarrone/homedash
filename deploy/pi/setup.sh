#!/usr/bin/env bash
# Provision a Raspberry Pi as a HomeDash wall panel.
#
# Idempotent: safe to re-run after changing the server URL, the mode, or the
# orientation. Run it from a checkout on the Pi:
#
#   sudo deploy/pi/setup.sh --server http://homedash.local:8000
#
# Two things vary, and the script works out both:
#
#   session  gnome    a desktop image - GDM and GNOME already own the display,
#                     so the panel rides that session and touches nothing else.
#            console  a Lite/Server image with no desktop, where HomeDash
#                     starts its own compositor on tty1.
#   mode     simple   fullscreen browser, desktop still reachable. The default,
#                     and where you want to be while the panel is new.
#            locked   kiosk, enterprise policy, no way out.
#
# See README.md in this directory for what it does and what it leaves to you.
set -euo pipefail

SERVER_URL="http://homedash.local:8000"
ORIENTATION="landscape"
KIOSK_USER="${SUDO_USER:-pi}"
MODE="simple"
SESSION="auto"
SKIP_PACKAGES=0
UNINSTALL=0

usage() {
  cat <<'USAGE'
usage: sudo setup.sh [options]

  --server URL         HomeDash server (default: http://homedash.local:8000)
  --mode WHICH         simple (default) or locked
  --session WHICH      auto (default), gnome, or console
  --orientation WHICH  landscape (default) or portrait
  --user NAME          account the kiosk runs as (default: the invoking user)
  --skip-packages      don't install any packages
  --uninstall          remove everything this script installs, and stop
  -h, --help           this message
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --server) SERVER_URL="$2"; shift 2 ;;
    --mode) MODE="$2"; shift 2 ;;
    --session) SESSION="$2"; shift 2 ;;
    --orientation) ORIENTATION="$2"; shift 2 ;;
    --user) KIOSK_USER="$2"; shift 2 ;;
    --skip-packages) SKIP_PACKAGES=1; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

if [ "$(id -u)" -ne 0 ]; then
  echo "setup.sh must run as root (use sudo)" >&2
  exit 1
fi

case "$ORIENTATION" in
  landscape|portrait) ;;
  *) echo "--orientation must be landscape or portrait" >&2; exit 2 ;;
esac
case "$MODE" in
  simple|locked) ;;
  *) echo "--mode must be simple or locked" >&2; exit 2 ;;
esac
case "$SESSION" in
  auto|gnome|console) ;;
  *) echo "--session must be auto, gnome, or console" >&2; exit 2 ;;
esac

if ! id "$KIOSK_USER" >/dev/null 2>&1; then
  echo "no such user: $KIOSK_USER (pass --user)" >&2
  exit 1
fi
KIOSK_UID="$(id -u "$KIOSK_USER")"
KIOSK_HOME="$(getent passwd "$KIOSK_USER" | cut -d: -f6)"
USER_UNIT_DIR="$KIOSK_HOME/.config/systemd/user"

# Strip any trailing slash: it ends up in a Chromium URLAllowlist entry, where
# a stray slash changes what the pattern matches.
SERVER_URL="${SERVER_URL%/}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

# --- session detection ----------------------------------------------------
# A display manager, or a desktop shell, means something else is already
# driving the screen. Starting our own compositor next to it does not fail
# cleanly - it fails by losing a fight for the seat every five seconds.
detect_session() {
  if [ -e /lib/systemd/system/gdm3.service ] || \
     [ -e /lib/systemd/system/gdm.service ] || \
     [ -e /usr/lib/systemd/system/gdm3.service ] || \
     [ -e /usr/lib/systemd/system/gdm.service ] || \
     command -v gnome-shell >/dev/null; then
    echo gnome
  else
    echo console
  fi
}
[ "$SESSION" = "auto" ] && SESSION="$(detect_session)"

# --- uninstall ------------------------------------------------------------
# Everything below, backed out. Worth having as a command: this is the second
# thing you want when a panel misbehaves, and reconstructing it from a README
# while looking at a console is not the moment to be improvising.
if [ "$UNINSTALL" -eq 1 ]; then
  say "Removing HomeDash panel configuration"

  systemctl disable --now homedash-kiosk.service homedash-screen.service 2>/dev/null || true
  rm -f /etc/systemd/system/homedash-kiosk.service /etc/systemd/system/homedash-screen.service
  systemctl daemon-reload

  rm -f "$USER_UNIT_DIR/homedash-kiosk.service" \
        "$USER_UNIT_DIR/homedash-screen.service" \
        "$USER_UNIT_DIR/graphical-session.target.wants/homedash-kiosk.service" \
        "$USER_UNIT_DIR/graphical-session.target.wants/homedash-screen.service"

  # The console path takes tty1 and stops the machine reaching a desktop. Both
  # have to go back or the Pi keeps booting to a login prompt.
  rm -f /etc/systemd/system/getty@tty1.service.d/autologin.conf
  rmdir --ignore-fail-on-non-empty /etc/systemd/system/getty@tty1.service.d 2>/dev/null || true
  if [ -e /lib/systemd/system/graphical.target ] && [ "$SESSION" = "gnome" ]; then
    systemctl set-default graphical.target
  fi

  # URLBlocklist:["*"] applies to any Chromium on the machine, so leaving this
  # behind makes a browser you launch by hand look broken.
  rm -f /etc/chromium/policies/managed/homedash.json \
        /etc/chromium-browser/policies/managed/homedash.json \
        /var/snap/chromium/current/policies/managed/homedash.json

  rm -f /usr/local/bin/homedash-kiosk-start /usr/local/bin/homedash-screen-agent

  # Autologin is the one change here that outlives the panel as a security
  # decision rather than a convenience, so put the file back as it was.
  for candidate in /etc/gdm3/custom.conf /etc/gdm/custom.conf; do
    if [ -f "$candidate.homedash.bak" ]; then
      mv "$candidate.homedash.bak" "$candidate"
      echo "  restored $candidate"
    fi
  done

  say "Done"
  echo "  Reboot to get the desktop back: sudo reboot"
  exit 0
fi

# Checked before anything is written, not where it is first used: the screen
# agent is a python3 script and the GDM edit below is one too, so failing here
# is a clean no-op where failing halfway leaves a part-configured machine.
if ! command -v python3 >/dev/null; then
  echo "python3 is required (the screen agent is a python3 script)" >&2
  echo "  sudo apt install python3" >&2
  exit 1
fi

# --- packages -------------------------------------------------------------
# Install one at a time. The names differ between Raspberry Pi OS and Ubuntu,
# and a single unknown name must not abort the whole run.
apt_try() {
  for pkg in "$@"; do
    apt-get install -y --no-install-recommends "$pkg" >/dev/null 2>&1 || \
      echo "note: could not install $pkg (skipping)"
  done
}

if [ "$SKIP_PACKAGES" -eq 0 ]; then
  say "Installing packages"
  apt-get update -qq || true
  apt_try ca-certificates avahi-daemon

  if [ "$SESSION" = "console" ]; then
    # labwc is the Wayland compositor Raspberry Pi OS moved to, and the reason
    # vcgencmd display_power no longer exists. seatd arbitrates device access
    # for a compositor started outside a desktop session.
    #
    # wlopm is not in every release; wlr-randr is, and covers the same job with
    # a bigger hammer. Install whichever exist rather than failing on the one
    # that does not - `screen_agent.py probe` decides between them later.
    apt_try labwc seatd wlr-randr wlopm
    systemctl enable --now seatd 2>/dev/null || true
    usermod -aG video,input,render,seat "$KIOSK_USER" 2>/dev/null || \
      usermod -aG video,input "$KIOSK_USER" 2>/dev/null || true
  fi
  # A desktop image needs no compositor and no seat management - it has both.

  if ! command -v chromium >/dev/null && ! command -v chromium-browser >/dev/null; then
    apt_try chromium chromium-browser
  fi
fi

# --- browser --------------------------------------------------------------
# Resolved once, here, and baked into the kiosk script: whether the browser is
# a snap decides whether it can be given a profile directory at all, and that
# is not a question to re-answer on every restart.
BROWSER=""
for candidate in chromium chromium-browser; do
  if command -v "$candidate" >/dev/null; then BROWSER="$candidate"; break; fi
done
if [ -z "$BROWSER" ]; then
  echo "no chromium found. Install one and re-run:" >&2
  echo "  sudo apt install chromium    # or: sudo snap install chromium" >&2
  exit 1
fi
BROWSER_PATH="$(readlink -f "$(command -v "$BROWSER")")"
case "$BROWSER_PATH" in
  /snap/*) BROWSER_IS_SNAP=1 ;;
  *) BROWSER_IS_SNAP=0 ;;
esac
# /snap/bin/chromium is itself a symlink to snapd's wrapper, so check the entry
# point as well as where it resolves to.
case "$(command -v "$BROWSER")" in
  /snap/bin/*) BROWSER_IS_SNAP=1 ;;
esac

# --- scripts --------------------------------------------------------------
say "Installing scripts"
sed -e "s|@SERVER_URL@|$SERVER_URL|g" \
    -e "s|@ORIENTATION@|$ORIENTATION|g" \
    -e "s|@SESSION@|$SESSION|g" \
    -e "s|@MODE@|$MODE|g" \
    -e "s|@BROWSER@|$BROWSER|g" \
    -e "s|@BROWSER_IS_SNAP@|$BROWSER_IS_SNAP|g" \
  "$HERE/kiosk-start.sh" > /usr/local/bin/homedash-kiosk-start
chmod 0755 /usr/local/bin/homedash-kiosk-start

install -m 0755 "$HERE/screen_agent.py" /usr/local/bin/homedash-screen-agent

# --- chromium enterprise policy -------------------------------------------
# locked mode only. This is the strongest lockdown layer and the one most
# guides skip: with it in place, escaping the browser still cannot load
# anything but HomeDash. It is also the one that will confuse you most while
# the panel is still being set up, which is why simple mode removes it.
if [ "$MODE" = "locked" ]; then
  say "Installing Chromium policy"
  install -d /etc/chromium/policies/managed
  sed -e "s|@SERVER_URL@|$SERVER_URL|g" \
    "$HERE/chromium-policy.json" > /etc/chromium/policies/managed/homedash.json
  chmod 0644 /etc/chromium/policies/managed/homedash.json
  # Debian's chromium and Pi OS's chromium-browser read different directories,
  # and the snap may read a third. Write all of them and confirm on the device
  # at chrome://policy - which one actually applies is not worth guessing at.
  for dir in /etc/chromium-browser/policies/managed \
             /var/snap/chromium/current/policies/managed; do
    case "$dir" in
      /var/snap/*) [ "$BROWSER_IS_SNAP" -eq 1 ] || continue ;;
      *) [ -d "${dir%/policies/managed}" ] || command -v chromium-browser >/dev/null || continue ;;
    esac
    install -d "$dir"
    cp /etc/chromium/policies/managed/homedash.json "$dir/homedash.json"
  done
else
  # Re-running in simple mode after a spell in locked mode has to undo it,
  # or the allowlist quietly outlives the mode that installed it.
  rm -f /etc/chromium/policies/managed/homedash.json \
        /etc/chromium-browser/policies/managed/homedash.json \
        /var/snap/chromium/current/policies/managed/homedash.json
fi

# --- autostart ------------------------------------------------------------
if [ "$SESSION" = "gnome" ]; then
  say "Configuring GDM autologin for $KIOSK_USER"
  # GDM has no drop-in mechanism for custom.conf, so this is an in-place edit.
  GDM_CONF=""
  for candidate in /etc/gdm3/custom.conf /etc/gdm/custom.conf; do
    [ -f "$candidate" ] && { GDM_CONF="$candidate"; break; }
  done
  if [ -n "$GDM_CONF" ]; then
    [ -f "$GDM_CONF.homedash.bak" ] || cp "$GDM_CONF" "$GDM_CONF.homedash.bak"
    KIOSK_USER="$KIOSK_USER" python3 - "$GDM_CONF" <<'PY'
import os, re, sys

path = sys.argv[1]
user = os.environ["KIOSK_USER"]
wanted = {"AutomaticLoginEnable": "true", "AutomaticLogin": user}
lines = open(path).read().splitlines()

out, seen, in_daemon = [], set(), False
for line in lines:
    if line.strip().startswith("["):
        # Leaving [daemon] is the last chance to add what was missing from it.
        if in_daemon:
            out += [f"{k}={v}" for k, v in wanted.items() if k not in seen]
        in_daemon = line.strip().lower() == "[daemon]"
    elif in_daemon:
        # Keys are often shipped commented out; rewrite those in place rather
        # than appending a duplicate below them.
        m = re.match(r"\s*#?\s*(\w+)\s*=", line)
        if m and m.group(1) in wanted:
            key = m.group(1)
            seen.add(key)
            out.append(f"{key}={wanted[key]}")
            continue
    out.append(line)

if in_daemon:
    out += [f"{k}={v}" for k, v in wanted.items() if k not in seen]
elif not any(l.strip().lower() == "[daemon]" for l in lines):
    out += ["", "[daemon]"] + [f"{k}={v}" for k, v in wanted.items()]

open(path, "w").write("\n".join(out) + "\n")
PY
    echo "  $GDM_CONF updated (original kept as $GDM_CONF.homedash.bak)"
  else
    echo "  note: no GDM config found; enable autologin yourself in Settings -> Users"
  fi

  say "Installing user units"
  install -d -o "$KIOSK_USER" -g "$KIOSK_USER" "$USER_UNIT_DIR"
  install -o "$KIOSK_USER" -g "$KIOSK_USER" -m 0644 \
    "$HERE/homedash-kiosk.user.service" "$USER_UNIT_DIR/homedash-kiosk.service"
  sed -e "s|@SERVER_URL@|$SERVER_URL|g" \
    "$HERE/homedash-screen.user.service" > "$USER_UNIT_DIR/homedash-screen.service"
  chown "$KIOSK_USER:$KIOSK_USER" "$USER_UNIT_DIR/homedash-screen.service"
  chmod 0644 "$USER_UNIT_DIR/homedash-screen.service"

  # Write the wants symlink `systemctl --user enable` would have written. It
  # is the same result and, unlike the command, needs no session bus - which
  # root does not have while running this.
  install -d -o "$KIOSK_USER" -g "$KIOSK_USER" \
    "$USER_UNIT_DIR/graphical-session.target.wants"
  for unit in homedash-kiosk homedash-screen; do
    ln -sf "../$unit.service" \
      "$USER_UNIT_DIR/graphical-session.target.wants/$unit.service"
    chown -h "$KIOSK_USER:$KIOSK_USER" \
      "$USER_UNIT_DIR/graphical-session.target.wants/$unit.service"
  done

  # An earlier run of this script on the console path may have left both of
  # these behind, which is what makes a desktop image boot to a text console.
  if [ -e /etc/systemd/system/getty@tty1.service.d/autologin.conf ] || \
     [ "$(systemctl get-default)" != "graphical.target" ]; then
    say "Undoing console-only boot left by an earlier run"
    rm -f /etc/systemd/system/getty@tty1.service.d/autologin.conf
    rmdir --ignore-fail-on-non-empty /etc/systemd/system/getty@tty1.service.d 2>/dev/null || true
    systemctl set-default graphical.target
  fi
  systemctl disable homedash-kiosk.service homedash-screen.service 2>/dev/null || true
  rm -f /etc/systemd/system/homedash-kiosk.service \
        /etc/systemd/system/homedash-screen.service
  systemctl daemon-reload
else
  # --- console autologin --------------------------------------------------
  # The compositor needs a logged-in seat on tty1. This is the Lite-image
  # equivalent of the desktop autologin raspi-config sets up.
  say "Enabling console autologin for $KIOSK_USER"
  install -d /etc/systemd/system/getty@tty1.service.d
  cat > /etc/systemd/system/getty@tty1.service.d/autologin.conf <<EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin $KIOSK_USER --noclear %I \$TERM
EOF
  systemctl set-default multi-user.target

  say "Installing systemd units"
  for unit in homedash-kiosk homedash-screen; do
    sed -e "s|@SERVER_URL@|$SERVER_URL|g" \
        -e "s|@KIOSK_USER@|$KIOSK_USER|g" \
        -e "s|@KIOSK_UID@|$KIOSK_UID|g" \
      "$HERE/$unit.service" > "/etc/systemd/system/$unit.service"
  done
  systemctl daemon-reload
  systemctl enable homedash-kiosk.service homedash-screen.service
fi

say "Done"
cat <<EOF

  server:      $SERVER_URL
  mode:        $MODE
  session:     $SESSION
  orientation: $ORIENTATION
  user:        $KIOSK_USER
  browser:     $BROWSER -> $BROWSER_PATH$([ "$BROWSER_IS_SNAP" -eq 1 ] && echo " (snap)")

EOF

if [ "$SESSION" = "gnome" ]; then
  cat <<EOF
Start it now without rebooting:

  systemctl --user start homedash-kiosk homedash-screen

(as $KIOSK_USER, from the graphical session - not over sudo.)

EOF
else
  cat <<EOF
Start it now without rebooting:

  sudo systemctl start homedash-kiosk homedash-screen

EOF
fi

cat <<EOF
Then, still to do by hand (see README.md):

  1. Confirm the panel reaches the server:
       curl -sf $SERVER_URL/healthz && echo OK
  2. Find out how this panel blanks, and wire it up:
       homedash-screen-agent probe
     The screen agent runs with --dry-run until you do - it will log what it
     would have done and leave the display alone.
  3. If you rotated the screen, check touch input rotated with it.
  4. Once the panel is settled, re-run with --mode locked to lock it down.

Anything wrong? Back all of it out with:

  sudo $HERE/setup.sh --uninstall

EOF
