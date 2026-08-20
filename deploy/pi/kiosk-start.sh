#!/bin/bash
# Launched by labwc as its startup command. Sets the display up, then keeps a
# Chromium kiosk running for as long as the compositor lives.
#
# Placeholders are substituted by setup.sh at install time.
set -uo pipefail

SERVER_URL="@SERVER_URL@"
ORIENTATION="@ORIENTATION@"

log() { echo "homedash-kiosk: $*"; }

# --- rotation -------------------------------------------------------------
# labwc has no output-transform directive in rc.xml, so rotation is applied by
# asking the compositor directly once it is up. wlroots maps touch input to the
# output transform, so the touchscreen should follow - verify it rather than
# assume, see README.
if [ "$ORIENTATION" = "portrait" ]; then
  if command -v wlr-randr >/dev/null; then
    OUTPUT="$(wlr-randr --json 2>/dev/null | grep -m1 '"name"' | cut -d'"' -f4)"
    [ -z "$OUTPUT" ] && OUTPUT="$(wlr-randr 2>/dev/null | head -1 | cut -d' ' -f1)"
    if [ -n "$OUTPUT" ]; then
      log "rotating $OUTPUT to portrait"
      wlr-randr --output "$OUTPUT" --transform 90 || log "rotation failed"
    else
      log "could not determine output name; leaving rotation alone"
    fi
  else
    log "wlr-randr not installed; cannot rotate"
  fi
fi

# --- browser --------------------------------------------------------------
# Debian calls it chromium; Raspberry Pi OS has historically called it
# chromium-browser. Take whichever is present.
BROWSER=""
for candidate in chromium-browser chromium; do
  if command -v "$candidate" >/dev/null; then BROWSER="$candidate"; break; fi
done
if [ -z "$BROWSER" ]; then
  log "no chromium found - install it with: sudo apt install chromium-browser"
  exit 1
fi

# A fresh profile every boot, so a wedged profile cannot become permanent and
# nothing accumulates on an SD card that may be mounted read-only.
PROFILE="$(mktemp -d /tmp/homedash-chromium-XXXXXX)"
cleanup() { rm -rf "$PROFILE"; }
trap cleanup EXIT

# shellcheck disable=SC2054  # the comma is inside --disable-features's value,
# which is a single argument, not a separator between two array elements.
FLAGS=(
  --kiosk
  --ozone-platform=wayland          # labwc is Wayland; without this it tries X11
  --user-data-dir="$PROFILE"
  --noerrdialogs
  --disable-infobars
  --disable-session-crashed-bubble
  --disable-features=TranslateUI,Translate
  --disable-pinch                   # no accidental zoom on a touch panel
  --overscroll-history-navigation=0 # a stray swipe must not navigate away
  --autoplay-policy=no-user-gesture-required
  --check-for-update-interval=31536000
  --password-store=basic            # no keyring on a headless Lite install
)

# The compositor outlives the browser: if Chromium crashes or is killed, bring
# it straight back rather than leaving a blank screen on the wall until the
# next reboot.
while true; do
  log "starting $BROWSER at $SERVER_URL"
  "$BROWSER" "${FLAGS[@]}" "$SERVER_URL"
  log "browser exited ($?); restarting in 3s"
  sleep 3
done
