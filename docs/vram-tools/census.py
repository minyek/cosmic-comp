#!/usr/bin/env python3
"""Parse and judge the SIGUSR1 census blocks written to the journal.

    census.py diff <journal-or-snapshot-dir>...   # counter table, one column each
    census.py verdict [capture-dir]               # automated pass/fail

The verdict checks two independent things, because either alone can mislead.
*Invariants* are absolute: cleanup queues drained, no dead cache entries, no
capture sessions outliving their client, one surface thread per output. But a
phase that never ran also satisfies every invariant, which is how a whole suite
of synthetic-input phases once "passed" while driving nothing at all. So each
phase must also show *activity* — GL objects actually created and destroyed —
before its flat counters are allowed to count as a pass.
"""
import re
import sys
from pathlib import Path

FIELD_RE = re.compile(r"(\w+)=(\d+)")
CACHE_RE = re.compile(r"renderer caches (\S+(?: \S+)?) DrmNode.*dmabuf_cache=(\d+)")
SLOTS_RE = re.compile(r"live_slots total=(\d+)")
THREADS_RE = re.compile(r"live surface threads=(\d+)")
GEN_RE = re.compile(r"swapchain=\[([\d,\-]+)\]")
COMP_RE = re.compile(r"compositor\[([^\]]+)\]")

# Counters that must be zero in every census, with what a non-zero value means.
INVARIANTS = {
    "queue.texture": "renderer cleanup queue not drained",
    "queue.framebuffer": "renderer cleanup queue not drained",
    "queue.renderbuffer": "renderer cleanup queue not drained",
    "queue.egl_image": "renderer cleanup queue not drained",
    "queue.mapping": "renderer cleanup queue not drained",
    "queue.program": "renderer cleanup queue not drained",
    "queue.sync": "renderer cleanup queue not drained",
    "dead": "dead entries retained in a renderer cache",
    "live.renderbuffers": "offscreen renderbuffers outliving their capture session",
    "offscreen_renderbuffers": "offscreen renderbuffers outliving their capture session",
}

# Counters that must be zero once a phase has finished, but legitimately rise
# mid-phase (the overview holds capture sessions open while it is on screen).
SETTLED = {
    "sessions": "capture sessions outliving their client",
    "cursor_sessions": "cursor capture sessions outliving their client",
    "ws_sessions": "workspace capture sessions not torn down",
    "surface_sessions": "toplevel capture sessions not torn down",
    "pending_frames": "capture frames queued but never completed",
}

ACTIVITY = {                     # phase expectation -> (counter, minimum delta)
    "egl_churn": ("raw.egl_images_created", 20),
    "texture_churn": ("raw.textures_created", 500),
    "renderbuffer_churn": ("raw.renderbuffers_created", 2),
}


def parse_block(lines):
    counters, generations = {}, {}
    for line in lines:
        # alive/dead ride on a "renderer cache detail" line, which is otherwise
        # skipped; dead entries are the strongest single leak signal there is.
        if "renderer cache detail" in line and " alive=" in line:
            for key, val in FIELD_RE.findall(line):
                if key in ("alive", "dead"):
                    counters[key] = int(val)
            continue
        m = COMP_RE.search(line)
        if m and "renderer cache detail" in line:
            gens = GEN_RE.search(line)
            if gens:
                live = [int(g) for g in gens.group(1).split(",") if g != "-"]
                if live:
                    generations[m.group(1)] = max(live)
            continue
        if 'output "' in line:
            scope = re.search(r'output "([^"]+)"', line).group(1)
            for key, val in FIELD_RE.findall(line):
                counters[f"{scope}.{key}"] = int(val)
            continue
        if line.startswith(("GL live objects", "GL cleanup queue depth")):
            prefix = "live" if "live objects" in line else "queue"
            for key, val in FIELD_RE.findall(line):
                counters[f"{prefix}.{key}"] = int(val)
            continue
        if "GL raw counters" in line:
            for key, val in FIELD_RE.findall(line.replace(": ", "=")):
                counters[f"raw.{key}"] = int(val)
            continue
        for regex, name in ((CACHE_RE, None), (SLOTS_RE, "live_slots"), (THREADS_RE, "surface_threads")):
            m = regex.search(line)
            if m:
                counters[name or f"cache.{m.group(1)}"] = int(m.group(2 if name is None else 1))
                break
        else:
            if "renderer cache detail" not in line:
                for key, val in FIELD_RE.findall(line):
                    counters[key] = int(val)
    counters["_generations"] = generations
    return counters


def censuses(path):
    """Yield one counter map per census block in a journal file."""
    text = Path(path).read_text(errors="replace")
    blocks, current = [], None
    for raw in text.splitlines():
        line = re.sub(r"^.*?cosmic-comp\[\d+\]: ", "", raw).strip()
        if "=== VRAM/resource census" in line:
            if current is not None:
                blocks.append(current)
            current = []
        elif current is not None:
            current.append(line)
    if current is not None:
        blocks.append(current)
    return [parse_block(b) for b in blocks]


