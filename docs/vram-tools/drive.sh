#!/bin/bash
# Drive the regression phases against a running compositor, censusing at every
# phase boundary so the counter deltas are attributable to one workflow.
#
# All synthetic input goes through ydotool (uinput -> libinput). wtype is NOT
# usable here: cosmic-comp matches shortcuts in filter_keyboard_input, reached
# only from process_input_event on the libinput backend path, so
# virtual-keyboard-v1 input lands on the focused surface and never fires a
# shortcut. A wtype-driven phase completes normally having done nothing.
#
#   bash drive.sh all                     # every phase, unattended
#   bash drive.sh {monitors|apps|capture|popups|zoom|workspaces|pointer|minimize}
#   bash drive.sh selftest                # prove input reaches the compositor
set -u

export YDOTOOL_SOCKET=${YDOTOOL_SOCKET:-/tmp/.ydotool_socket}
CTL=${CTL:-/tmp/vram-ctl}
SHORTCUTS=$HOME/.config/cosmic/com.system76.CosmicSettings.Shortcuts/v1/custom

PHASE=${1:-}
ROUNDS=${2:-8}

command -v ydotool >/dev/null || { echo "ydotool not installed"; exit 1; }
# The socket file outlives the daemon, so check the process too — a stale socket
# means every keystroke is silently discarded and the phases test nothing.
pgrep -x ydotoold >/dev/null || { echo "ydotoold is not running (socket may be stale)"; exit 1; }
[ -S "$YDOTOOL_SOCKET" ] || { echo "ydotoold socket $YDOTOOL_SOCKET missing"; exit 1; }
[ -d "$CTL" ] || { echo "$CTL missing — is session.sh running?"; exit 1; }

# Linux input-event-codes, which is what ydotool speaks.
ESC=1; K1=2; K2=3; K3=4; K4=5; MINUS=12; EQUAL=13; TAB=15
W=17; A=30; M=50; SHIFT=42; SLASH=53; ALT=56; META=125; LEFT=105; RIGHT=106

