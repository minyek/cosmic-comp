# VRAM leak investigation runbook

Operational companion to [`vram-leak-investigation.md`](./vram-leak-investigation.md).
That doc describes the diagnostic *mechanisms*; this one is the *procedure* —
the exact command sequence to localise the residual VRAM leak after a session
restart, and the division of labour between the user and the agent. For the
current state of the hunt — what's confirmed, ruled out, and still open — see
[`vram-leak-findings.md`](./vram-leak-findings.md).

## The two-actor setup — read this first

The compositor runs as the desktop user (`andy`, uid 1000). The agent runs as a
separate, unprivileged account (`agent`, uid 1001 — no `sudo`, not in
`systemd-journal`/`adm`/`wheel`). Consequences that shape everything below:

- The **agent cannot** read the compositor's journal, signal the process, or
  read `/proc/<pid>/environ`. Every command that touches the running compositor
  or its log must be run **by the user**.
- The hand-off is a file: the user dumps output to a world-readable path under
  `/tmp` (`chmod a+r`), then tells the agent the path. `/tmp` is shared; the
  agent can read it.

The enabler that makes this work at all: the SIGUSR1 census now logs at
**`warn!`** (`src/utils/vram_dump.rs`). A release build's default log filter is
`cosmic_comp=warn`, so the census is visible **without setting `RUST_LOG`**.
(It previously logged at `info!` and was silently dropped — that is why earlier
attempts produced no data.) The GL-debug path in step 5 still needs `RUST_LOG`,
because it logs at `trace!`/`debug!`.

## 0. Pre-flight — right after logging back in (user)

Confirm the `warn!`-instrumented build is the one now running:

```bash
journalctl --user -b | grep "SIGUSR1 dumper installed"   # must print exactly once
pgrep cosmic-comp                                        # note the PID
nvidia-smi | grep cosmic-comp                            # baseline VRAM (type "G" row)
```

If the first command prints nothing, the running binary is not the new build —
reinstall (`cd ~/cosmic-comp && sudo make install`) and restart the session.

## 1. Capture protocol (user runs; one workflow per cycle)

Isolate a single suspect workflow each cycle so the census delta is
attributable. Take a census before and after, and sample VRAM throughout.

```bash
P=$(pgrep cosmic-comp)

# background VRAM sampler (per-process; Ctrl-C / kill when done)
( while kill -0 "$P" 2>/dev/null; do
    echo "$(date +%T) $(nvidia-smi | awk -v p="$P" '$0 ~ p && /cosmic-comp/ {print $(NF-1)}')"
    sleep 10
  done > /tmp/vram.csv ) &
SAMPLER=$!

kill -USR1 "$P"            # BASELINE census

#  --- exercise ONE suspect workflow ~50–100x (see §2) ---

kill -USR1 "$P"            # AFTER census
kill "$SAMPLER" 2>/dev/null

journalctl --user -b > /tmp/cosmic-census.txt && chmod a+r /tmp/cosmic-census.txt
echo "ready: /tmp/cosmic-census.txt  /tmp/vram.csv"
```

Then tell the agent: *"census ready"* and the two paths. Repeat with the next
workflow if the first cycle is inconclusive.

## 2. Suspect workflows, in priority order

Ordered by the evidence in the appendix (capture path first, then client churn).

**Empirical note:** in census runs where the capture path was exercised, every
capture counter (`sessions`, `offscreen_renderbuffers`, `pending_frames`) stayed
flat while VRAM still grew under normal desktop use, and the reporter rarely uses
screen capture at all. Treat **popup churn and app/client open-close (3 and 2
below) as the prime suspects** for the residual daily leak; the capture path
(1) appears cleared by the fixes already on this branch.

1. **Screencast / screen-recording start → stop**, repeated. The
   image-copy-capture path; every prior leak fix lives here, and the recurring
   GL error clusters around capture/render.
