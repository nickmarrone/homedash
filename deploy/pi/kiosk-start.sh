#!/bin/bash
# Keeps a browser on screen showing HomeDash, for as long as whatever started
# this script lives. Two callers, and the difference matters:
#
#   console session  labwc runs it as its startup command, so the browser's
#                    lifetime is bounded by the compositor's.
#   gnome session    homedash-kiosk.user.service runs it inside the desktop
#                    session, which already owns the display.
#
# Placeholders are substituted by setup.sh at install time.
set -uo pipefail

SERVER_URL="@SERVER_URL@"
ORIENTATION="@ORIENTATION@"
SESSION="@SESSION@"          # gnome | console
MODE="@MODE@"                # simple | locked
BROWSER="@BROWSER@"
BROWSER_IS_SNAP="@BROWSER_IS_SNAP@"   # 1 | 0

log() { echo "homedash-kiosk: $*"; }

if [ -z "$BROWSER" ] || ! command -v "$BROWSER" >/dev/null; then
  log "browser '$BROWSER' is not on PATH - re-run setup.sh after installing one"
  exit 1
fi

# --- desktop settings -----------------------------------------------------
# Applied here rather than in setup.sh because this runs inside the session,
# where the bus these need already exists - and because re-applying them every
# start means they survive anyone poking at Settings.
#
# Without them GNOME dims, blanks and locks the panel on its own idle schedule,
# which looks exactly like the screen agent misbehaving.
if [ "$SESSION" = "gnome" ] && command -v gsettings >/dev/null; then
  gsettings set org.gnome.desktop.session idle-delay 0 2>/dev/null
  gsettings set org.gnome.desktop.screensaver lock-enabled false 2>/dev/null
  gsettings set org.gnome.desktop.screensaver idle-activation-enabled false 2>/dev/null
  gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type nothing 2>/dev/null
fi

# --- rotation -------------------------------------------------------------
# wlr-randr speaks wlr-output-management, which labwc implements and Mutter
# does not. Under GNOME there is nothing to call, so say where the setting
# lives instead of failing quietly.
if [ "$ORIENTATION" = "portrait" ]; then
  if [ "$SESSION" = "gnome" ]; then
    log "portrait under GNOME: rotate once in Settings -> Displays (it persists)"
  elif command -v wlr-randr >/dev/null; then
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

# --- flags ----------------------------------------------------------------
# shellcheck disable=SC2054  # the comma is inside --disable-features's value,
# which is a single argument, not a separator between two array elements.
FLAGS=(
  --noerrdialogs
  --disable-infobars
  --disable-session-crashed-bubble
  --disable-features=TranslateUI,Translate
  --disable-pinch                   # no accidental zoom on a touch panel
  --overscroll-history-navigation=0 # a stray swipe must not navigate away
  --autoplay-policy=no-user-gesture-required
  --check-for-update-interval=31536000
  --password-store=basic            # don't block on a keyring that may not exist
  # Both of these open a window of their own on top of the panel, and taking
  # that window drops the fullscreen the panel started in. They cost nothing
  # with a throwaway profile and are essential with the snap's persistent one.
  --no-first-run
  --no-default-browser-check
)

# simple mode leaves the window escapable and the desktop reachable, which is
# what you want while the panel is still being worked on. locked mode is the
# wall-mounted end state - see README.
if [ "$MODE" = "locked" ]; then
  FLAGS+=(--kiosk)
else
  FLAGS+=(--start-fullscreen)
fi

# Under labwc there is no X server to fall back to, so Chromium has to be told.
# Under GNOME it picks Wayland or XWayland correctly on its own, and forcing
# ozone is a way to fail on the snap for nothing.
if [ "$SESSION" = "console" ]; then
  FLAGS+=(--ozone-platform=wayland)
fi

# A throwaway profile per boot means a wedged profile can never become
# permanent, and nothing accumulates on a card that may be mounted read-only.
#
# The snap cannot have one. Its `home` interface allows non-hidden paths under
# $HOME and nothing else - not /tmp, and not ~/.config either - so a
# --user-data-dir it cannot open makes Chromium exit at once, which upstack
# looks like a crash loop rather than a rejected flag. Let the snap use the
# profile inside its own confinement instead.
PROFILE=""
if [ "$BROWSER_IS_SNAP" != "1" ]; then
  PROFILE="$(mktemp -d "${XDG_RUNTIME_DIR:-/tmp}/homedash-chromium-XXXXXX")"
  FLAGS+=(--user-data-dir="$PROFILE")
  cleanup() { [ -n "$PROFILE" ] && rm -rf "$PROFILE"; }
  trap cleanup EXIT
fi

# --- run ------------------------------------------------------------------
# Whatever started this script outlives the browser: if it crashes or is
# killed, bring it back rather than leaving a blank rectangle on the wall
# until someone notices.
#
# But back off when it is failing immediately. A browser rejecting a flag exits
# in milliseconds, and a fixed 3s retry turns that into a silent spin that
# reads as a hardware fault - the journal fills with starts and never says why.
WINDOW_START=$SECONDS
FAILURES=0

while true; do
  log "starting $BROWSER at $SERVER_URL (mode=$MODE session=$SESSION)"
  STARTED_AT=$SECONDS
  "$BROWSER" "${FLAGS[@]}" "$SERVER_URL"
  STATUS=$?
  RAN_FOR=$((SECONDS - STARTED_AT))

  if [ $((SECONDS - WINDOW_START)) -gt 60 ]; then
    WINDOW_START=$SECONDS
    FAILURES=0
  fi
  [ "$RAN_FOR" -lt 10 ] && FAILURES=$((FAILURES + 1))

  if [ "$FAILURES" -ge 5 ]; then
    log "$BROWSER exited after ${RAN_FOR}s ($STATUS), $FAILURES times in a minute."
    log "  this is a startup failure, not a crash. try it by hand:"
    log "    $BROWSER ${FLAGS[*]} $SERVER_URL"
    log "backing off 60s"
    sleep 60
    WINDOW_START=$SECONDS
    FAILURES=0
  else
    log "browser exited after ${RAN_FOR}s ($STATUS); restarting in 3s"
    sleep 3
  fi
done
