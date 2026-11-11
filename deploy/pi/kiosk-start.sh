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
FRAME_TOP="@FRAME_TOP@"      # CSS px a physical frame hides, top and bottom
FRAME_BOTTOM="@FRAME_BOTTOM@"
SESSION="@SESSION@"          # gnome | console
MODE="@MODE@"                # simple | locked
BROWSER="@BROWSER@"
BROWSER_IS_SNAP="@BROWSER_IS_SNAP@"   # 1 | 0

log() { echo "homedash-kiosk: $*"; }

if [ -z "$BROWSER" ] || ! command -v "$BROWSER" >/dev/null; then
  log "browser '$BROWSER' is not on PATH - re-run setup.sh after installing one"
  exit 1
fi

# --- a browser left over from the last run --------------------------------
# A snap app does not stay in the cgroup of the unit that launched it: `snap
# run` moves it into a transient scope of its own (snap.<name>.<app>-*.scope),
# so stopping or restarting homedash-kiosk ends this script and leaves the
# browser running. The next launch shares that browser's profile - the snap
# cannot have a throwaway one, see PROFILE below - so Chromium's single-instance
# check hands the URL to the old process as one more window and exits at once.
# The loop at the bottom reads that as a browser that died in under a second
# and launches again in 3s: a new fullscreen window every few seconds, each
# taking focus from the last.
#
# So a confined browser is cleared out before every launch, and by the units'
# ExecStopPost (`homedash-kiosk-start stop`) when the kiosk stops. That also
# closes a Chromium somebody opened by hand from the same snap, which on a
# kiosk is the right call: it would have captured the panel's launch anyway.
# The non-snap path needs none of this - its browser dies with the unit, and a
# stale instance cannot capture a launch with a fresh --user-data-dir.
snap_name_of() {
  local entry real
  entry="$(command -v "$1")"
  real="$(readlink -f "$entry")"
  case "$entry" in
    /snap/bin/*) entry="${entry#/snap/bin/}"; echo "${entry%%.*}"; return ;;
  esac
  case "$real" in
    /snap/*) real="${real#/snap/}"; echo "${real%%/*}"; return ;;
  esac
}

SNAP_NAME=""
[ "$BROWSER_IS_SNAP" = "1" ] && SNAP_NAME="$(snap_name_of "$BROWSER")"

clear_stray_browser() {
  [ -n "$SNAP_NAME" ] || return 0
  local pattern="^/snap/$SNAP_NAME/" uid
  uid="$(id -u)"
  pgrep -u "$uid" -f "$pattern" >/dev/null || return 0
  log "a $SNAP_NAME browser is already open; closing it before starting ours"
  pkill -TERM -u "$uid" -f "$pattern"
  for _ in $(seq 20); do
    pgrep -u "$uid" -f "$pattern" >/dev/null || return 0
    sleep 0.5
  done
  log "it did not close in 10s; killing it"
  pkill -KILL -u "$uid" -f "$pattern"
  sleep 1
}

if [ "${1:-}" = "stop" ]; then
  clear_stray_browser
  exit 0
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
# does not. Mutter's own tool is gdctl, from mutter-common-bin.
#
# gdctl set describes the whole layout, so anything it does not name is turned
# off - hence one monitor only. And a panel that is already rotated is left
# alone: 90 is what the opposite mount needs, set in Settings -> Displays, and
# restarting the browser must not undo it.
rotate_gnome() {
  local layout monitors
  if ! command -v gdctl >/dev/null; then
    log "gdctl not installed; rotate once in Settings -> Displays"
    return
  fi
  layout="$(gdctl show 2>/dev/null)"
  monitors="$(sed -n 's/^[^A-Za-z]*Monitor \([^ ]*\) (.*/\1/p' <<<"$layout")"
  if [ -z "$monitors" ] || [ "$(wc -l <<<"$monitors")" -ne 1 ]; then
    log "expected one monitor, found '${monitors//$'\n'/ }'; leaving rotation alone"
    return
  fi
  if ! grep -q 'Transform: normal' <<<"$layout"; then
    log "$monitors is already rotated; leaving it alone"
    return
  fi
  log "rotating $monitors to portrait"
  gdctl set --logical-monitor --primary --monitor "$monitors" --transform 270 \
    || log "rotation failed"
}

if [ "$ORIENTATION" = "portrait" ]; then
  if [ "$SESSION" = "gnome" ]; then
    rotate_gnome
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

# --- url ----------------------------------------------------------------
# The frame margins ride on the URL because they belong to this panel, not to
# the server, and the browser profile is thrown away on every start - see
# frontend/src/lib/frame.ts. Built here rather than substituted whole by
# setup.sh, because `&` means something in a sed replacement.
PANEL_URL="$SERVER_URL/"
QUERY=""
[ "${FRAME_TOP:-0}" != "0" ] && QUERY="frame_top=$FRAME_TOP"
[ "${FRAME_BOTTOM:-0}" != "0" ] && QUERY="${QUERY:+$QUERY&}frame_bottom=$FRAME_BOTTOM"
[ -n "$QUERY" ] && PANEL_URL="$PANEL_URL?$QUERY"

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
  clear_stray_browser
  log "starting $BROWSER at $PANEL_URL (mode=$MODE session=$SESSION)"
  STARTED_AT=$SECONDS
  "$BROWSER" "${FLAGS[@]}" "$PANEL_URL"
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
    log "    $BROWSER ${FLAGS[*]} '$PANEL_URL'"
    log "backing off 60s"
    sleep 60
    WINDOW_START=$SECONDS
    FAILURES=0
  else
    log "browser exited after ${RAN_FOR}s ($STATUS); restarting in 3s"
    sleep 3
  fi
done