2. **Open/close apps repeatedly**, especially ones that crash on their own
   (cosmic-files did, ~21×). Stresses per-client connect/disconnect teardown.
3. **Popup churn** — open/close menus, applets, context menus many times
   (942 popup warnings in the prior run).
4. **Monitor hotplug** (plug/unplug or toggle outputs) and **zoom toggling**.

## 3. What the agent does with the data

Read `/tmp/cosmic-census.txt`. Each census block is delimited by
`=== VRAM/resource census (SIGUSR1) ===` and ends at the
`TOTALS (outputs only): …` line. Pull the first (baseline) and last (after)
blocks and diff every counter. Helper:

```bash
grep -nE "resource census|pending_windows=|^.*output .*: sessions=|workspaces=|toplevels=|TOTALS \(outputs only\)" /tmp/cosmic-census.txt
```

Any counter that **grew** is the leak class. Map it to the owning subsystem:

| Counter that grew | Meaning | Where to look |
|---|---|---|
| `offscreen_renderbuffers` | GLES renderbuffers on dead capture sessions — **clearest VRAM signal** | `src/wayland/handlers/image_copy_capture/` (`SessionUserData`, renderbuffer free) |
| `sessions` / `cursor_sessions` | image-copy capture sessions not torn down | `image_copy_capture` handlers; `SessionHolder` add/remove on Output/Workspace/Surface |
| `pending_frames` | frames queued but never submitted/failed | `image_copy_capture/render.rs` |
| `output_zoom_states` | `OutputZoomState` not dropped | `src/shell/zoom.rs` (one ref-cycle already fixed; check for another) |
| `minimized_windows` | minimized windows outliving close | `src/shell/workspace.rs` (`minimized_windows.retain(alive)`) |
| `toplevels` / `surface_*` | `CosmicSurface` outliving its window | shell window lifecycle; weak-ref handling |
| `pending_windows`/`pending_layers`/`pending_activations`/`override_redirect_windows`/`idle_inhibiting_surfaces` | shell pending lists / activation tokens not expiring | `src/shell` pending queues; activation-token expiry |

Then correlate with `/tmp/vram.csv`: VRAM should rise during the workflow that
grew the counter. Finally, trace that container's lifecycle — find the `add`/
`insert`/`push` with no matching `remove`/`drop`/`retain(alive)`/`prune` on the
teardown path — and fix the missing teardown (root cause, not a periodic sweep).

## 4. Decision tree

- **A counter grew with VRAM** → leak is in cosmic-comp and localised to that
  subsystem. Trace to the missing teardown and fix.
- **All counters flat but `vram.csv` still climbs** → the leak is GPU-side with
  no counted CPU handle. Go to §5 (GL allocate/delete imbalance) — likely a
  smithay-internal leak to upstream.
- **Inconclusive** → run `heaptrack ./target/release/cosmic-comp` over the same
  workflow as an independent CPU-side signal (see `vram-leak-investigation.md`
  §4).

## 5. Escalation — GL allocate/delete imbalance (user runs)

Only when §4 says "counts flat, VRAM climbs". This path needs `RUST_LOG`
because the GL callback logs at `trace!`/`debug!` (the `warn!` change only
covered the census), and it needs `COSMIC_GL_DEBUG=1` to install the callback
at all.

> **The env vars MUST reach the `cosmic-comp` process, and
> `~/.config/environment.d` does NOT deliver them to it.** COSMIC's
> `cosmic-session` launches `cosmic-comp` *first* — with only
> `COSMIC_SESSION_SOCK` added on top of its own inherited environment — then
> `cosmic-comp` reports `WAYLAND_DISPLAY` back, and only *after* that does
> `cosmic-session` read `environment.d` and inject the augmented env into every
> *later* child (panel, launcher, …). So `environment.d` reaches every COSMIC
> component **except the compositor**, which is the one we instrument. (Observed
> 2026-06-09: a full diagnostic session produced zero GL output because of this;
> the launch line read `starting process ' COSMIC_SESSION_SOCK=12 cosmic-comp '`
> with no debug vars, and `cosmic-comp` ran at the default `warn` filter — no
> `Version:` info line, no `GL_KHR_debug callback installed` line.)

