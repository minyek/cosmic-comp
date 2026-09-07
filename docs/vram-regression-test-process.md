# VRAM regression test process

## Current procedure

Use [the retest guide](vram-tools/RETEST.md) and
[build instructions](vram-tools/BUILDING.md). They replace the command-file
transport, optional binary identity check, persistent capture faults and single
combined verdict described below. Each normal, fault and assisted hardware round
now requires a fresh directory and complete manifest. Do not run the legacy
commands below against the updated scripts.

## Historical procedure

How to re-run the full desktop regression pass against an installed build and get
a machine-checked verdict. This is the *repeatable* companion to
[`vram-leak-runbook.md`](./vram-leak-runbook.md), which covers exploratory leak
*hunting*; use this doc when the question is "does the build still hold up?"
rather than "where is the leak?". Results go in
[`vram-leak-findings.md`](./vram-leak-findings.md), newest entry first.

Budget ~25 minutes, of which ~20 is unattended.

## 0. Install the build under test

`sudo make install` copies `target/release/cosmic-comp` — it never rebuilds — then
**log out and back in**, because the census only ever describes the process that
is actually running. Preflight enforces this.

## 1. Preflight (terminal 1)

```bash
cd <worktree>
bash docs/vram-tools/preflight.sh target/release/cosmic-comp
```

Refuses to continue unless the running compositor is the instrumented build, the
binary predates the session start, ydotoold is up, and the input tooling is
present. Every check here corresponds to a way a previous round produced hours of
worthless data.

If it reports no ydotoold:

```bash
sudo dnf install -y ydotool
sudo rm -f /tmp/.ydotool_socket    # the socket file outlives the daemon
sudo systemd-run --unit=ydotoold-test ydotoold --socket-path=/tmp/.ydotool_socket --socket-own=$(id -u):$(id -g)
```

Preflight checks for the *process*, not just the socket: a stale socket left by a
stopped daemon silently discards every keystroke, which would make each phase
complete normally while testing nothing.

That daemon is the one privileged component in the whole setup. Stop it when
finished: `sudo systemctl stop ydotoold-test`.

## 2. Start the capture session (terminal 1, stays running)

```bash
bash docs/vram-tools/session.sh
```

Samples cosmic-comp's own VRAM row every 10 s and then waits for commands. It
creates `/tmp/vram-ctl/` world-writable and writes every capture there
world-readable, which is what lets a second, unprivileged account drive and
analyse a session it cannot otherwise observe (it can neither signal the
compositor nor read its journal). Drop `census <label>` or `snapshot <label>`
into `/tmp/vram-ctl/cmd` to take a reading at any moment; `stop` ends it. It also
detects the compositor dying and captures the crash journal.

## 3. Drive the phases (terminal 2)

```bash
bash docs/vram-tools/drive.sh all
```

Unattended, ~20 min, censusing at every phase boundary. **Synthetic input drives
the real desktop** — the pointer moves, popups open, monitors blank — so keep
hands off. Phases: `selftest`, `monitors`, `apps`, `capture`, `popups`, `zoom`,
`workspaces`, `pointer`, `minimize`, then a 90 s settle. Any one can be run
alone: `bash docs/vram-tools/drive.sh popups 8`.

Two phases mutate live state and restore it: `minimize` installs a temporary
`Super+Shift+M` → `Minimize` binding in the user's custom shortcuts (backed up
and restored on exit, including on Ctrl-C), and `monitors` disables and re-enables
an output via `cosmic-randr`, refusing to run if only one display is connected.

## 4. Verdict

```bash
python3 docs/vram-tools/census.py verdict /tmp/vram-ctl   # PASS/FAIL
python3 docs/vram-tools/census.py diff  /tmp/vram-ctl/journal-*.txt   # counter table
```

`verdict` exits non-zero on failure, so it can gate a release.

## 5. The fault round (capture failure paths)

The steps above never exercise the capture *failure* paths, because nothing a
desktop does can provoke them: `constraints_for_output` and
`constraints_for_toplevel` return `None` only when an output has no current mode
or an offscreen renderer cannot be built, and `apply_config_for_outputs` gives
every output a mode before any client can bind a capture source. The
removal-and-stop code behind those failures — where the workspace-scope capture
panic lived — therefore needs the fault armed:

The variable has to enter the environment *above* `cosmic-session`;
`~/.config/environment.d` reaches every COSMIC component except the compositor,
for the reason set out in runbook §5. Use the same route as the GL-debug pass:

```bash
echo COSMIC_FAULT_CAPTURE_CONSTRAINTS=1 | sudo tee -a /etc/environment
# log out and back in, then verify it landed, as the desktop user:
tr '\0' '\n' < /proc/$(pgrep -x cosmic-comp)/environ | grep COSMIC_FAULT
journalctl --user -b | grep 'Fault armed'
```

The compositor logs `Fault armed: …` at startup and
`Failing screencopy constraints for {workspace,toplevel}` on every fault. Remove
the line from `/etc/environment` and log back in when the round is done — an
armed session cannot take screenshots or draw overview thumbnails.

