#!/bin/bash
# Verify the machine is ready to produce a trustworthy capture, and refuse to
# proceed if it is not. Every check here has burned a previous session: a stock
# distro binary with no instrumentation, a build that was never installed, or
# synthetic input that silently went nowhere.
#
#   bash preflight.sh [path/to/target/release/cosmic-comp]
set -u

BUILD=${1:-}
FAIL=0

ok()   { echo "  ok    $*"; }
warn() { echo "  WARN  $*"; }
bad()  { echo "  FAIL  $*"; FAIL=1; }

echo "== compositor =="
PID=$(pgrep -x cosmic-comp) || { bad "cosmic-comp is not running"; exit 1; }
ok "running, pid $PID"

if strings /usr/bin/cosmic-comp 2>/dev/null | grep -q 'VRAM/resource census'; then
  ok "installed binary carries the SIGUSR1 census"
else
  bad "installed binary has no census — it is not an instrumented build (sudo make install?)"
fi

# The census only describes the process that is actually running, so the binary
# on disk must predate the compositor's start.
INSTALLED=$(stat -c %Y /usr/bin/cosmic-comp)
STARTED=$(date -d "$(ps -o lstart= -p "$PID")" +%s)
if [ "$INSTALLED" -lt "$STARTED" ]; then
  ok "binary installed $(( (STARTED - INSTALLED) / 60 )) min before the session started"
else
  bad "binary is NEWER than the running compositor — log out and back in first"
fi

if [ -n "$BUILD" ]; then
  if [ -f "$BUILD" ]; then
    if [ "$(md5sum < /usr/bin/cosmic-comp)" = "$(md5sum < "$BUILD")" ]; then
      ok "installed binary is identical to $BUILD"
    else
      bad "installed binary DIFFERS from $BUILD — wrong build under test"
    fi
  else
    warn "build not found at $BUILD — skipping identity check"
  fi
else
  warn "no build path given — skipping identity check (pass one to verify)"
fi

echo "== capture tooling =="
for t in nvidia-smi journalctl; do
  command -v "$t" >/dev/null && ok "$t present" || bad "$t missing"
done

echo "== synthetic input =="
# wtype is deliberately not used: virtual-keyboard-v1 input reaches the focused
# surface but never the compositor's shortcut matcher, so a wtype-driven phase
# runs to completion having done nothing at all.
if command -v ydotool >/dev/null; then
  ok "ydotool present"
else
  bad "ydotool missing (sudo dnf install -y ydotool)"
fi
SOCK=${YDOTOOL_SOCKET:-/tmp/.ydotool_socket}
# The socket file outlives the daemon, so its presence alone proves nothing.
if pgrep -x ydotoold >/dev/null && [ -S "$SOCK" ]; then
  ok "ydotoold running, socket $SOCK"
else
  [ -S "$SOCK" ] && bad "stale socket $SOCK, no ydotoold process" || bad "no ydotoold at $SOCK"
  echo "        sudo rm -f $SOCK"
  echo "        sudo systemd-run --unit=ydotoold-test ydotoold --socket-path=$SOCK --socket-own=$(id -u):$(id -g)"
fi

echo "== outputs =="
command -v cosmic-randr >/dev/null && ok "cosmic-randr present (monitor phase can run unattended)" \
  || warn "cosmic-randr missing — the monitor phase needs a physical power-cycle"
for c in /sys/class/drm/card*-*/; do
  [ "$(cat "$c/status" 2>/dev/null)" = connected ] && \
    echo "  ---   $(basename "$c" | sed 's/^card[0-9]*-//') $(head -1 "$c/modes" 2>/dev/null)"
done

echo
[ "$FAIL" -eq 0 ] && echo "PREFLIGHT PASS" || echo "PREFLIGHT FAILED — fix the above before capturing"
exit "$FAIL"