The vars must therefore enter the environment **above `cosmic-session`** (the
PAM login session it inherits), or be injected directly into `cosmic-comp`'s
exec. Use `/etc/environment` (system-wide PAM login env, inherited by the whole
graphical session from first process; needs root, easy to revert):

```bash
# as root (user andy has sudo); revert by deleting these lines + re-login
sudo tee -a /etc/environment >/dev/null <<'EOF'
COSMIC_GL_DEBUG=1
COSMIC_GL_DEBUG_SYNC=1
RUST_LOG=cosmic_comp=warn,cosmic_comp::utils::gl_debug=warn,smithay=warn,calloop=error,cosmic_text=error
EOF
# then log out / log back in (or reboot)
```

The `gl_debug=warn` directive is the Pass A (backtrace) capture below; Pass B
swaps it to `trace` — see the volume caveat before doing so.

**Verify the vars actually reached `cosmic-comp` — in seconds, before spending an
hour capturing** (run as andy, uid 1000, who can read the compositor's `/proc`):

```bash
tr '\0' '\n' < /proc/$(pgrep -x cosmic-comp)/environ | grep -E 'COSMIC_GL_DEBUG|RUST_LOG'
journalctl --user -b | grep -iE 'GL_KHR_debug callback installed|not available on this driver|Version:'
```

`GL_KHR_debug callback installed (sync=true)` appearing within seconds of login
= instrumentation is live. **Filter caveat:** the install confirmation logs at
`warn!` and the install *failure* paths also log at `warn!`, so the gl_debug
directive in `RUST_LOG` must be `warn` or lower for this check to mean anything
— under a `…gl_debug=error` directive both are suppressed and the journal can't
distinguish "installed silently" from "failed silently". (Builds installed
before 2026-06-10 logged the confirmation at `info!`, invisible even at `warn`;
for those, verify via `/proc/<pid>/environ` above and rely on the first
HIGH-severity `GL:` line — see the in-session discriminator below.) If the vars
are absent, `/etc/environment` did not propagate through the greeter's PAM
stack; fall back to a wrapper that injects them into `cosmic-comp` directly
(re-apply after every `sudo make install`, which overwrites
`/usr/bin/cosmic-comp`):

```bash
sudo mv /usr/bin/cosmic-comp /usr/bin/cosmic-comp.real
sudo tee /usr/bin/cosmic-comp >/dev/null <<'EOF'
#!/bin/sh
export COSMIC_GL_DEBUG=1 COSMIC_GL_DEBUG_SYNC=1
export RUST_LOG=cosmic_comp=warn,cosmic_comp::utils::gl_debug=trace,smithay=warn,calloop=error,cosmic_text=error
exec /usr/bin/cosmic-comp.real "$@"
EOF
sudo chmod +x /usr/bin/cosmic-comp
```

Once verified live, **reproduce the provoking workload** — the GL errors are
bursty and track window operations (open/close many windows, change display
scale, drag between outputs), *not* idle time; a light idle session surfaces
nothing. Then capture:

```bash
journalctl --user -b > /tmp/cosmic-gl.txt && chmod a+r /tmp/cosmic-gl.txt
```

**Volume / rate-limit caveat.** With `cosmic_comp::utils::gl_debug=trace`, NVIDIA
emits a `Buffer detailed info` NOTIFICATION on *every* `glGen*`/`glDelete*` →
one `trace!` line each → thousands/sec, which journald will rate-limit and drop
(`Suppressed N messages`), corrupting the allocate/delete counts. Two clean
passes avoid this:
- **Pass A — error backtraces (low volume, do this first):** set the gl_debug
  target to `warn` instead of `trace`
  (`…,cosmic_comp::utils::gl_debug=warn,…`). The callback installs and logs
  HIGH-severity errors + `GL error call site:` backtraces (both at `error!`)
  plus MEDIUM-severity driver warnings and the install confirmation (`warn!`),
  while the per-alloc NOTIFICATION firehose (`trace!`) and LOW chatter
  (`debug!`) are filtered out. Do not use `=error`: it also suppresses the
  install confirmation *and* the install-failure warnings, making liveness
  unverifiable from the journal. This is the decisive capture for localising
  the 48 `GL_INVALID_VALUE` errors.

  **In-session liveness discriminator:** cosmic-comp's callback replaces
  smithay's own per-context one, so once installed, GL errors arrive as `GL: …`
  lines (+ backtrace in sync mode) — smithay-prefixed `[GL] …` error lines
  appearing *without* a paired `GL:` line mean the install silently failed;
  re-login after fixing the env rather than continuing the capture.
- **Pass B — allocate/delete imbalance (high volume):** use the `trace` target
  and first disable journald rate-limiting for the run:
  `printf '[Journal]\nRateLimitBurst=0\n' | sudo tee /etc/systemd/journald.conf.d/99-nolimit.conf && sudo systemctl restart systemd-journald`
  (revert by deleting the drop-in). Then grep `Suppressed` in the capture to
  confirm nothing was dropped.

First calibrate to the driver's actual phrasing. cosmic-comp's callback
(`src/utils/gl_debug.rs`) logs `GL: <driver message>` — note the `GL:` prefix,
which is distinct from smithay's own `[GL] …` error logging (the `[GL]` lines
appear even without `COSMIC_GL_DEBUG`; match `GL:` here, not `[GL]`). NVIDIA's
exact wording for allocate vs. delete varies by driver, so bucket the distinct
message templates (digits normalised) to see them:

```bash
grep -oE 'GL: .*' /tmp/cosmic-gl.txt | sed -E 's/[0-9]+/N/g' \
  | sort | uniq -c | sort -rn | head -30
```

Identify the allocate template (typically `… object N … will use VIDEO memory …`)
and the matching delete/free template, then count each per object class:

```bash
for kind in Buffer Texture Framebuffer Renderbuffer; do
  a=$(grep -c "GL: .*$kind .*VIDEO memory" /tmp/cosmic-gl.txt)   # allocate (adjust to template above)
  d=$(grep -c "GL: .*$kind .*deleted"      /tmp/cosmic-gl.txt)   # delete   (adjust to template above)
  echo "$kind: alloc=$a delete=$d net=$((a-d))"
done
```

The object class with the largest positive `net` (allocated far more than
deleted) is the leak. `gl_debug.rs` documents the mechanism; remove the env file
when finished (it is verbose).

## Appendix A — leads from the prior 3.8-day run

Captured from the journal of the pre-`warn!` session (Jun 1–5, ~+435 MiB/day,
roughly linear; VRAM reached ~1.8 GB). These are *leads*, not localisation —
the per-day rates were flat, consistent with an activity-driven linear leak:

- **122× `[GL] GL_INVALID_VALUE … Size and/or offset out of range`** — a
  recurring renderer error, clustered right after popup churn. Suspect the
  capture/popup render path.
- **942× `surface missing from known popups`** — high-frequency popup
  bookkeeping warning.
- **Client-app panics** (cosmic-files ~21×, `xdg-desktop-portal-cosmic`,
  cosmic-applets) — these crash in the *clients*, not the compositor, but the
  repeated disconnects stress compositor-side teardown.

## Appendix B — known latent bug (not the leak)

`src/wayland/handlers/image_copy_capture/render.rs:299` calls
`output.remove_session(session)` on a capture-failure path, and
`Output::remove_session` (`user_data.rs:171`) does
`.get::<ImageCopySessionsData>().unwrap()` — which panics if that output never
had an image-copy session. A screencast client can crash the compositor. It did
not fire in the 3.8-day run, but should be fixed (guard the `get`, or remove
from the correct container for the capture source).