Then, against an armed session:

```bash
bash docs/vram-tools/drive.sh faultall
python3 docs/vram-tools/census.py verdict /tmp/vram-ctl
```

The workspace overview holds workspace- *and* toplevel-scope sessions at once, so
one workload drives both branches. The phase aborts if it sees no fault in the
journal after its first cycle, which is what distinguishes "the fix held" from
"the environment never reached the compositor".

This round is deliberately separate from `all`: with the fault armed every
capture fails, so the ordinary `capture` phase could not evidence the
renderbuffer churn it exists to prove.

## What the verdict actually checks, and why it is three things

**Invariants** — cleanup queues drained, no `dead` cache entries, no capture
sessions or offscreen renderbuffers outliving their client, `surface_threads ==
outputs`, `live_slots` bounded relative to output count.

**Activity** — every phase must show that its own workload reached the compositor.
This half exists because the pass condition for a leak test is "counters stayed
flat", which is indistinguishable from "the workload never ran". On 2026-08-06 an
entire suite of popup, zoom and workspace phases reported success having done
nothing at all, and it was caught only by noticing `egl_images_created` frozen
across 32 supposed popup opens. `census.py verdict` now fails that case outright.

The evidence has to be a counter *only that phase's workload can move*, which
rules out generic GL churn for most phases. An idle desktop creates textures at
~10/s and EGL images at ~1/s, so a delta threshold over either scores how long a
phase took rather than what it did: measured against the 90 s settle window, the
`pointer` and `workspaces` phases moved *fewer* textures per second than an idle
desktop, and `apps` was within 1.5× of idle. So the phases that drive input are
evidenced by counters incremented on the input itself
(`src/utils/workload_counters.rs`: `pointer_motions`, `workspace_activations`,
`zoom_changes`), and the rest by state only the phase can enter — a capture
session, a minimized window, a renderbuffer, a toplevel above the phase's own
baseline. Where that state is transient the phase censuses *while holding it*
(`apps-window`, `minimize-held`, `overview-OPEN`); a boundary census alone would
see it already gone.

Each phase declares its own minimum in `expectations.csv`, derived from its round
count, because one global constant cannot fit workloads that differ by two orders
of magnitude. A capture whose build predates a counter fails with "absent from the
census" rather than passing on a counter that was never emitted.

**Journal evidence** — a compositor panic anywhere in the captured journal fails
the run, and the fault round additionally requires both injected-fault messages.
Neither leaves a counter behind: a panicked compositor is simply gone, and every
census taken before it died looks clean.

Activity and peaks are scored **inside each phase's own span**, delimited by the
`post-<phase>` boundary censuses. Scoring against the whole run let a phase that
drove nothing borrow a busier phase's counters — which is the exact failure the
activity check exists to catch, so `all` now sets `$PHASE` per phase and marks a
boundary after each.

## Traps

- **`wtype` cannot drive compositor shortcuts.** Shortcuts are matched in
  `filter_keyboard_input`, reached only from `process_input_event` on the libinput
  backend path (`src/input/mod.rs`). `virtual-keyboard-v1` input goes straight to
  the focused surface, so a wtype-driven phase completes normally having achieved
  nothing. Use ydotool, which injects through uinput and enters the same path a
  real keyboard does.
- **A launcher that re-execs defeats `kill $!`.** `cosmic-files` leaves the job
  pid a short-lived stub; resolve the live process with `pgrep -n -x` and confirm
  it actually died, or the "churn" phase just accumulates running apps.
- **`cosmic-randr disable/enable` is not a physical hotplug.** It exercises
  `apply_config_for_outputs`, but a real power-cycle also exercises connector
  disconnect handling. Do a manual power-cycle round periodically and note in the
  findings log which method was used.
- **`output_zoom_states` and the per-output `zoom=` flag cannot fall.** They test
  `user_data().get::<Mutex<OutputZoomState>>().is_some()`, and smithay's
  `UserDataMap` has no removal API, so both are stuck true once zoom has been used
  even once — regardless of whether zoom is active. The counter saturates at
  `outputs.len()`; do not read growth into it.
- **Chrome's WebGL emits `GL_INVALID_OPERATION` into the same journal.** Attribute
  GL errors by process before believing them; compositor-side errors are prefixed
  `GL:` from `src/utils/gl_debug.rs`.

## Coverage this process does not have

Long-lived **screencast** sessions (needs a portal-using recorder — OBS, kooha or
wf-recorder — installed; `cosmic-screenshot` drives the same handlers but only
with short-lived sessions), **panel-applet and right-click context menus** (they
need pointer coordinates the harness does not know), and the `COSMIC_GL_DEBUG`
allocate/delete imbalance pass, which needs the env to reach the compositor —
see runbook §5, and note `~/.config/environment.d` does *not* deliver it.

The fault round covers the two constraints-failure branches only. The idempotent
`remove_session`/`remove_cursor_session` hardening on `Output` and
`CosmicSurface` is defence-in-depth for callers that remove twice; no caller does
so today, so it remains verified by inspection rather than by the harness.