key() {                            # key <code>... — press all, release in reverse
  local codes=("$@") seq=() i
  for c in "${codes[@]}"; do seq+=("$c:1"); done
  for ((i = ${#codes[@]} - 1; i >= 0; i--)); do seq+=("${codes[i]}:0"); done
  ydotool key "${seq[@]}" >/dev/null
}

mark() {                           # census, blocking until it lands
  local label=$1 before
  before=$(wc -l < "$CTL/marks.csv")
  printf 'census %s' "$label" > "$CTL/cmd.tmp" && mv "$CTL/cmd.tmp" "$CTL/cmd"
  for _ in $(seq 1 30); do
    [ "$(wc -l < "$CTL/marks.csv")" -gt "$before" ] && { echo "  [census: $label]"; return 0; }
    sleep 2
  done
  echo "  [census: $label TIMED OUT — is session.sh still running?]"
  return 1
}

# Every phase must prove it did something. "All counters flat" is the pass
# condition, which is indistinguishable from "the phase never ran" unless the
# workload is separately evidenced — so each phase records what to expect and
# census.py verdict fails the phase if the evidence is absent.
expect() { echo "$PHASE,$1,$2" >> "$CTL/expectations.csv"; }

selftest() {
  echo "opening workspace overview (Super+w), censusing while open"
  key $META $W; sleep 3
  mark overview-OPEN
  key $ESC; sleep 3
  mark overview-closed
  expect ws_sessions_peak "overview-OPEN must show ws_sessions > 0"
}

# cosmic-randr reconfigures through the same apply_config_for_outputs path a
# physical hotplug takes. A real power-cycle additionally exercises connector
# disconnect handling, so do one by hand periodically (see the process doc).
monitors() {
  command -v cosmic-randr >/dev/null || { echo "cosmic-randr missing — power-cycle by hand"; return 1; }
  # Connector names come from sysfs (DP-2, HDMI-A-1, ...), which is what
  # cosmic-randr calls them too, rather than from parsing its list output.
  local connected=() c name
  for c in /sys/class/drm/card*-*/; do
    [ "$(cat "$c/status" 2>/dev/null)" = connected ] || continue
    connected+=("$(basename "$c" | sed 's/^card[0-9]*-//')")
  done
  if [ "${#connected[@]}" -lt 2 ]; then
    echo "only ${#connected[@]} output(s) connected — refusing to disable the only display"
    return 1
  fi
  name=${TARGET_OUTPUT:-${connected[0]}}
  echo "cycling output $name ${ROUNDS}x (connected: ${connected[*]})"
  local target=$name
  for i in $(seq 1 "$ROUNDS"); do
    cosmic-randr disable "$target" >/dev/null 2>&1; sleep 6
    cosmic-randr enable  "$target" >/dev/null 2>&1; sleep 10
    echo "cycle $i/$ROUNDS"
  done
  expect slot_generations "swapchain generations must advance while live_slots stays bounded"
}

apps() {
  local app=${APP:-cosmic-files} pid
  command -v "$app" >/dev/null || { echo "$app not found"; return 1; }
  for i in $(seq 1 "$ROUNDS"); do
    "$app" >/dev/null 2>&1 &
    sleep 4
    # The launcher re-execs, so the job pid is a stub — target the live process.
    pid=$(pgrep -n -x "$app")
    if [ -z "$pid" ]; then echo "cycle $i: $app never appeared"; continue; fi
    kill "$pid" 2>/dev/null
    for _ in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
    kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null
    echo "cycle $i/$ROUNDS (closed pid $pid)"
    sleep 2
  done
  local left; left=$(pgrep -c -x "$app" 2>/dev/null || echo 0)
  [ "$left" -gt 0 ] && echo "WARNING: $left $app still running"
  expect egl_churn "client churn must create and destroy EGLImages"
}

capture() {
  command -v cosmic-screenshot >/dev/null || { echo "cosmic-screenshot missing"; return 1; }
  local out=${TMPDIR:-/tmp}/vram-ctl-shots
  mkdir -p "$out"
  for i in $(seq 1 "$ROUNDS"); do
    cosmic-screenshot --interactive=false --notify=false --save-dir "$out" >/dev/null 2>&1 \
      && echo "shot $i/$ROUNDS ok" || echo "shot $i/$ROUNDS FAILED"
    sleep 3
  done
  expect renderbuffer_churn "each screenshot must create and free renderbuffers"
}

# The capture constraint queries fail only when an output has no current mode or an
# offscreen renderer cannot be built. Neither is reachable from a desktop workload, so the
# removal-and-stop branches behind them need the compositor started with
# COSMIC_FAULT_CAPTURE_CONSTRAINTS=1. The overview holds workspace- and toplevel-scope
# sessions at once, so a single workload drives both branches.
faultcapture() {
  key $META $W; sleep 3
  mark fault-overview-OPEN
  key $ESC; sleep 2
  if ! grep -qs "Failing screencopy constraints" "$CTL"/journal-*.txt; then
    echo "no injected fault in the journal — the compositor is not running with"
    echo "COSMIC_FAULT_CAPTURE_CONSTRAINTS=1 (see the regression-test process doc)"
    return 1
  fi
  for i in $(seq 2 "$ROUNDS"); do
    key $META $W; sleep 2.5
    key $ESC; sleep 1.5
    echo "round $i/$ROUNDS (overview open/close against failing constraints)"
  done
  if command -v cosmic-screenshot >/dev/null; then
    local out=${TMPDIR:-/tmp}/vram-ctl-shots
    mkdir -p "$out"
    for i in $(seq 1 3); do
      cosmic-screenshot --interactive=false --notify=false --save-dir "$out" >/dev/null 2>&1
      sleep 2
    done
  fi
  expect fault_fired_workspace "the workspace capture path must take the injected failure"
  expect fault_fired_toplevel "the toplevel capture path must take the injected failure"
}

popups() {
  for i in $(seq 1 "$ROUNDS"); do
    key $META $SLASH; sleep 1.0; key $ESC; sleep 0.6   # launcher
    key $META $A;     sleep 1.0; key $ESC; sleep 0.6   # app library
    key $META $W;     sleep 1.2; key $ESC; sleep 0.6   # workspace overview
    key $META $TAB;   sleep 1.0; key $ESC; sleep 0.6   # window switcher
    echo "round $i/$ROUNDS (4 popups)"
  done
  expect egl_churn "popup churn must create and destroy EGLImages"
}

zoom() {
  for i in $(seq 1 "$ROUNDS"); do
    key $META $EQUAL; sleep 0.8
    key $META $EQUAL; sleep 0.8
    key $META $MINUS; sleep 0.8
    key $META $MINUS; sleep 0.8
    echo "round $i/$ROUNDS (zoom in x2, out x2)"
  done
  expect texture_churn "zoom must drive rendering"
}

workspaces() {
  for i in $(seq 1 "$ROUNDS"); do
    for ws in $K1 $K2 $K3 $K4; do key $META "$ws"; sleep 0.7; done
    key $META $SHIFT $ALT $RIGHT; sleep 1.5    # move focused window to other output
    key $META $SHIFT $ALT $LEFT;  sleep 1.5    # and back
    echo "round $i/$ROUNDS (4 switches + 2 output moves)"
  done
  key $META $K1
  expect texture_churn "workspace switching must drive rendering"
}

# Relative motion avoids depending on how absolute uinput coordinates map onto a
# dual-head layout; the sweep crosses the shared edge in both directions.
pointer() {
  for i in $(seq 1 "$ROUNDS"); do
    for _ in $(seq 1 12); do ydotool mousemove -x 400 -y 0 >/dev/null; sleep 0.05; done
    sleep 0.3
    for _ in $(seq 1 12); do ydotool mousemove -x -400 -y 0 >/dev/null; sleep 0.05; done
    sleep 0.3
    for _ in $(seq 1 8); do ydotool mousemove -x 0 -y 300 >/dev/null; sleep 0.05; done
    for _ in $(seq 1 8); do ydotool mousemove -x 0 -y -300 >/dev/null; sleep 0.05; done
    echo "sweep $i/$ROUNDS"
  done
  expect texture_churn "pointer motion must drive rendering"
}

# Minimize has no default binding, so bind it for the duration. cosmic-comp
# watches this config, so the binding applies live and the restore is immediate.
minimize() {
  local restore=""
  if [ -f "$SHORTCUTS" ]; then
    restore=$(mktemp); cp "$SHORTCUTS" "$restore"
  fi
  mkdir -p "$(dirname "$SHORTCUTS")"
  # shellcheck disable=SC2064
  trap "[ -n '$restore' ] && mv '$restore' '$SHORTCUTS' || rm -f '$SHORTCUTS'; trap - RETURN" RETURN
  printf '{\n    (modifiers: [Super, Shift], key: "m"): Minimize,\n}\n' > "$SHORTCUTS"
  sleep 2

  local app=${APP:-cosmic-files} pid
  "$app" >/dev/null 2>&1 &
  sleep 4
  pid=$(pgrep -n -x "$app")
  [ -n "$pid" ] || { echo "$app never appeared"; return 1; }

  key $META $SHIFT $M; sleep 2
  mark minimize-held                 # must show minimized_windows > 0
  key $META $SHIFT $M; sleep 2       # restore, then close while minimized
  key $META $SHIFT $M; sleep 2
  kill "$pid" 2>/dev/null
  for _ in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.5; done
  kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null
  sleep 5
  expect minimize_tracked "minimize-held must show minimized_windows > 0, and it must return to 0"
  echo "minimize phase complete (closed while minimized)"
}

# Run one phase under its own $PHASE, so its expectations and its closing census
# both carry its name and census.py can score activity inside the phase's own span
# rather than across the whole run.
run_phase() {                      # run_phase <name> [rounds]
  PHASE=$1
  ROUNDS=${2:-$ROUNDS}
  "$PHASE"
  mark "post-$PHASE"
}

all() {
  : > "$CTL/expectations.csv"
  mark baseline
  run_phase selftest
  run_phase monitors 3
  run_phase apps 10
  run_phase capture 6
  run_phase popups 8
  run_phase zoom 6
  run_phase workspaces 5
  run_phase pointer 5
  run_phase minimize
  echo "settling for 90s"; sleep 90
  printf 'snapshot settle' > "$CTL/cmd.tmp" && mv "$CTL/cmd.tmp" "$CTL/cmd"; sleep 10
  echo
  echo "all phases complete — run: python3 census.py verdict $CTL"
}

# Kept out of `all`: with the fault armed every capture fails, so the ordinary capture
# phase could not evidence the renderbuffer churn it is there to prove.
faultall() {
  : > "$CTL/expectations.csv"
  mark baseline
  run_phase faultcapture 6
  echo "settling for 60s"; sleep 60
  printf 'snapshot settle' > "$CTL/cmd.tmp" && mv "$CTL/cmd.tmp" "$CTL/cmd"; sleep 10
  echo
  echo "fault round complete — run: python3 census.py verdict $CTL"
}

case "$PHASE" in
  selftest|monitors|apps|capture|popups|zoom|workspaces|pointer|minimize|faultcapture) "$PHASE" ;;
  all) all ;;
  faultall) faultall ;;
  *) echo "usage: $0 {all|faultall|selftest|monitors|apps|capture|popups|zoom|workspaces|pointer|minimize|faultcapture} [rounds]"; exit 2 ;;
esac
