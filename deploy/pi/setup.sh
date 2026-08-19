#!/usr/bin/env bash
# Provision a Raspberry Pi as a HomeDash wall panel.
#
# Idempotent: safe to re-run after changing the server URL or orientation.
# Run it from a checkout on the Pi:
#
#   sudo deploy/pi/setup.sh --server http://homedash.local:8000
#
# See README.md in this directory for what it does and what it leaves to you.
set -euo pipefail

SERVER_URL="http://homedash.local:8000"
ORIENTATION="landscape"
KIOSK_USER="${SUDO_USER:-pi}"
SKIP_PACKAGES=0

usage() {
  cat <<'USAGE'
usage: sudo setup.sh [options]

  --server URL         HomeDash server (default: http://homedash.local:8000)
  --orientation WHICH  landscape (default) or portrait
  --user NAME          account the kiosk runs as (default: the invoking user)
  --skip-packages      don't apt-install anything
  -h, --help           this message
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --server) SERVER_URL="$2"; shift 2 ;;
    --orientation) ORIENTATION="$2"; shift 2 ;;
    --user) KIOSK_USER="$2"; shift 2 ;;
    --skip-packages) SKIP_PACKAGES=1; shift ;;
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

if ! id "$KIOSK_USER" >/dev/null 2>&1; then
  echo "no such user: $KIOSK_USER (pass --user)" >&2
  exit 1
fi
KIOSK_UID="$(id -u "$KIOSK_USER")"

# Strip any trailing slash: it ends up in a Chromium URLAllowlist entry, where
# a stray slash changes what the pattern matches.
SERVER_URL="${SERVER_URL%/}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

# --- packages -------------------------------------------------------------
if [ "$SKIP_PACKAGES" -eq 0 ]; then
  say "Installing packages"
  apt-get update -qq
  # labwc is the Wayland compositor Raspberry Pi OS moved to, and the reason
  # vcgencmd display_power no longer exists. seatd arbitrates device access for
  # a compositor started outside a desktop session.
  #
  # wlopm is not in every Debian release; wlr-randr is, and covers the same job
  # with a bigger hammer. Install whichever exist rather than failing on the
  # one that does not - `screen_agent.py probe` decides between them later.
  apt-get install -y --no-install-recommends \
    labwc seatd wlr-randr chromium-browser python3 ca-certificates avahi-daemon
  apt-get install -y --no-install-recommends wlopm || \
    echo "note: wlopm is not packaged here; wlr-randr will be used instead"
  systemctl enable --now seatd
  usermod -aG video,input,render,seat "$KIOSK_USER" 2>/dev/null || \
    usermod -aG video,input "$KIOSK_USER"
fi

# --- console autologin ----------------------------------------------------
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

# --- scripts --------------------------------------------------------------
say "Installing scripts"
sed -e "s|@SERVER_URL@|$SERVER_URL|g" -e "s|@ORIENTATION@|$ORIENTATION|g" \
  "$HERE/kiosk-start.sh" > /usr/local/bin/homedash-kiosk-start
chmod 0755 /usr/local/bin/homedash-kiosk-start

install -m 0755 "$HERE/screen_agent.py" /usr/local/bin/homedash-screen-agent

# --- chromium enterprise policy -------------------------------------------
# The strongest lockdown layer, and the one most guides skip: with this in
# place, escaping kiosk mode still cannot load anything but HomeDash.
say "Installing Chromium policy"
install -d /etc/chromium/policies/managed
sed -e "s|@SERVER_URL@|$SERVER_URL|g" \
  "$HERE/chromium-policy.json" > /etc/chromium/policies/managed/homedash.json
chmod 0644 /etc/chromium/policies/managed/homedash.json
# Debian's chromium and Pi OS's chromium-browser read different directories.
if [ -d /etc/chromium-browser ] || command -v chromium-browser >/dev/null; then
  install -d /etc/chromium-browser/policies/managed
  cp /etc/chromium/policies/managed/homedash.json \
     /etc/chromium-browser/policies/managed/homedash.json
fi

# --- systemd units --------------------------------------------------------
say "Installing systemd units"
for unit in homedash-kiosk homedash-screen; do
  sed -e "s|@SERVER_URL@|$SERVER_URL|g" \
      -e "s|@KIOSK_USER@|$KIOSK_USER|g" \
      -e "s|@KIOSK_UID@|$KIOSK_UID|g" \
    "$HERE/$unit.service" > "/etc/systemd/system/$unit.service"
done
systemctl daemon-reload
systemctl enable homedash-kiosk.service homedash-screen.service

say "Done"
cat <<EOF

  server:      $SERVER_URL
  orientation: $ORIENTATION
  user:        $KIOSK_USER

Start it now without rebooting:

  sudo systemctl start homedash-kiosk homedash-screen

Then, still to do by hand (see README.md):

  1. Confirm the panel reaches the server:
       curl -sf $SERVER_URL/healthz && echo OK
  2. Find out how this panel blanks, and wire it up:
       homedash-screen-agent probe
     The screen agent runs with --dry-run until you do - it will log what it
     would have done and leave the display alone.
  3. If you rotated the screen, check touch input rotated with it.
  4. Last of all, once everything works: enable the read-only overlay
     filesystem with \`sudo raspi-config\`.

EOF
