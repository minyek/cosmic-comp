# VRAM capture and regression-test scripts

Two toolsets share this directory. For a full regression pass against an
installed build, follow
[`../vram-regression-test-process.md`](../vram-regression-test-process.md); for
exploratory leak hunting, follow
[`../vram-leak-runbook.md`](../vram-leak-runbook.md).

Run them straight from the worktree — the desktop user is in the `work` group, so
no copying to `/tmp` is needed. Only the capture output lives in `/tmp/vram-ctl/`.

## Regression pass

- **`preflight.sh`** — refuses to start unless the running compositor really is
  the build under test: census strings present, binary older than the session,
  optional md5 match against a given `target/release/cosmic-comp`, input tooling
  and ydotoold up. Pass the build path to get the identity check.
- **`session.sh`** — the capture session. Samples cosmic-comp's VRAM every 10 s
  and serves `census`/`snapshot`/`stop` commands dropped into `/tmp/vram-ctl/cmd`,
  writing every reading back world-readable. This is what lets a second,
  unprivileged account drive and analyse a session it can neither signal nor read
  the journal of. Also captures the journal if the compositor dies.
- **`drive.sh`** — the phases: `selftest`, `monitors`, `apps`, `capture`,
  `popups`, `zoom`, `workspaces`, `pointer`, `minimize`, or `all` for an
  unattended run that censuses at every boundary. Input goes through ydotool;
  see the process doc for why wtype cannot work here.
  `faultcapture`/`faultall` cover the capture *failure* paths, and need the
  compositor started with `COSMIC_FAULT_CAPTURE_CONSTRAINTS=1` — no desktop
  workload can reach those branches on its own (process doc §5).
- **`census.py`** — `verdict` for a machine-checked PASS/FAIL (non-zero exit on
  failure, so it can gate a release), `diff` for a counter table across captures.
  Activity is scored inside each phase's own `post-<phase>` span, and a
  compositor panic anywhere in the captured journal fails the run.

## Leak hunting

- **`vram-watch.sh`** — `flock`-guarded single-instance sampler. Logs
  cosmic-comp's own `nvidia-smi` VRAM row every 60 s to `/tmp/vram.csv`
  (`YYYY-MM-DD-HH:MM:SS,<MiB>`) and fires a SIGUSR1 census at start + hourly.
  Use for multi-day soaks, where `session.sh` would be overkill.
- **`leak-snapshot.sh`** — point-in-time snapshot into
  `/tmp/leak-snapshot-<ts>/`: census, `nvidia-smi`, open-fd count, dma-buf fdinfo
  inventory + summary (pinned client buffers vs compositor-internal GL objects),
  `/proc/<pid>/status` `Vm*` lines, and the journal.
- **`reverse-ref-scan.py`** — static scan for the reference patterns that caused
  the original leaks.

## Prerequisite: the running compositor must be OUR instrumented build

`SIGUSR1` and `GL_KHR_debug` only do anything if the live `/usr/bin/cosmic-comp`
is the instrumented counter build, **not** the Fedora distro RPM (a `dnf`
operation can silently overwrite it — observed 2026-06-13). `preflight.sh` checks
this; to verify by hand:

```bash
# our build contains these strings; the distro RPM contains none of them
strings /usr/bin/cosmic-comp | grep -E 'VRAM/resource census|SIGUSR1 dumper installed'
rpm -Vf /usr/bin/cosmic-comp   # a '5' flag on the binary = NOT the pristine RPM (good — it's ours)
```

If absent: `sudo make install` from the repo (copies `target/release/cosmic-comp`),
then log out / back in so the new binary is the running compositor.
