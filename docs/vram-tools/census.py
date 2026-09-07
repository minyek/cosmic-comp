#!/usr/bin/env python3
"""Parse and judge the SIGUSR1 census blocks written to the journal.

    census.py diff <journal-or-snapshot-dir>...   # counter table, one column each
    census.py verdict [capture-dir]               # automated pass/fail

Verdicts require a complete declared run and phase-local evidence. Historical
captures remain readable through diff; their missing contracts cannot earn PASS.
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


def parse_block(lines):
    counters, generations = {}, {}
    for line in lines:
        line = re.sub(r"\x1b\[[0-9;]*m", "", line)
        pointer = re.search(
            r"retest pointer: seat=(\d+) pointer_x=([-\d.]+) pointer_y=([-\d.]+)", line
        )
        if pointer:
            counters[f"retest.pointer.{pointer[1]}.x"] = float(pointer[2])
            counters[f"retest.pointer.{pointer[1]}.y"] = float(pointer[3])
            continue
        # alive/dead ride on a "renderer cache detail" line, which is otherwise
        # skipped; dead entries are the strongest single leak signal there is.
        if "renderer cache detail" in line and " alive=" in line:
            for key, val in FIELD_RE.findall(line):
                if key in ("alive", "dead"):
                    counters[key] = counters.get(key, 0) + int(val)
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
        if "workload counters:" in line:
            for key, val in FIELD_RE.findall(line.split("workload counters:")[1]):
                counters[f"workload.{key}"] = int(val)
            continue
        if "retest counters:" in line or "retest state:" in line:
            for key, val in FIELD_RE.findall(line):
                counters[f"retest.{key}"] = int(val)
            continue
        if "GL live objects" in line or "GL cleanup queue depth" in line:
            prefix = "live" if "live objects" in line else "queue"
            for key, val in FIELD_RE.findall(line):
                counters[f"{prefix}.{key}"] = int(val)
            continue
        if "GL raw counters" in line:
            for key, val in FIELD_RE.findall(line.replace(": ", "=")):
                counters[f"raw.{key}"] = int(val)
            continue
        for regex, name in (
            (CACHE_RE, None),
            (SLOTS_RE, "live_slots"),
            (THREADS_RE, "surface_threads"),
        ):
            m = regex.search(line)
            if m:
                counters[name or f"cache.{m.group(1)}"] = int(
                    m.group(2 if name is None else 1)
                )
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
    for path in sorted(Path(directory).glob("journal-*.txt")) + sorted(
        Path(directory).glob("snapshot-*/journal.txt")
    ):
        seq_match = re.search(r"(?:journal|snapshot)-(\d+)", str(path))
        seq = int(seq_match.group(1)) if seq_match else 0
        label, vram = marks.get(seq, (path.stem, "?"))
        for block in censuses(path):
            out.append((seq, label, vram, block))
    return sorted(out, key=lambda r: r[0])


def phase_spans(rows):
    """phase -> (baseline row, its own rows), delimited by the post-<phase> marks.

    Scoring a phase against the whole run lets a phase that drove nothing borrow another
    phase's activity, which is exactly the failure the activity check exists to catch.
    """
    spans, start = {}, 0
    for i, row in enumerate(rows):
        label = row[1]
        if label.startswith("post-"):
            spans[label[len("post-") :]] = (rows[start], rows[start : i + 1])
            start = i
    return spans


def journal_text(directory):
    paths = sorted(Path(directory).glob("journal-*.txt")) + sorted(
        Path(directory).glob("snapshot-*/journal.txt")
    )
    return "\n".join(p.read_text(errors="replace") for p in paths)


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
        grew = (
            len(present) > 1
            and present[-1] > present[0]
            and not key.startswith(("raw.", "gen."))
        )
        print(f"{key:<{width}}{row}" + ("  <-- GREW" if grew else ""))


def cmd_verdict(directory):
    from evidence import verdict

    return verdict(directory)


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
