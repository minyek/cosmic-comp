#!/bin/sh
# Sample cosmic-comp's VRAM (its own nvidia-smi process row) every 60 s into
# /tmp/vram.csv, firing a SIGUSR1 census at start and then hourly so the
# journal carries periodic counter dumps alongside the trajectory.
# Run as the desktop user:  setsid /tmp/vram-watch.sh & disown
# Stop with:                pkill -f vram-watch.sh

exec 9>/tmp/vram-watch.lock
flock -n 9 || { echo "vram-watch already running"; exit 1; }

i=0
while :; do
  P=$(pgrep -x cosmic-comp) || { sleep 60; continue; }
  # Census at start (i=0) and every 60th sample == hourly at the 60 s cadence.
  if [ $((i % 60)) -eq 0 ]; then
    kill -USR1 "$P" 2>/dev/null || true
  fi
  MIB=$(nvidia-smi | awk -v p="$P" '$0 ~ p && /cosmic-comp/ {print $(NF-1)}' | tr -d 'MiB')
  echo "$(date +%F-%T),$MIB" >> /tmp/vram.csv
  chmod a+r /tmp/vram.csv 2>/dev/null || true
  i=$((i + 1))
  sleep 60
done