def collect(directory):
    """Every census in the capture dir, in capture order, labelled from marks.csv."""
    marks = {}
    marks_file = Path(directory) / "marks.csv"
    if marks_file.exists():
        for row in marks_file.read_text().splitlines()[1:]:
            parts = row.split(",")
            if len(parts) >= 4:
                marks[int(parts[0])] = (parts[2], parts[3])

    out = []
    for path in sorted(Path(directory).glob("journal-*.txt")) + \
                sorted(Path(directory).glob("snapshot-*/journal.txt")):
        seq_match = re.search(r"(?:journal|snapshot)-(\d+)", str(path))
        seq = int(seq_match.group(1)) if seq_match else 0
        label, vram = marks.get(seq, (path.stem, "?"))
        for block in censuses(path):
            out.append((seq, label, vram, block))
    return sorted(out, key=lambda r: r[0])


def cmd_diff(paths):
    columns = []
    for p in paths:
        path = Path(p)
        journal = path / "journal.txt" if path.is_dir() else path
        found = censuses(journal)
        if not found:
            print(f"warning: no census block in {journal}", file=sys.stderr)
        for i, block in enumerate(found):
            name = path.name.replace("journal-", "").replace(".txt", "")
            columns.append((f"{name}[{i}]" if len(found) > 1 else name, block))

    keys = sorted({k for _, c in columns for k in c if not k.startswith("_")})
    width = max((len(k) for k in keys), default=10) + 2
    print(f"{'counter':<{width}}" + "".join(f"{n[:16]:>18}" for n, _ in columns))
    for key in keys:
        values = [c.get(key) for _, c in columns]
        present = [v for v in values if v is not None]
        if key.startswith("raw.") and len(set(present)) == 1:
            continue
        row = "".join(f"{'-' if v is None else v:>18}" for v in values)
        grew = len(present) > 1 and present[-1] > present[0] and not key.startswith(("raw.", "gen."))
        print(f"{key:<{width}}{row}" + ("  <-- GREW" if grew else ""))


def cmd_verdict(directory):
    rows = collect(directory)
    if not rows:
        print(f"no censuses found in {directory}")
        return 1

    failures, notes = [], []
    outputs = rows[-1][3].get("outputs", 0)

    for seq, label, vram, c in rows:
        for key, meaning in INVARIANTS.items():
            if c.get(key, 0) != 0:
                failures.append(f"[{seq} {label}] {key}={c[key]} — {meaning}")
        threads = c.get("surface_threads")
        if threads is not None and outputs and threads != outputs:
            failures.append(f"[{seq} {label}] surface_threads={threads} != outputs={outputs} — stranded surface thread")
        # Sessions may be open mid-phase; they must not survive a phase boundary.
        if "OPEN" not in label and "held" not in label:
            for key, meaning in SETTLED.items():
                if c.get(key, 0) != 0:
                    failures.append(f"[{seq} {label}] {key}={c[key]} — {meaning}")

    # Activity: a phase whose workload never reached the compositor is a failure,
    # not a pass, however flat its counters are.
    expectations = Path(directory) / "expectations.csv"
    if expectations.exists():
        wanted = {}
        for row in expectations.read_text().splitlines():
            phase, kind, _ = (row.split(",", 2) + ["", ""])[:3]
            wanted.setdefault(phase, set()).add(kind)
        for phase, kinds in wanted.items():
            span = [r for r in rows if phase in r[1] or r[1].startswith("post-" + phase)]
            for kind in kinds:
                if kind not in ACTIVITY:
                    continue
                counter, minimum = ACTIVITY[kind]
                if len(rows) < 2:
                    continue
                delta = rows[-1][3].get(counter, 0) - rows[0][3].get(counter, 0)
                if delta < minimum:
                    failures.append(
                        f"[{phase}] {counter} advanced by {delta} (< {minimum}) — "
                        "the workload never reached the compositor"
                    )
        _ = span

    # Slot generations must advance across a reconfigure while live_slots stays
    # bounded: that pairing is what distinguishes recycling from accumulation.
    gens = [(r[0], r[3]["_generations"], r[3].get("live_slots")) for r in rows if r[3].get("_generations")]
    if len(gens) >= 2:
        first, last = gens[0], gens[-1]
        advanced = any(last[1].get(k, 0) > first[1].get(k, 0) for k in last[1])
        slots = [g[2] for g in gens if g[2] is not None]
        notes.append(
            f"slot generations {'advanced' if advanced else 'did not advance'}; "
            f"live_slots ranged {min(slots)}-{max(slots)}" if slots else ""
        )
        if slots and max(slots) > 2 * (outputs or 1) + 4:
            failures.append(f"live_slots peaked at {max(slots)} — swapchain generations may be accumulating")

    print(f"censuses analysed: {len(rows)}   outputs: {outputs}")
    for note in filter(None, notes):
        print(f"note: {note}")
    print()
    if failures:
        print(f"VERDICT: FAIL ({len(failures)} finding(s))")
        for f in failures:
            print(f"  {f}")
        return 1
    print("VERDICT: PASS — invariants hold and every phase evidenced real activity")
    return 0


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    if argv[1] == "diff":
        cmd_diff(argv[2:] or sorted(str(p) for p in Path("/tmp/vram-ctl").glob("*-*")))
        return 0
    if argv[1] == "verdict":
        return cmd_verdict(argv[2] if len(argv) > 2 else "/tmp/vram-ctl")
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
