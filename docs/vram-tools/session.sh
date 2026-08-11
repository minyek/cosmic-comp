#!/bin/bash
# Agent-driven capture session for the running cosmic-comp.
#
# The desktop user (andy, uid 1000) runs this once and leaves it in the
# foreground; the agent (uid 1001) drives it by dropping command files into
# /tmp/vram-ctl/cmd. The agent cannot signal the compositor or read its
# journal, so every privileged action happens here and the results are written
# world-readable back into the same directory.
#
#   run:   bash /tmp/vram-ctl/session.sh
#   stop:  Ctrl-C (or the agent sends the `stop` command)
set -u

CTL=${CTL:-/tmp/vram-ctl}
CSV=$CTL/vram.csv
MARKS=$CTL/marks.csv
LOG=$CTL/log
CURSOR=$CTL/jcursor
SEQ=0

P=$(pgrep -x cosmic-comp) || { echo "cosmic-comp is not running"; exit 1; }

# World-writable on purpose: the compositor's user runs this, while the analysing
# account (a different uid, which cannot signal the compositor or read its
# journal) drops command files in and reads the captures back out.
mkdir -p "$CTL" && chmod 0777 "$CTL"

vram_mib() {
  nvidia-smi | awk -v p="$P" '$0 ~ p && /cosmic-comp/ {print $(NF-1)}' | tr -d 'MiB'
}

say() { echo "$(date +%T) $*" | tee -a "$LOG"; chmod a+r "$LOG" 2>/dev/null; }

# Journal from this point on only; the pre-session backlog is not interesting
# and keeps the incremental dumps small.
journalctl --user -b --cursor-file="$CURSOR" -n 0 >/dev/null 2>&1

# A previous round's captures are archived rather than left in place: the sequence
# numbers restart here, so any journal whose label differs from its counterpart in
# this round survives it, and census.py globs the directory as one run. The archive
# name matches neither glob, so the round it holds stays readable but unscored.
if compgen -G "$CTL/journal-*.txt" >/dev/null || [ -e "$CTL/expectations.csv" ]; then
  PREVIOUS=$CTL/previous-$(date +%H%M%S)
  mkdir -p "$PREVIOUS"
  mv "$CTL"/journal-*.txt "$CTL"/snapshot-* "$CTL"/expectations.csv "$PREVIOUS"/ 2>/dev/null
  # marks.csv carries every census label and is truncated in place below, so the
  # archive needs a copy: without it the round reads back as bare filenames, its
  # phase boundaries disappear, and it re-scores as though nothing was checked.
  cp "$MARKS" "$PREVIOUS"/ 2>/dev/null
  chmod -R a+rX "$PREVIOUS"
  echo "archived the previous round to $PREVIOUS"
fi

: > "$CSV"; : > "$MARKS"; : > "$LOG"
echo "time,mib" >> "$CSV"
echo "seq,time,label,mib" >> "$MARKS"
chmod a+r "$CSV" "$MARKS" "$LOG"

# Background sampler: cosmic-comp's own per-process VRAM row every 10 s.
( while kill -0 "$P" 2>/dev/null; do
    echo "$(date +%T),$(vram_mib)" >> "$CSV"
    chmod a+r "$CSV" 2>/dev/null
    sleep 10
  done ) &
SAMPLER=$!
trap 'kill $SAMPLER 2>/dev/null; say "session ended"; exit 0' INT TERM

say "session started: cosmic-comp pid=$P, vram=$(vram_mib) MiB"
say "waiting for agent commands (Ctrl-C to stop)"

collect() {                      # collect <label> — census + incremental journal
  local label=$1 out
  SEQ=$((SEQ + 1))
  out=$(printf '%s/journal-%02d-%s.txt' "$CTL" "$SEQ" "$label")
  kill -USR1 "$P" 2>/dev/null
  sleep 2
  journalctl --user -b --cursor-file="$CURSOR" > "$out" 2>/dev/null
  echo "$SEQ,$(date +%T),$label,$(vram_mib)" >> "$MARKS"
  chmod a+r "$out" "$MARKS"
  say "census #$SEQ [$label] vram=$(vram_mib) MiB -> $(basename "$out")"
}

snapshot() {                     # snapshot <label> — census + fd/dmabuf inventory
  local label=$1 dir
  SEQ=$((SEQ + 1))
  dir=$(printf '%s/snapshot-%02d-%s' "$CTL" "$SEQ" "$label")
  mkdir -p "$dir"
  kill -USR1 "$P" 2>/dev/null
  sleep 2
  journalctl --user -b --cursor-file="$CURSOR" > "$dir/journal.txt" 2>/dev/null
  nvidia-smi > "$dir/nvidia-smi.txt"
  ls "/proc/$P/fd" | wc -l > "$dir/fd-count.txt"
  for f in "/proc/$P/fdinfo"/*; do
    if grep -q '^exp_name' "$f" 2>/dev/null; then echo "== $f"; cat "$f"; fi
  done > "$dir/dmabuf-fdinfo.txt" 2>/dev/null
  awk '/^size:/ {s+=$2; n++} END {printf "dmabuf fds: %d, total: %.1f MiB\n", n+0, s/1048576}' \
    "$dir/dmabuf-fdinfo.txt" > "$dir/dmabuf-summary.txt"
  grep -i '^Vm' "/proc/$P/status" > "$dir/proc-status.txt"
  echo "$SEQ,$(date +%T),$label,$(vram_mib)" >> "$MARKS"
  chmod -R a+rX "$dir"; chmod a+r "$MARKS"
  say "snapshot #$SEQ [$label] vram=$(vram_mib) MiB $(cat "$dir/dmabuf-summary.txt") -> $(basename "$dir")"
}

while :; do
  if [ -f "$CTL/cmd" ]; then
    line=$(cat "$CTL/cmd" 2>/dev/null)
    mv -f "$CTL/cmd" "$CTL/cmd.done" 2>/dev/null || rm -f "$CTL/cmd"
    verb=${line%% *}; label=${line#* }
    [ "$label" = "$line" ] && label=mark
    label=$(printf '%s' "$label" | tr -c 'A-Za-z0-9._-' '-' | sed 's/-*$//')
    case "$verb" in
      census)   collect "$label" ;;
      snapshot) snapshot "$label" ;;
      stop)     kill $SAMPLER 2>/dev/null; say "session ended by agent"; exit 0 ;;
      *)        say "unknown command: $line" ;;
    esac
  fi
  # The compositor dying mid-session is itself a test result — report and stop.
  if ! kill -0 "$P" 2>/dev/null; then
    say "!!! cosmic-comp (pid $P) is GONE — compositor crashed or session restarted"
    journalctl --user -b --cursor-file="$CURSOR" > "$CTL/journal-crash.txt" 2>/dev/null
    chmod a+r "$CTL/journal-crash.txt"
    kill $SAMPLER 2>/dev/null
    exit 1
  fi
  sleep 2
done
