#!/bin/sh
# Capture cosmic-comp's live VRAM state into a world-readable snapshot dir.
# Run as the desktop user (andy):  sh /tmp/leak-snapshot.sh
set -eu
P=$(pgrep -x cosmic-comp) || { echo "cosmic-comp not running"; exit 1; }
OUT=/tmp/leak-snapshot-$(date +%Y%m%d-%H%M%S)
mkdir -p "$OUT"

kill -USR1 "$P"          # resource census -> journal
sleep 2

nvidia-smi > "$OUT/nvidia-smi.txt"

# How many fds does the compositor hold, and how many are dmabufs (pinned
# client/GPU buffers)? A large dmabuf total at near-idle means the residual
# is pinned imports; near-zero means compositor-internal GL objects.
ls "/proc/$P/fd" | wc -l > "$OUT/fd-count.txt"
for f in "/proc/$P/fdinfo"/*; do
  if grep -q '^exp_name' "$f" 2>/dev/null; then
    echo "== $f"
    cat "$f"
  fi
done > "$OUT/dmabuf-fdinfo.txt" 2>/dev/null || true

awk '/^size:/ {s+=$2; n++} END {printf "dmabuf fds: %d, total: %.1f MiB\n", n, s/1048576}' \
  "$OUT/dmabuf-fdinfo.txt" > "$OUT/dmabuf-summary.txt"

grep -i '^Vm' "/proc/$P/status" > "$OUT/proc-status.txt"

journalctl --user -b > "$OUT/journal.txt" 2>/dev/null \
  || journalctl -b > "$OUT/journal.txt" 2>/dev/null \
  || echo "journal not readable" > "$OUT/journal.txt"

chmod -R a+rX "$OUT"
echo "snapshot ready: $OUT"
