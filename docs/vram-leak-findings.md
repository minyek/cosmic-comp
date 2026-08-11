# VRAM leak findings log

Living record of what the VRAM-leak hunt on branch `pr-2095` has established —
confirmed facts, ruled-out hypotheses, open leads, and how to resume. Companion
to the [toolkit](./vram-leak-investigation.md) (mechanisms) and the
[runbook](./vram-leak-runbook.md) (capture procedure). Update this as evidence
arrives; keep entries dated so the timeline stays legible.

## Current status

**2026-08-11 (fault round, md5 `e54aa008`): PASS — the capture failure paths are
now covered, and the workspace-capture panic fix `1148a2c7` is verified by
execution rather than by inspection.**

`COSMIC_FAULT_CAPTURE_CONSTRAINTS=1` reached the compositor (`Fault armed:
COSMIC_FAULT_CAPTURE_CONSTRAINTS` at startup) and both branches took the
injected failure: **56** `Failing screencopy constraints for workspace` and
**58** for toplevel across six overview cycles and three screenshots. No panic
anywhere in the journal, invariants clean, and every capture session torn down
by the settle census. This is the round the 2026-08-09 entry called for — the
removal-and-stop code behind the constraints failures had never executed under
test before, and it now has, ~114 times.

*Verdict-tool defect found by this round, fixed.* It first reported
`queue.framebuffer=1 — renderer cleanup queue not drained` at the baseline
census, which was a sampling artifact, not a leak: that census recorded
`queued_framebuffer=91305` against `drained_framebuffer=91304`, and the next
census showed both at `92627`. A cleanup queue depth is an in-flight count, the
compositor renders continuously, and the census arrives asynchronously on
SIGUSR1 — with framebuffers churning in the hundreds per second, catching one
mid-drain is luck. Requiring zero at every instant had been passing by luck up
to this point. The queues are now judged by whether they *drain*: occupied at
the settled final census, or across two consecutive censuses, still fails, and
both shapes are regression-tested against synthetic captures.

*Second defect, same round.* Re-scoring an archived round returned PASS with
"every phase evidenced real activity" while checking nothing at all — its
`expectations.csv` had been archived empty and its labels lost, because
`session.sh` archived the journals but not `marks.csv`. An empty expectation set
is now a failure in itself, since a verdict that checks no phase is the silent
no-op this half of the verdict exists to prevent, and the archive keeps a copy
of `marks.csv`.

**2026-08-11 (second round, md5 `e54aa008`): PASS — invariants hold and, for the
first time, every phase is evidenced by a counter its own workload moves. No
leak found.**

Fifteen censuses, two outputs. Every invariant held at every one: cleanup queues
drained, no `dead` cache entries, no capture sessions or offscreen renderbuffers
outliving their client, `surface_threads == outputs == 2`, `live_slots` flat at 4
with swapchain generations advancing, and no panic in the journal. Live GL
objects went 32 → 42 textures and 20 → 31 EGL images across the whole round, the
difference tracking the two retained zoom OSD elements and the desktop's own
state rather than growth under churn.

What each phase actually evidenced, against the minimum it declared:

| phase | evidence | observed | min |
|---|---|---:|---:|
| popups | `egl_images_created` | 582 | 64 |
| pointer | `pointer_motions` | 200 | 100 |
| zoom | `zoom_changes` | 24 | 12 |
| workspaces | `workspace_activations` | 13 | 5 |
| capture | `renderbuffers_created` | 12 | 6 |
| selftest | `ws_sessions` peak | 4 | 1 |
| apps | `toplevels` above baseline | +1 | +1 |
| minimize | `minimized_windows` peak | 1 | 1 |

Synthetic input delivery is exact where it can be checked: 200 motion events for
200 `mousemove` calls, 24 zoom changes for 24 keystrokes.

*Open lead — `Super+N` presses are silently dropped, and this one is probably a
compositor bug rather than a harness artifact.* The `workspaces` phase drives 21
presses across the full round and only 13 reach `Shell::activate`; a single
round measured in isolation, with the desktop quiet (13 pointer events over the
whole phase), yields 3 activations for 4 presses. `Shell::activate` is the sole
caller path for `Action::Workspace` and increments unconditionally on success, so
the missing presses never matched as shortcuts at all.

The yield is *variable* — 2.6 per round across the full run, 3.0 in isolation —
which rules out the obvious explanations: a key left unbound would lose the same
one every round, and zoom-phase state cannot be it because the isolated round ran
with no zoom before it. Dropped uinput events are ruled out too, since the `zoom`
phase delivers 24 changes for 24 keystrokes through the identical `key` helper at
the same cadence, and `pointer` delivers 200 for 200. What remains is a timing- or
state-dependent loss upstream of `Shell::activate`, in shortcut matching itself.
A workspace shortcut that intermittently does nothing under repeated presses is
user-visible and unrelated to VRAM, so it is recorded here as a lead rather than
chased: isolating it needs a log line per matched `Action::Workspace`, which costs
a rebuild, reinstall and logout to answer a question no leak verdict rests on.

The `workspaces` minimum is set from the measured yield rather than the nominal
4, leaving the check a 3× margin.

**2026-08-11 (first round, md5 `aa870af2`): invariants clean, but the round is
not scoreable — the activity check's *evidence counters* turned out to be
unsound, not just the span they were measured over. Fixed by counting the
workload itself.**

The round drove all nine phases against installed md5 `aa870af2` (compositor
started 13:47:41, binary installed 13:44:50, so the live process is the build
under test). Every invariant held at all 14 censuses: cleanup queues drained,
no `dead` cache entries, no capture sessions or offscreen renderbuffers
outliving their client, `surface_threads == outputs == 2`, `live_slots` flat at
4 with swapchain generations advancing, and no panic anywhere in the journal.
Live GL objects returned to baseline (35 textures at start, 51 at settle, of
which the difference is accounted for below).

`verdict` failed `pointer` and `workspaces` for driving too few textures. That
was not a dead phase — it was the check being wrong. Scoring each phase's
declared evidence against the **idle rate measured in the run's own 90 s settle
window** shows the counters cannot carry the claim:

| phase | evidence | rate | idle rate | ratio |
|---|---|---:|---:|---:|
| capture | `renderbuffers_created` | 0.50/s | 0.02/s | 23× |
| popups | `egl_images_created` | 11.10/s | 0.97/s | 11× |
| zoom | `textures_created` | 43.21/s | 9.99/s | 4× |
| apps | `egl_images_created` | 1.41/s | 0.97/s | 1.5× |
| workspaces | `textures_created` | 5.33/s | 9.99/s | **0.5×** |
| pointer | `textures_created` | 4.47/s | 9.99/s | **0.4×** |

An idle desktop creates textures at ~10/s and EGL images at ~1/s, so a delta
threshold over either measures how long a phase took, not what it did: a dead
phase passes by lasting long enough, and `pointer` and `workspaces` — which
legitimately render *less* than an idle desktop, because cursor motion goes to
the cursor plane rather than through composition — fail while working perfectly.
The single global `texture_churn` threshold of 500 could not be recalibrated per
phase, because the phases' workloads differ by two orders of magnitude.

Fixed by making each phase's evidence a counter only that phase's workload can
move. `src/utils/workload_counters.rs` counts `pointer_motions`,
`workspace_activations` and `zoom_changes` at the point the input is processed;
`apps` now censuses *while a window is open* and requires `toplevels` above its
own phase baseline; each phase declares its own minimum in `expectations.csv`,
derived from its round count at roughly half the expected value so the check
fails a dead phase rather than a slow one. `zoom` is counted rather than read
from `output_zoom_states`, which saturates — smithay's `UserDataMap` has no
removal API, so that flag is stuck true once zoom has been used at all. A
capture whose build predates a counter now fails with "absent from the census"
instead of passing on a counter that was never emitted. Verified against
synthetic captures: each phase killed in isolation fails its own check and only
its own.

*Blast radius.* The **invariants** half of every previous verdict is unaffected
— evaluated per census, independent of phases. The **activity** half is weaker
than the 2026-08-09 entry below claims: that entry says re-running would settle
zoom, workspaces and pointer, and it would not have. `apps` joins the
un-evidenced set (1.5× idle is not evidence), and `zoom`'s 4× is suggestive but
was never a designed margin. `capture` (23×) and `popups` (11×) stand, as do the
hand-quoted per-phase deltas in the 2026-08-06 entry. No fix verdict rests on
the un-evidenced phases, and no leak conclusion changes; what changes is that
the harness could not have caught a dead phase in four of nine cases, so the
"no leak found" result covers less than it appeared to. This round cannot be
rescored — the counters that would evidence it did not exist when it ran — and
is superseded by the re-drive recorded above, which settles all four phases.

*Also observed:* `iced_elements` rises 4 → 6 during the zoom phase and stays
there through settle. That is the zoom OSD element inside `OutputZoomState`, one
per output, retained because the state itself is never removed. It is bounded —
six zoom rounds produced exactly two, and live textures stayed flat at 53 across
them — so it is retention, not a leak, but it is why the post-zoom baseline sits
above the pre-zoom one.

**2026-08-09: the capture-panic fix is now coverable, and the verdict tool's
per-phase activity check was found broken and fixed.**

The 2026-08-06 entry closed the build-delta caveat but left one gap open: the
capture-panic fix `5bbb12e8` (carried here as `1148a2c7`) was called
"unvalidatable by this harness", because it only changes paths that fail when an
output has no current mode or an offscreen renderer cannot be built. Re-reading
the code confirms why no workload can reach them: `apply_config_for_outputs`
gives *every* output a mode, disabled ones included, before a client can bind a
capture source, and smithay's `Output` never clears `current_mode` once set. So
re-running `drive.sh all` on a build that *contains* the fix would still not test
it — the round would pass without the fixed lines ever executing.

Closed by `COSMIC_FAULT_CAPTURE_CONSTRAINTS=1` (`src/utils/fault_inject.rs`),
which fails the constraints query made while rendering a frame and forces every
frame down the constraint-mismatch branch. The fault deliberately spares the
query made at session creation: failing there stops the session before any frame
arrives, and the frame path is the one carrying the code under test. Driven by
`drive.sh faultall` — see the [process doc](./vram-regression-test-process.md) §5.

**Verdict-tool defect (found 2026-08-09, fixed): phase activity was scored across
the whole run, not the phase.** `cmd_verdict` computed each expectation's delta
from `rows[0]` to `rows[-1]`; it also built a per-phase `span` and then discarded
it (`_ = span`). Compounding it, `all()` never set `$PHASE`, so every expectation
was recorded under the phase name `all`. A phase that drove nothing therefore
passed on a busier phase's counters — precisely the failure the activity check
was added to catch.

*Blast radius on the 2026-08-06 verdict:* the **invariants** half is unaffected
(evaluated per census, not per phase). The **activity** half is only as good as
the per-phase counter deltas quoted by hand in that entry — client churn
(+99/+99 EGLImages, +9,379/+9,379 textures), image-copy capture (+12/+12
renderbuffers) and popup churn (+1,230 EGLImages) were each read off the counter
tables and stand. **Zoom, workspace switching and pointer motion were not
independently evidenced**: all three declare `texture_churn`, and any one of them
could have satisfied the check for the other two. Their "all counters flat"
result is therefore un-evidenced, not wrong. No fix verdict changes: nothing in
that entry rests on those three phases. (Scoring inside each phase's own span
was necessary but not sufficient — see the 2026-08-11 entry, which found the
counters those spans were measuring to be unsound too.)

The process doc also claimed the dead-run detection was "regression-tested
against the journals from that dead run". There is no such test in the repo; the
claim has been removed rather than left standing.

**2026-08-06: full-desktop regression pass on the installed build — every fix on
the branch verified against live interaction; no leak found.** Running
`/usr/bin/cosmic-comp` verified identical to the `instr-invalidate` worktree
build (md5 `5b013f3a…`, branch tip `c4a02f9b`); compositor PID 5334, installed
16:14 and session started 16:52, so the live compositor is the build under test.
27 censuses over 158 min, VRAM sampled every 10 s. Dual output DP-2 (3840x2160)
+ HDMI-A-1 (2560x1600). `COSMIC_GL_DEBUG` was *not* enabled this round.

- **Monitor reconfigure (3× DP-2 power-cycle):** DP-2 slot generations went
  `[1,3]` → `[338,339,341]` — ~338 swapchain generations allocated — while
  `live_slots` stayed pinned at **5** and later fell back to 4. Every superseded
  generation was freed. `surface_threads=2 == outputs`, queue depth 0, `dead=0`.
  VRAM 155 → 339 MiB (+184), sticky, of which the dmabuf inventory accounts for
  only +35 MiB — reproducing the 2026-07-29 figure (+186 MiB) within 2 MiB, i.e.
  the same driver-side pooling, not a compositor leak.
- **Client churn (10× open/close):** every counter byte-for-byte identical
  across the phase (toplevels 6→6, textures 45→45, egl_images 22→22, caches
  9/13 unchanged, VRAM 675→675 MiB), while the raw counters prove the workload
  ran: **+99 EGLImages created / +99 destroyed**, **+9,379 textures created /
  +9,379 destroyed**.
- **Image-copy capture (6× `cosmic-screenshot`):** renderbuffers **+12 created /
  +12 destroyed**, live renderbuffers 0, `sessions`/`offscreen`/`pending_frames`
  0 at output, workspace and surface scope. No panic on any capture path.
- **Workspace overview (11 open/close):** `ws_sessions` 0→**4**→0 and
  `surface_sessions` 0→**6**→0 on every cycle — the `e138ec74` teardown holds.
  The retained import caches **plateau**: live textures 46→58→66→**70** and
  `cache.main` alive 0→8→10→**14** over 1, 2 and 11 cycles, then flat through
  three further phases and 90 s idle. A linear leak would have reached ~+96 by
  cycle 11; this is a saturating dmabuf import cache.
- **Popup churn (32 cycles), zoom (24 ops), workspace switching (20) and
  cross-output window moves (10):** all counters flat; +1,230 EGLImages created
  and ~1,185 destroyed during the popup phase.
- **Minimize:** `minimized_windows` 0→**3**→0 tracking three real windows, and 0
  with no dead residue after a window was killed *while minimized*
  (`6b9e1b78`'s retain-alive path).
- **Settle:** VRAM 559 MiB with **460.8 MiB of it pinned client dmabufs**,
  leaving only ~98 MiB compositor/driver-side.
- **No compositor panics, no `GL:` errors, no DRM commit failures** in any
  phase. The 256 `GL_INVALID_OPERATION` lines in the journal are Chrome's own
  WebGL process, not the compositor.

**Build-delta caveat — the instrumented branch is NOT the clean branch plus
instrumentation.** A file-by-file audit of `all-fixes..all-fixes-instrumented-invalidate`
(2026-08-06) found two *functional* differences, in opposite directions, which
qualify what this round proves:

1. **The tested build lacks the capture-panic fix `5bbb12e8`** — the four
   `remove_*` unwraps are unguarded, both `offscreen_renderer()` unwraps remain,
   and `render_workspace_to_buffer` still removes the session from the *Output*
   rather than the workspace. The screenshot phase therefore exercised the
   pre-fix code; it did not panic (consistent with that path never firing in
   normal use) but this round provides **no validation of `5bbb12e8`**, which
   ships as its own PR. PR 2500 is unaffected — `vram-leak-fixes` lacks the fix
   by design, so the tested build matches it here.
2. **The tested build carries an extra fix that ships nowhere:** a third
   `drop_and_join()` call site in `kms/device.rs`, joining the surface thread
   synchronously on connector removal, added inside `95411f26 "VRAM-leak debug
   instrumentation (do not merge)"` and absent from both `all-fixes` and
   `vram-leak-fixes`. Its comment describes the leak it prevents — a detached
   thread stalling on the compositor read lock and stranding its renderer,
   swapchain and postprocess offscreens. **This is the path a monitor
   power-cycle takes**, and `surface_threads == outputs` is the metric it
   protects, so the reconfigure results above (and those of the 2026-07-28 and
   2026-07-29 rounds, which used the same instrumentation commit) may be better
   than the submitted code achieves. Resolve by hoisting the fix into the PR
   branch, then re-running the monitors phase.

**Both deltas are now closed** (2026-08-07), so a re-run no longer carries this
caveat. The panic fix was cherry-picked onto the instrumented branch, and the
connector-removal join now ships as its own PR branch
(`fix-surface-thread-leak-on-connector-removal`), whose code is byte-identical
to the instrumented copy — only the explanatory comment differs, which the PR
carries in its commit message instead. Both branches were then rebased onto
cosmic-comp `d3ffa814`, so the instrumented build also exercises upstream's
wl-dmabuf v6 support rather than the local API-migration shim it supersedes.
A fresh audit against `all-fixes` finds the remaining delta is instrumentation
only: added census/probe modules and counters, one `gl_debug::try_install`
call, import-line rewrites, a `render_result` → `res` rename in
`screenshot.rs`, and comment wording.

The panic fix stays **unvalidatable by this harness** either way: it converts
`unwrap()` into `if let Some(...)` on failure paths that do not fire in normal
use, so no census counter can observe it. Only fault injection would cover it.

Everything else differs only by instrumentation, rustfmt wrapping, a variable
rename and doc-comment wording. On the smithay side the instrumented branch *is*
`renderer/invalidate-caches` plus instrumentation, with every modified existing
line a tracking hook that is inert unless `COSMIC_DMABUF_TRACE` is set.

**Two instrumentation defects found this round (neither affects any verdict):**

1. **`output_zoom_states` / the per-output `zoom=` flag cannot fall.**
   `vram_dump.rs:162` reports `user_data().get::<Mutex<OutputZoomState>>()
   .is_some()`, but smithay's `UserDataMap` has no removal API, so the flag is
   stuck `true` for the output's lifetime once zoom has been used even once —
   even though `shell/mod.rs:2582` correctly drops `Shell::zoom_state` when all
   outputs return to level 1.0 (confirmed visually: no magnification). The
   counter therefore saturates at `outputs.len()` and can never signal the leak
   class the runbook's decision table assigns to it. The residual cost is
   bounded and small: 2 retained `IcedElement`s (`iced_elements` 7→9), no GL
   textures. The `zoom_state.take()` logic is upstream `0ba0a0cd`, not ours.
2. **`virtual-keyboard-v1` clients cannot drive compositor shortcuts**, so
   `wtype` is useless for scripted testing here. Shortcuts are matched in
   `filter_keyboard_input`, reached only from `process_input_event` on the
   libinput backend path (`src/input/mod.rs:168,205,257`); virtual-keyboard
   input goes straight to the focused surface. A wtype-driven phase looks like
   it ran (timings are sleep-dominated) but changes nothing — caught here only
   because `egl_images_created` stayed frozen at 1286 across 32 supposed popup
   opens. Drive synthetic input through **ydotool** (uinput → libinput), which
   enters the correct path.

**Still not covered:** long-lived screencast sessions (no recorder installed on
this machine — `cosmic-screenshot` exercises the same handlers but with a
short-lived session), panel-applet and right-click context-menu popups, and the
`COSMIC_GL_DEBUG` allocate/delete imbalance pass.

**Unchanged upstream log noise (both files verified unmodified vs upstream in
our fork):** `Failed to destroy old mode property blob: No such file or
directory` once per reconfigure (`smithay drm/surface/atomic.rs:815`; ENOENT
means the kernel already dropped the blob — nothing leaks), and 37×
`surface missing from known popups` (`smithay wayland/shell/xdg/mod.rs:1994`),
concentrated in manual menu/applet interaction. The latter is *not* eliminated —
~337/day here against ~248/day in the pre-fix 3.8-day run — but it correlates
with no counter growth.

**2026-07-29: post-rebase regression round — all three mechanisms RE-VERIFIED
PASSING on the rebased smithay; no regression from the tablet/dmabuf API
migration. Cleared to switch to the clean build.** The 2026-07-28 verdicts below
predate the smithay rebase onto current upstream (reworked tablet types, dmabuf
preference tranches) and the review-hardening of `invalidate_caches`, so this
round re-runs the three mechanisms against the build that actually carries them.
Running binary verified identical to the instrumented worktree build
(md5 `8756399b…`); compositor PID 5303; census via `SIGUSR1`, VRAM sampled every
10 s.

- **Mechanism 3 (destruction-scheduled drain), isolated:** app open→close.
  `toplevels` 3→4→3 confirms the client really mapped and exited. `textures`
  37→43→**34** and `egl_images` 26→31→**23** both settled *below* baseline;
  dmabuf caches returned to `main=0 / DP-2=14 / HDMI-A-1=9`; queue depth 0,
  `dead=0` throughout. Pass.
- **Mechanism 2 (drain-after-capture), isolated:** 4× non-interactive
  `cosmic-screenshot`. `textures` 37→36, `egl_images` 26→25, `sessions` and
  `offscreen_renderbuffers` 0 on both sides, queue 0, `dead=0`. Pass.
- **Mechanism 1 (invalidate-after-reconfigure), isolated:** 3× DP-2
  power-cycle. Counters bracket-identical across the cycles — `egl_images` 20→20,
  caches `0/8/11` → `0/8/11`, `alive=0 dead=0`, queue 0 (`textures` 37→43 sits
  inside the round's observed 33–46 idle oscillation band). The decisive evidence
  is slot lifetime: the DP-2 swapchain regenerated through generations
  202→288→301→314 while `live_slots` stayed pinned at **4–5** (2 × 2560x1600 +
  2–3 × 3840x2160), i.e. every superseded generation was freed rather than
  accumulating. `live surface threads=2 == outputs` — no stranded surface
  threads. Pass.
- **Idle-decay check (~4.5 min, no interaction):** counters byte-for-byte
  identical (`textures=37 egl_images=20`, caches `0/8/11`, `dead=0`); VRAM in a
  332–359 MiB band with no directional drift.
- **No errors anywhere in the round:** zero `GL:` errors and zero GL error
  backtraces (against 122× `GL_INVALID_VALUE … Size and/or offset out of range`
  in the pre-fix 3.8-day run), zero DRM/atomic commit failures across ~6 monitor
  reconfigures, `egl_images_freed_on_import_error: 0`. The only `GL:` output was
  NVIDIA `GL_PIXEL_PACK_BUFFER` *performance* hints from the screenshot readback.

**Monitor reconfigure is the most VRAM-expensive workflow, and the cost is
driver-side.** The three clean power-cycles moved cosmic-comp 345 → **531 MiB**,
which then held flat for 3+ min with every compositor-side counter unchanged —
the runbook §4 "counters flat, VRAM climbs" branch. Of the 523 MiB live at
settle, the dmabuf-fd inventory accounts for **271.6 MiB across 30 fds** as
genuinely pinned live buffers, leaving ~251 MiB with no GL handle and no held
dmabuf fd. Each reconfigure allocates a fresh 4K swapchain generation whose
slots are imported and sampled cross-process, so this is the 2026-06-22
root-caused NVIDIA pooling behaviour hitting its most expensive input, not a new
compositor leak. Worth knowing operationally: a user who power-cycles displays
often will see the sticky floor rise faster than one who does not.

**2026-07-28: event-driven renderer-cache-cleanup fix (PR 2500 addendum) — all
three mechanisms VERIFIED PASSING in isolation; open watch-item resolved.**
Completes the 3-cycle retest referenced in the 2026-07-26 entry below (cycle 1 —
reconfigure — had already passed; cycles 2/3 run this session). Live compositor,
PID 25988, census via `SIGUSR1` + `journalctl --user -b`, VRAM via `nvidia-smi`
polled every 10 s:

- **Carried-over watch item resolved:** the prior test round's `renderer cache
  detail main … alive=1 dead=1` (one stale, not-yet-reaped cache entry) cleared
  naturally — a zero-cost re-census this session showed `alive=0 dead=0`, matched
  by an independent census already logged mid-session at 13:27:38. Confirms it was
  reaped by the destruction-scheduled drain on a later `refresh()`, not a leak.
- **Mechanism 3 (destruction-scheduled drain), isolated:** single app open→close
  (gnome-calculator), no monitor toggling. `textures` 69→66, `egl_images` 27→24
  (net decrease after close), cleanup queue depth 0→0, `dead=0` both sides, VRAM
  flat (1079→1081 MiB). Pass.
- **Mechanism 2 (drain-after-capture), isolated:** 4× rapid `cosmic-screenshot`
  captures. GL live-object counts flat (textures 66→67, egl_images 24→25 —
  noise-level), queue depth 0→0, `dead=0` both sides. Pass.
- **Idle-decay check (15 min idle):** census counters byte-for-byte identical
  before/after (`textures=66 egl_images=24`, `dmabuf_cache` 0/10/13 unchanged,
  `dead=0` both sides). VRAM oscillated in a tight 1073–1087 MiB band with no
  directional drift (first sample 1081 MiB, last 1073 MiB) — the plateau
  signature consistent with the driver dmabuf-pooling behavior below, not
  compositor-side growth.
- Mechanism 1 (invalidate-after-reconfigure) had already passed a prior round in
  this session (3× DP-2/HDMI-A-1 reconfigure cycles, cleanup queue stayed 0,
  caches bounded).

All three event-driven mechanisms now have isolated, passing evidence. Combined
with the 2026-06-22 root-cause finding, the remaining VRAM residual is
attributable to the NVIDIA driver dmabuf-pooling behavior, not a compositor leak.

**2026-07-26: driver leak CONFIRMED STILL PRESENT on 610.43.03** (up from
595.71.05) — re-ran the standalone reproducer, zero compositor code, after the
driver package upgrade. `repro_cross_process 20000 1 1920 1080 0` (cross-process
import + sample, one size): importer climbs 8 → 2278 MiB by iter 2000, then holds
flat at **~2178 MiB** through iter 20000 — same shape, same order of magnitude as
the original finding below. Control (`draw=0`, import without sampling) stays
flat at **6 MiB** the entire run, isolating the trigger to import+sample exactly
as before. Not fixed upstream in this driver revision; still not a cosmic-comp
bug. This is a separate check from the compositor-level 3-cycle event-driven-cache
retest (cycle 1 — reconfigure — passed on its own tracked counters; cycles 2/3 —
capture, client-exit — not yet run).

**2026-06-22: RESOLVED — the residual is an NVIDIA driver leak (confirmed on
595.71.05 with a standalone reproducer), NOT a compositor object leak.** The
fresh long-session census (snapshot `…-183219`, PID 1468471, ~25 h, **681 MiB**)
settled both open questions:

- With the validation-EGLImage accounting now balanced, the honest counters are
  **all bounded**: `egl_images` 21 live (created 6178 / destroyed 6157),
  `textures` 45 live (24038/23993), renderbuffers/buffers/framebuffers all
  `created == destroyed`, every cleanup queue drained. dmabuf-fd inventory flat at
  **159 MiB** whether nvidia-smi reads 122, 483, or 681 MiB. So Leak A is genuinely
  fixed and there is **no remaining compositor-side GL-object leak** — every
  resource cosmic-comp/smithay allocate is freed.
- The 681 − 159 ≈ **522 MiB of GPU memory has no counted GL handle and no held
  dmabuf fd.** Per-interval attribution (27 censuses joined to `/tmp/vram.csv`):
  during *flat*-VRAM stretches the renderer churned **1.14 M framebuffers** with
  zero VRAM growth (per-frame FBO churn does not leak); VRAM climbed only during
  *window operations*, tracking **client-buffer import** churn (+13 626 textures,
  +3 275 EGLImages over the climb).

**Root cause (proven, not inferred — [reproducer](./vram-tools/nvidia-dmabuf-leak-repro/)):**
a ~90-line standalone program with no compositor code shows that on 595.71.05 the
driver leaks GPU memory **only when a *foreign* (cross-process) dmabuf is imported
*and sampled*** in a draw:

| scenario (≈20 k imports) | importer GPU memory |
|---|---|
| same-process import + sample | flat ~6 MiB |
| cross-process import, **no** sample | flat ~6 MiB |
| cross-process import **+ sample**, one size | →2.2 GB then **plateaus** |
| cross-process import **+ sample**, varying sizes | 1.5–2.2 GB (bounded) |

The driver makes a private VRAM copy of the foreign buffer on first sample (it
cannot sample a foreign-layout buffer directly) and **pools that copy instead of
freeing it** when the `EGLImage`/texture is destroyed. The pool is bounded (~2 GB
even under extreme churn) but large and slow to release, so for the compositor it
manifests as elevated, activity-driven, idle-sticky VRAM that resets only on
restart (matches the login-floor reset). This is the same class of bug reported on
the NVIDIA forums for kwin/sway/weston ("not freeing VRAM after resizing windows")
— a driver issue every Wayland compositor hits, **not** a cosmic-comp bug.

**Consequences:** there is nothing left to fix in cosmic-comp/smithay for the
residual — a compositor must import and sample client buffers. The genuine,
shippable win is **Leak A** (smithay non-thread-safe `UserData` dmabuf leak,
verified 635→196 MiB). Open follow-ups are driver-side only: file the NVIDIA bug
with the reproducer above, and/or test whether a different driver version reclaims
the pool. The prior EGLImage-leak hypotheses (and "Leak C") are fully retired.

**2026-06-21 (later): "Leak C" RETRACTED — it was an instrumentation artifact, not
a leak. The `egl_images` counter was lying.** The fixed-build census (snapshot
`…-155645`, PID 989054) settled it:

- `egl_images_freed_on_import_error: 0` — the `import_egl_image` error path I
  blamed **never fired**. The "Leak C" fix caught nothing.
- Yet `egl_images` still read 535 "live" (created 897 − destroyed 362). The
  EGLImage allocation-site tracer (built as insurance) named the dominant survivor
  group **n=201** at `cosmic_comp::state::BackendData::dmabuf_imported` →
  `create_image_from_dmabuf`.
- That handler (`kms/mod.rs:578`) **validates** every client dmabuf by creating an
  EGLImage and immediately destroying it with a **raw `DestroyImageKHR`**
  (`kms/mod.rs:581`) that bypasses `egl_image_destroyed`. So each validated client
  buffer did `egl_images_created += 1` with **no** matching destroy — the counter
  (and the handle-keyed registry) drifted up one per client buffer and *looked*
  like a leak. The image is genuinely freed; only the accounting was wrong.

After excluding that artifact, the only genuinely-live EGLImages are the
render-path survivors (`n=2,2,3,4,8` ≈ 19): swapchain/scanout render targets
(`MultiRenderer::bind`/`render_frame`) and current client surface textures
(`from_surface`) — i.e. images that *should* be live with two outputs and warp
open. **No EGLImage leak is demonstrated.**

**Fix applied (instrumentation correctness, not a leak fix):** smithay now exposes
`note_egl_image_destroyed(handle)`, and `dmabuf_imported` calls it after the raw
`DestroyImageKHR`, so the validation create/destroy balance. The
`import_egl_image`-error queue-the-orphan change is kept as a correct defensive
fix (a real but unexercised resource leak on that error path) — *not* the cause of
any observed VRAM.

**Open / next:** with the counter now honest, take a *long*-session census and check
whether `egl_images` (and cosmic-comp `nvidia-smi`) actually grow unboundedly or
plateau. cosmic-comp was **122 MiB** in this short session (vs 483 MiB earlier in a
long one); with dmabuf Leak A already fixed (635→196 MiB), the residual may be a
normal high-water mark rather than a leak. Re-establish whether there is a leak at
all before chasing another cause.

---

**2026-06-21 (earlier, SUPERSEDED by the retraction above): SECOND LEAK (call it
"Leak C") — orphaned `EGLImage` on the `import_dmabuf` error path. Root cause from
counting; fix built, awaiting runtime verification.** After the Leak-A/B fixes, a desktop session (all apps closed except
warp) still held **483 MiB** in cosmic-comp (`nvidia-smi`). The SIGUSR1 census
(snapshot `…-20260621-145039`, PID 375454) was decisive:

```
GL live objects: textures=38 egl_images=1897 renderbuffers=0 framebuffers=0 buffers=6
GL raw counters: egl_images_created: 3397, egl_images_destroyed: 1500
```

`egl_images` is stuck at **1897 live** with only **38 live textures** and a constant
window set. Counting proves these are *orphaned bare handles*, not texture-owned:

- `use_system_lib` is **off** in cosmic-comp's smithay features, so `EGLBuffer` /
  `egl_buffer_contents` are not compiled. The *only* compiled `CreateImageKHR` site
  is `create_image_from_dmabuf` (`egl/display.rs:843`), called from exactly one
  place: `GlesRenderer::import_dmabuf` (`gles/mod.rs:1305`).
- Every destroyed EGLImage went through the `GlesTextureInternal::drop` queue
  (`queued_egl_image == drained_egl_image == egl_images_destroyed == 1500`);
  `EGLBuffer::drop` fired **0** times.
- In `import_dmabuf` the single fallible step between creating the image and
  storing it in a `GlesTexture` is `import_egl_image(image, …)?`. On error the bare
  `image` handle was dropped — no `GlesTexture` ever owned it, so it was never
  queued, never `DestroyImageKHR`'d. 3397 created − 1500 texture-owned-and-dropped ≈
  **1897 leaked on that error path** — exactly the live count. (On the proprietary
  NVIDIA driver each imported EGLImage backs a real GPU allocation, so this pins
  hundreds of MiB.)

**Fix (smithay `gles/mod.rs`):** in `import_dmabuf`, when `import_egl_image` errors,
route the orphaned `EGLImage` through the same deferred-destruction queue a
`GlesTexture` uses on drop (`CleanupResource::EGLImage`), instead of dropping the
bare handle. Upstreamable (independent of the multigpu/threadsafe fixes).

**Diagnostic added (instrumentation branch):** an `egl_images_freed_on_import_error`
counter at that exact path, and a per-`EGLImage` allocation-site registry
(`debug_egl_image_sites`, gated on `COSMIC_DMABUF_TRACE`) surfaced in the census as
`egl-image-site n=…` lines.

**Verify (next census on the new build):**
1. `egl_images` live falls from ~1897 to a few tens (≈ live textures);
2. `egl_images_freed_on_import_error` reads ≈1897 (confirms this path was the
   source);
3. `egl-image-site` survivor groups are empty (or, if any remain, their backtrace
   names the *next* leak to chase).

The open second-order question — *why* `import_egl_image` fails this often (likely
benign per-node multigpu import attempts that fall back) — is separate from the
leak: a failed import must not leak its image regardless.

**2026-06-20: ROOT CAUSE FOUND + ONE-LINE FIX — the leak is smithay's non-thread-safe
`UserData` deliberately leaking the slot's cached `Dmabuf` when the slot is dropped off
its creating thread.** The chain, fully closed:

1. `Slot::export()` (smithay `swapchain.rs:241`) caches the exported `Dmabuf` in the slot's
   `UserDataMap` via the **non-thread-safe** `insert_if_missing`, which records the *creating
   thread's* id (`UserData::set`, `utils/user_data.rs:56`).
2. `UserData::drop` (`utils/user_data.rs:94`) only runs `ManuallyDrop::drop` **if dropped on
   that same thread** — otherwise the comment literally says *"leak it otherwise"* and skips
   the drop (to avoid dropping possibly-`!Send` data on the wrong thread).
3. The main renderer exports these slots on the **main thread** during
   `allow_frame_flags`/`apply_config` (`device.rs:949-969`); the slots' `InternalSlot`s are
   later dropped on a **different** thread → the cached `Dmabuf`'s `Drop` never runs → its
   `Arc` strong count is orphaned and the 33 MiB GBM buffer is pinned forever. The
   `Box<ManuallyDrop<Dmabuf>>` itself is freed, which is exactly why the matched reverse-ref
   found the orphans had **no `[base, clone_id]` value anywhere** (freed without `Drop`), and
   why the surface-thread path (same-thread export+drop, tracer group `n=4`) does **not** leak.

**This is a smithay bug:** `Dmabuf` is `Send + Sync`, so it never needed thread-affine
storage — and the sibling framebuffer cache one line away (`compositor/mod.rs:1770`) already
uses `insert_if_missing_threadsafe`. **Fix (`swapchain.rs`):**
`insert_if_missing` → `insert_if_missing_threadsafe` for the `Dmabuf` cache. Upstreamable.
(Minor possible siblings noted: `drm/surface/gbm.rs:215,269` cache `fb` non-thread-safe, but
that's a tiny DRM handle, not the byte leak, and `fb` may legitimately be `!Send` — left alone.)

**Verify:** rebuild, relogin, baseline census, ~3 DP-2 power-cycles, idle, final census.
**Pass = the `main` dmabuf-cache stays bounded (no old-generation 4K ids accumulating), the
`dmabuf clone-site` orphan groups vanish, and VRAM returns to the login floor.**

**VERIFIED FIXED (2026-06-20, fixed build PID 2620271, snapshot `…-110816`).** Fresh login
(login alone leaked 13 orphans on the old build):

| signal | old build (`…-093115`) | fixed build (`…-110816`) |
|---|---|---|
| `main` dmabuf-cache | `alive=18` (≈13 4K/1440p orphans) | **`alive=5`** — all are current swapchain slots |
| `dmabuf clone-site` groups | `n=9`,`n=3` render-target orphans | **none** — only live slots + one client (`[28,29,30]`) |
| dma-buf fds / MiB | 33 / **635.7 MiB** | 20 / **196.1 MiB** |
| cosmic-comp `nvidia-smi` | 234 MiB | **178 MiB** |

Every tracked dmabuf is now live: the 5 `main` entries `[1,4,49,50,52]` are exactly the two
compositors' current swapchains (`compositor_map crtc81=[49,50,52]`, `crtc62=[1,4]`), and the
only non-cache group `[28,29,30]` is a client's own buffers (`DmabufParamsData::request ←
dispatch_clients`). The ~440 MiB of forgotten render-target clones is gone. (Capture was
fresh-login without cycling; cycling only repeats the now-fixed `apply_config` path, so a
power-cycle run is optional extra assurance.)

**2026-06-20: TRACER RESULT — the escaped clone is BORN at `DrmCompositor::render_frame`'s
`Slot::export()`, on the MAIN renderer's `allow_frame_flags`/`apply_config_for_outputs`
path. Confirmed at a fresh login (no cycling needed).** The `COSMIC_DMABUF_TRACE` clone
tracer (snapshot `…-093115`, PID 1248796) grouped every live render-target `Dmabuf` clone
by birth site. The census: `main` cache `alive=18 dead=0` (all `sc1`); `live_slots total=5`
(InternalSlots are bounded — slots ARE freed); `compositor_map crtc62 swapchain=[1,3]
current=3`, `crtc81 swapchain=[136,138,140] current=138 pending=- queued=- next=-`. Six
distinct `dmabuf clone-site` groups:

| n | ids | birth site | verdict |
|---|---|---|---|
| 9 | 115,116,118,126,127,129,137,138,140 | `Slot::export ← render_frame ← LockedDevice::allow_frame_flags ← apply_config_for_outputs ← refresh_output_config ← init_udev` | **LEAK** (138,140 current; rest old gens) |
| 3 | 114,125,136 | `Slot::export ← test_format ← find_supported_format ← DrmCompositor::new ← initialize_output ← apply_config` | **LEAK** (format-probe slots that become the swapchain) |
| 2 | 3,4 | `Slot::export ← render_frame ← allow_frame_flags ← … ← init_backend` | mixed (3 current/crtc62, 4 old) |
| 2 | 1,2 | `Slot::export ← test_format ← DrmCompositor::new ← initialize_output ← init_backend` | mixed (1 current, 2 old) |
| 4 | 6,7,139,141 | `Slot::export ← render_frame ← DrmOutput::render_frame ← SurfaceThreadState::redraw` | **live** (surface-thread scanout — legit) |
| 6 | 43,44,45,64,65,66 | `Dmabuf::clone ← DmabufParamsData::request ← dispatch_clients` | **client imports** (legit; NOT the leak) |

**Mechanism (now code-confirmed):** `Slot::export()` (swapchain.rs:240) builds the
render-target `Dmabuf` once and **caches it in the slot's `UserDataMap`**, returning a
clone each call. `render_frame` exports the acquired primary-plane slot and wraps it as
`ScanoutBuffer::Swapchain(Arc::new(primary_plane_buffer))` in the frame state. **The MAIN
renderer renders+commits each `compositor_map` compositor once per `apply_config`**
(cosmic-comp `device.rs:965-970`); login fires several `apply_config`s (init_backend +
per-udev), each regenerating the swapchain via `set_format`. **`set_format`
(compositor/mod.rs:2786) replaces `self.swapchain` but does NOT reset the frame states or
`element_states`/`previous_element_states`** — so prior-generation render-target exports
escape. This is **not Leak B** (client imports are the separate `n=6` group) and matches the
heaptrack backtrace exactly.

**MATCHED reverse-ref (core `cc-core-matched`, PID 1248796, 36-min skew — orphans are
stable) CONFIRMS "forgotten clone": the strong ref is orphaned, not held.** Ran
`reverse-ref-scan.py` with the `…-093115` payload. Orphans
`2,114,115,116,118,125,126,127,129,136,137,138,140` read `strong=1 weak=2` with their
**only** base hit being the `dmabuf_cache` key; live `1`/`3` resolve a real second holder,
orphans do not. Reading the raw bytes of the base-hit array proved it is the
`HashMap<WeakDmabuf, GlesTexture>` (entries are `[base, GlesTexture*]` — `word1` is a heap
texture pointer, not a `clone_id`; HashMap control rows interleaved), i.e. a **weak** key,
not a strong holder. So `strong=1` with **no findable strong pointer anywhere** in the
fully-dumped core = the holding `Dmabuf` value's memory was **freed without its `Drop`
running** (the tracer's surviving registry entry independently proves `Drop` never ran).
This is a `mem::forget`-class orphaned strong count, born at the main-renderer
`render_frame`→`Slot::export()` apply_config path.

**Bottom line:** the leak is the **main renderer rendering the `DrmCompositor`s during
`apply_config` (`allow_frame_flags`, device.rs:965)** — its `render_frame`→`Slot::export()`
render-target clones are forgotten (`Drop` never runs). The *same* `render_frame` on the
surface-thread redraw path (group `n=4`) does **not** leak. **Open:** the exact line where
the export-clone's `Drop` is skipped (somewhere in `render_frame`'s export→bind→frame-result
flow on the `GlMultiRenderer`). Fix options: (a) smithay — find/fix the forget in that path
and/or have `set_format` release prior-generation primary slots; (b) cosmic-comp — stop the
one-shot main-renderer `render_frame` in `allow_frame_flags` from stranding render targets
across `apply_config`s.

**2026-06-20: escape-hatch #1 CLOSED — re-`gcore` WITH excluded mappings reveals
NO new strong owner. "Forgotten clone" confirmed; next step is the clone/drop
backtrace tracer (#2).** New full core (`/tmp/cc-core-full`, PID 1286836 — same
long-lived process, no restart; **335 PT_LOAD segs, `undumped=0`, filesz==memsz**
throughout) captured with `set dump-excluded-mappings on`. Re-ran
`reverse-ref-scan.py` against the prior `main ptrs` payload. For every confirmed
orphan (ids 2,4,51,52,54,61,62,63,65,72,73,75) the **only** base-pointer hit is the
renderer `dmabuf_cache` weak key (the contiguous `0x7fe028284c..` `Weak`-key array,
stride 0x10); the only other hits are **non-owning** data-ptr (`as_ptr`) entries in
the 24-byte-stride `DrmCompositor` damage history (`0x55e96be11…` and `0x7fe04c0…`).
**No strong `Arc`/`Weak` base hit appeared in any region the default dump skips.**
Expected: a Rust `Arc<DmabufInternal>` strong owner lives in normal heap/stack/static
memory (always dumped); `VM_DONTDUMP` is reserved for `madvise(MADV_DONTDUMP)` /
device mappings, where no Rust `Arc` can live. Escape hatch #1 is closed.

**CAVEAT — census/core time skew (does NOT change the conclusion).** The `main ptrs`
census is from `21:41:16` (snapshot `…-214116`); the new core was dumped the next
morning at `07:44` — **~10 h later**, same PID. Over that gap the swapchain churned, so
the live↔orphan boundary buffers shifted: **id 50 flipped orphan→owned** (its old
address was reallocated to a new live buffer — direct proof of address reuse) and **ids
72/75 flipped owned→orphan** (consistent with them leaking overnight). The "no owner
for the confirmed orphans" result is robust — address reuse can only *invent* spurious
owners, never hide a real one — but the live-buffer *validation anchor* (old scan found
`Box<Dmabuf>` owners for 1/72/75) is no longer clean. A fresh **matched**
census+`gcore` pair would re-validate the method, but is not required: the tracer (#2)
names the escape site directly and supersedes the reverse-ref approach.

**2026-06-20: both repos rebased onto latest masters before building the tracer —
upstream does NOT fix the leak path.** Confirmed the "fixed for free" hope is dead:
smithay's upstream advance (`de005503..5afb87fd`) touches only EI/`libei`,
`XWayland::spawn`, and chores — **zero** changes to `renderer/gles`, `drm/compositor`,
`allocator/dmabuf.rs`, or `swapchain.rs`. Rebased anyway for a clean base:
`vram-pbo-fix` onto smithay `origin/master` (5afb87fd, conflict-free) and `pr-2095`
onto cosmic-comp `origin/master` (296e1416 ≈ pop-os `aac1e19f`; the 12 leak-fix commits
replayed with **no conflicts** — file overlaps in `kms/mod.rs`/`state.rs`/`toplevel_info.rs`
didn't collide at line level; both our fixes and upstream's changes verified present).
The only migration was the new `extra_args` param on `XWayland::spawn`
(`src/xwayland.rs:118`, passed `std::iter::empty::<OsString>()`). Release build clean
(0 warnings) with all instrumentation markers intact. Backups:
`backup/{pr-2095,vram-pbo-fix}-2026-06-20` branches + `.backups/*-worktree-2026-06-20.tgz`.

**2026-06-20: the `Dmabuf` clone/drop backtrace tracer (#2) is BUILT — one capture
will name the escaped-clone site.** Implements the findings' decisive step #2:
- **smithay `dmabuf.rs`:** `Dmabuf` gains a per-*value* `clone_id` (`.1`); `Clone`/`Drop`
  are now manual (`PartialEq`/`Eq`/`Hash` still key only on `.0`, the shared `Arc`, so
  identity is unchanged). A process-global registry `clone_id -> (debug_id, Backtrace)`
  records where each live clone was made (`Clone`/`build()`/`WeakDmabuf::upgrade()`) and
  removes it on `Drop`. So a buffer with `strong_count == 1` (a `main ptrs` orphan) has
  **exactly one surviving entry — its backtrace is the escaped-clone site.** Exposed as
  `debug_dmabuf_clone_sites() -> Vec<(debug_id, backtrace)>`.
- **Gated for ~zero cost:** only active when env `COSMIC_DMABUF_TRACE` is set, and only
  for full-output-sized buffers (reusing the `created_at` gate), so the 60 fps primary-
  plane re-clone path is untouched in normal builds. Backtraces are `Arc`-snapshotted and
  symbolised *outside* the registry lock, so a census never stalls a render thread.
- **cosmic-comp census:** `renderer_cache_probe::group_clone_sites` dedups the live clones
  by reduced call site (std/runtime frames stripped, `file:line` kept) and the census emits
  one `dmabuf clone-site n=<k> ids=[…]` line per unique site (rarest first), each on its
  own log line so journald can't truncate it.
- **Build:** `target/release/cosmic-comp` rebuilt clean (0 warnings); markers
  `COSMIC_DMABUF_TRACE` + `clone-site n=` present. NOT yet installed.

**Capture procedure (decisive, one cycle).** ① The user adds `COSMIC_DMABUF_TRACE=1` to
`/etc/environment` (alongside the existing `COSMIC_GL_DEBUG*` vars). ② `sudo make install`
+ re-login. ③ Restart `/tmp/vram-watch.sh`; baseline `kill -USR1` census. ④ ~3 DP-2 power-
cycles (empty desktop), 10 s idle. ⑤ Final census + `leak-snapshot.sh`. **Read:** in the
census, cross-reference the orphan ids on the `main ptrs` line against the `dmabuf
clone-site … ids=[…]` lines — the (rare, usually `n=1`) site whose ids ARE the leaked
orphans is **the exact code path that made the clone whose `Drop` never runs = the fix
target.** (If no `dmabuf clone-site` lines appear, `COSMIC_DMABUF_TRACE` wasn't set in the
compositor's environment.)

**2026-06-19: the ENDGAME reverse-ref search is DONE — the leaked render-target
dmabufs have `strong=1` with NO owning reference anywhere in the process. They are
escaped/forgotten `Dmabuf` clones, not retained by any live container.** A core dump
(`gcore`, PID 1286836, snapshot `…-214116`, empty desktop after 3 DP-2 power-cycles)
plus the new `main ptrs` census line resolved the holder question the prior sessions
left open.

Tooling (now in [`vram-tools/reverse-ref-scan.py`](./vram-tools/reverse-ref-scan.py)):
the census prints `id@WxH=0x<dataptr>` where `dataptr = Arc::as_ptr` (the
`DmabufInternal` data ptr); `ArcInner` base = `dataptr-0x10`. A strong `Arc` **and** a
`Weak` both store the base; `Arc::into_raw`/`as_ptr` store the data ptr. The scanner
searches the core (parsing PT_LOAD) for both and reads the `ArcInner` strong/weak
counts at the base.

Results (16 cache entries; 5 live swapchain slots `[1,3,72,73,75]`, **11 leaked**):

- **Every dmabuf reads `strong=1, weak=2`** (id 44: `weak=3`). `weak=2` ⇒ exactly **one
  explicit `Weak`** = the main renderer's `dmabuf_cache` key; `strong=1` ⇒ exactly **one
  strong owner** to find.
- **Method validated on LIVE buffers:** ids 1/72/75's strong owner resolves to a clean
  `Box<Dmabuf>` (`0x21` malloc chunk = `[dmabuf_ptr, 0]`) in a swapchain slot's
  `UserDataMap` — the scan finds real owners.
- **The 11 leaked buffers have NO strong owner.** 6 (ids 51,54,61,62,63,65) have **only**
  the renderer's weak cache key — zero other base/data hits in all 1.6 GB. The other 5
  (2,4,50,52, and live 73) additionally appear as **`as_ptr` (data-ptr) entries** in a
  24-byte-stride cache at `0x7fe04c00…` — the `DrmCompositor` damage tracker's per-buffer
  history (non-owning keys; holds live id 73 too), **not** a strong holder. id 44 is the
  one live client window buffer (legit), not a leak.
- **Coverage is complete** for this core: every PT_LOAD has `filesz==memsz`
  (`undumped=0`); all leaked `DmabufInternal`s AND every live buffer's owner sit in the
  same fully-dumped 47.6 MB heap segment. The orphans' owner is genuinely absent.
- All 11 orphan `DmabufInternal`s are **valid live** (debug_id + size match), so these
  are real leaks, not stale memory.

**What this proves and rules OUT.** The strong owner is not a swapchain `Slot`, not the
frame pipeline (`current/pending/queued/next`), not the damage tracker, not the multigpu
cache, not a `GbmFramebuffer`, not a closure/channel — any of those would leave a findable
base pointer. `strong=1` with no base pointer anywhere (memory *or* thread registers — the
scan covers the NOTE segment too) is the signature of a **`Dmabuf` clone whose `Drop`
never runs** (a `mem::forget`-class escape). Grep confirms **no** explicit
`mem::forget`/`Arc::into_raw`/`ManuallyDrop`/`increment_strong_count` on `Dmabuf` in
cosmic-comp or the smithay fork — so the escape is via a generic/unsafe path, not a literal
call. **Corollary: pruning the main renderer's `dmabuf_cache` will NOT fix the byte leak** —
it would free the cached EGLImage but the GBM bo / 33 MiB stays pinned by the escaped clone's
fds. The fix must eliminate the escaped clone.

**Two cheap decisive next steps (no rebuild for #1):**
1. **Re-`gcore` with excluded mappings included** — rules out the only remaining escape
   hatch (a holder in a `VM_DONTDUMP` region gdb's `gcore` skips by default; unlikely for a
   Rust `Arc`, but closes it rigorously). Same still-running PID (orphans never move, so
   their addresses stay valid):
   ```
   P=$(pgrep -x cosmic-comp)
   sudo gdb -p "$P" -batch -ex 'set dump-excluded-mappings on' \
            -ex 'gcore /tmp/cc-core-full' -ex detach -ex quit
   sudo chmod a+r /tmp/cc-core-full.*
   ```
   Re-run `reverse-ref-scan.py` on the new core. A new base hit ⇒ the holder was in a
   skipped region (named). No new hit ⇒ truly forgotten, go to #2.
2. **`Dmabuf` clone/drop backtrace tracer** (smithay, needs rebuild) — give `Dmabuf` a
   manual `Clone`/`Drop` that registers a backtrace per clone in a global map keyed by
   debug_id and removes one per drop; the census dumps the unmatched (clone-without-drop)
   backtraces for each alive orphan id = the **exact escaped-clone site**. Definitive.

---

**2026-06-18 (evening): residual leak RE-CONFIRMED compositor-side and the
frame-state retainer candidate is REFUTED. Two new captures narrow the open
question.** The main-renderer full-screen render-target leak reproduces live on a
long-running build (PID 1810564, ~2 h): `nvidia-smi` 301 → **551 MiB**, dmabuf-fd
inventory 44/846 MiB → **62/1326 MiB** with **34 × 31.9 MiB (4K) fds**, all
`exp_name: drm` (compositor GBM buffers, not client wl-imports). Two discriminating
captures:

- **Snapshot `…-163148` (fullscreen app on DP-2):** 4K `count=1` orphans climbed
  2→20 across monitor power-cycles; the per-cycle adds came in groups of ~3 with
  sequential ids spanning *both* outputs (e.g. `394,395,398` 4K + `396` 1440p +
  swapchain `397,399`) — a synchronized reconfigure-render batch, not a client.
- **Snapshot `…-181525` (EMPTY desktop — only wallpaper/panel on DP-2):** 4K
  orphans still grew **23 → 32 (+9 over 3 power-cycles)**. This **rules out client
  window buffers** (the fullscreen app was a red herring) — on an empty desktop the
  only 4K content is the wallpaper composited into DP-2's swapchain, so these are
  the compositor's own primary-plane render targets. The journal showed mode-blob
  / `use_mode` reconfigure events with **zero** connector-disconnect / thread-
  termination and `live surface threads=2` throughout — confirming the persistent-
  surface `use_mode`/`set_format` trigger, not hotplug.

**Frame-state retainer candidate REFUTED.** The leaked 4K ids (`1,3,77,…,398`) are
**disjoint from every DP-2 swapchain generation** (`5,7 / 90,92 / 114,116 /
134,136 / 325,327 / 397,399`) **and** appear in **none** of the census's
`current/pending/queued/next` fields (which only ever show the current generation,
e.g. `current=136 pending=- queued=- next=-`). So the survivors were **acquired,
rendered during reconfigure, but never scanned out**, and are **not** held by the
DrmCompositor frame states that `set_format` leaves unreset (the prior leading
guess). Confirmed by reading the whole lifecycle: `submit_composited_frame`
(`output.rs`) consumes+drops its `RenderFrameResult`; `commit_frame` rolls
`current_frame` (bounded); `timings::Frame` holds no buffer; the DRM atomic `State`
holds a mode blob, not buffers; multigpu shadow caches are dormant single-GPU.
Every static path frees these slots, yet ≥32 persist at idle.

**The retainer is therefore in a NOT-YET-INSTRUMENTED holder** — candidates:
`DrmCompositor::element_states`/`previous_element_states`, per-overlay/cursor-plane
state, the DRM-commit/page-flip framebuffer tracking (a flip submitted while the
monitor is off may never complete its vblank, stranding its framebuffer), or
cosmic-comp frame-result handling. **Next step (decisive in one capture):** extend
the per-compositor census (`compositor[…]` line, built from
`DrmCompositor::debug_slot_ids_string`, `compositor/mod.rs`) to also dump the
primary-buffer dmabuf id + `Arc` strong-count held in `element_states`,
`previous_element_states`, and the cursor/overlay plane state — then cross-
reference the leaked main-cache ids. Alternatively run `heaptrack` over a
cycle session (runbook §4) to name the growing container by allocation backtrace.
Do NOT ship a `set_format` frame-state-reset fix — the census already shows the
survivors are not in the frame states, so that fix would be a partial/wrong patch.

**heaptrack (attach to live PID, ~3 power-cycles, 62 s, `ht-cosmic.zst`) pinned the
leak SOURCE.** `total memory leaked: 4.86M` is CPU-heap only (the 33 MiB GBM bos
are kernel-allocated, invisible to heaptrack) — but the NVIDIA driver's per-EGLImage
CPU bookkeeping IS tracked, and `--print-leaks` shows the leaked `create_image_from_dmabuf`
backtraces. **The dominant render-target leak:**

```
create_image_from_dmabuf ← GlesRenderer::import_dmabuf ← Bind<Dmabuf>::bind
  ← MultiRenderer::bind ← DrmCompositor::render_frame
  ← cosmic_comp::backend::kms::device::LockedDevice::allow_frame_flags
  ← KmsGuard::apply_config_for_outputs ← Config::read_outputs ← refresh_output_config
```

So the main renderer (`single_renderer`) binds each compositor's swapchain slot as a
render target inside **`allow_frame_flags`** (`device.rs:965` `render_frame`, iterating
`self.drm.compositors()`), once per `apply_config` — ~3 leaked EGLImages/cycle,
matching the 4K orphan rate. A **secondary** leak is client-buffer EGLImages imported
on the surface-thread redraw path (`import_dmabuf ← WaylandSurfaceRenderElement::from_surface
← render_elements_from_surface_tree ← SurfaceThreadState::redraw`) — the count-dominant
"Leak B". Ruled out by heaptrack: **no growing Vec/HashMap holds the slots** (the only
container leaks are DRM `planes()` `IndexMap`s and `RendererSurfaceState` damage
`VecDeque`s, both small/unrelated); `MultiRenderer::bind` is a pass-through on single-GPU
(no strong-`Dmabuf` caching). So the ~9 live slots are pinned by **individual `Arc<Slot>`
clones** whose holder heaptrack cannot reverse-trace.

**Where this leaves the retainer.** The EGLImage independently pins the GBM bo, but the
census shows the source `Dmabuf`s are strong-alive (`dead=0`) — so the bo is *also*
pinned by a live slot, and force-clearing only the main renderer's cache would free the
EGLImage pin but not the slot's. The leaked-EGLImage **count (~9) exceeds the bounded
frame-state holders (~2 `current_frame`s on an idle desktop)**, and the per-surface-thread
census only ever dumps the 2 *active* compositors — so the prime remaining suspect is
**stale `DrmCompositor`s lingering in the `DrmOutputManager.compositor` map** (which
`allow_frame_flags` iterates and re-renders every cycle), each pinning a full-screen
`current_frame` slot, invisible to the current census. **Decisive next instrument:** dump
`device.drm.compositors().len()` + each map entry's crtc and full per-field buffer ids
from the main-thread census (`cleanup_renderer_caches`, `kms/mod.rs:606`, which already
holds `api.devices_mut()`); if the map has >2 entries the stale-compositor accumulation
is the root and the fix is to evict map entries for crtcs without a live surface, before
`allow_frame_flags` re-renders them.

**Instrument BUILT (2026-06-18 evening).** `cleanup_renderer_caches` (`kms/mod.rs:606`)
now also dumps, per device, `compositor_map <node>: count=N {<crtc>: <swapchain/frame
slot ids>; …}` by `device.drm.lock().compositors()` (brief write lock, same as
`apply_config`; inner per-compositor `try_lock` so it never blocks a rendering surface
thread). Binary rebuilt at `target/release/cosmic-comp` (markers verified). **Next
capture:** `sudo make install` + relogin, restart sampler, baseline census, ~3 DP-2
power-cycles (empty desktop), 10 s idle, final census, `leak-snapshot.sh`. Read the new
`compositor_map` line vs. `renderer cache detail main`: leaked 4K ids appearing under a
`compositor_map` crtc name the retaining compositor (and `count`>active-outputs proves
stale-compositor accumulation); absent from all of them ⇒ the holder is the DRM
commit/page-flip framebuffer state, fix moves there.

**compositor_map RESULT (2026-06-18, fresh login PID 272438, snapshot `…-192624`):
stale-compositor hypothesis REFUTED; the orphans live outside ALL compositor state.**
`compositor_map count=2 {crtc 81: swapchain=[70,71,73,-] current=71; crtc 62:
swapchain=[1,4,53,-] current=1}` vs `main alive=14 {2560x1600×3 [1,4,53]; 3840x2160×11
[2,3,49,50,52,60,61,63, 70,71,73]}`. So `count=2` == active outputs (no stale
compositors); the 3 1440p ids + 4K ids 70,71,73 are the live swapchains; **the other 8
4K ids (2,3,49,50,52,60,61,63) are in NO compositor — not a swapchain slot, not
current/pending/queued/next.** Even at a fresh login (no user cycling) 8 orphans
accumulate from login's several `apply_config`s, +~3 per later power-cycle. Confirmed by
reading the rest of the path: `Slot::drop` only sets `acquired=false` (the `InternalSlot`
+ its userdata `Dmabuf` live on in the swapchain `slots[]` array, freed only when the
last `Arc` drops); `commit_frame` clears `pending`/`queued` and rolls `current_frame`;
`swapchain.submitted()` is age-only; `create_image_from_dmabuf` borrows (no clone). So
every code path *should* free these slots, yet 8 persist — the holder is an escaped
`Arc<Slot>`/`Arc<InternalSlot>` clone that static analysis, heaptrack (CPU-only, no
reverse-refs), and the census all cannot point to. **This is the genuine impasse.**

**heaptrack `--print-leaks` (snapshot trace) localised the SOURCE precisely.** Leaked
render-target EGLImages: `create_image_from_dmabuf ← import_dmabuf ← Bind<Dmabuf>::bind
← MultiRenderer::bind ← DrmCompositor::render_frame ← LockedDevice::allow_frame_flags
← apply_config_for_outputs` (~9 leaked ≈ 3/cycle). Secondary client-import EGLImage leak
via `WaylandSurfaceRenderElement::from_surface ← render_elements_from_surface_tree ←
SurfaceThreadState::redraw` (the count-dominant Leak B). heaptrack confirmed **no growing
container** holds the slots (only DRM `planes()` `IndexMap`s + `RendererSurfaceState`
damage `VecDeque`s, small/unrelated), and `MultiRenderer::bind` is a single-GPU
pass-through.

**Slot-lifecycle instrument BUILT (2026-06-18 evening) to name the holder's render path.**
smithay `swapchain.rs`: a process-global `LIVE_SLOTS` registry — each `InternalSlot`
registers (size + acquire-path backtrace signature) when a buffer is allocated in
`acquire`, links its exported dmabuf id in `Slot::export`, and deregisters in a new
`impl Drop for InternalSlot`. Exposed as `debug_live_slots()` (per-size live counts + ids)
and `debug_live_slot_sites()` (live slots grouped by acquire-path signature), re-exported
from `allocator/mod.rs`; cosmic-comp's census (`cleanup_renderer_caches`, `kms/mod.rs`)
records both as `live_slots`/`live_slot_sites` detail lines. Binary rebuilt at
`target/release/cosmic-comp` (markers verified). **Next capture:** `sudo make install` +
relogin, baseline census, ~3 DP-2 power-cycles, 10 s idle, final census,
`leak-snapshot.sh`. Read `live_slots` (total live-slot count + ids — should match the
`main` orphans and prove they're never dropped) and `live_slot_sites` (the acquire path
that stranded each). The acquire signature for the orphan ids names the exact render to
fix.

**slot-instrument RESULT (2026-06-18, fresh login PID 1535549, snapshot `…-205103`):
the stranded-slot theory is REFUTED — slots are freed; the leak is one level down at the
`Dmabuf`.** `live_slots total=5` (≈ the two live swapchains; `live_slot_sites` = 3×
`render_frame ← allow_frame_flags` + 2× `find_supported_format ← DrmCompositor::new`),
but `main alive=15` (11×4K + 3×1440p + cursor). dmabuf-fd inventory: **13×31.9 MiB (4K)
held = ~415 MiB, but only 3 are live slots** ⇒ ~10 leaked 4K dmabufs (~320 MiB) at a
*fresh login*, before any cycling. So the `InternalSlot`s are dropped correctly; the
**exported `Dmabuf`s outlive their slots** — the retainer holds a `Dmabuf` clone (which
keeps the fd + ~32 MiB alive), not an `Arc<Slot>`. This is why every slot-level path
looked bounded, and it kills the long-running "stranded render-target slot" theory. Also
ruled out as holder: `GbmFramebuffer` holds only a DRM fb handle (no `Dmabuf`); per-clone
`Dmabuf` tracking would be a 60 fps firehose (surface thread re-exports the primary plane
each frame).

**`Dmabuf` creation-tag probe BUILT (2026-06-18 evening).** smithay `dmabuf.rs`: each
`Dmabuf` ≥1280×720 records a compact creation-path signature in `build()`
(`dmabuf_creation_signature()`), exposed via `Dmabuf::debug_created_at()`;
`debug_dmabuf_cache_report` now returns it per alive entry, and cosmic-comp's census emits
a new `main origins <node>` line grouping the live main-cache dmabufs by creation path
(`format_cache_origins`). One capture then says **what the ~10 orphans are**: a
swapchain/`export()` render-target origin ⇒ a `DrmCompositor` clone escaping the frame
pipeline; a client-buffer-import origin ⇒ Leak B (per-surface `MultiTextureInternal`
retention, no destruction hook). Binary rebuilt at `target/release/cosmic-comp` (markers
verified). **Next capture:** `make install` + relogin, baseline census, ~3 DP-2
power-cycles, 10 s idle, final census, `leak-snapshot.sh`; read `main origins` against
`renderer cache detail main`.

**creation-tag RESULT (2026-06-18, fresh login PID 1712134, snapshot `…-214423`):
ALL leaked dmabufs are compositor render-target `export()` clones — ZERO client imports.**
`main origins`: `9× [3,4,43,44,46,54,56,64,66] build ← AsDmabuf::export ←
DrmCompositor::render_frame ← allow_frame_flags ← apply_config` and `5× [1,2,42,53,63]
build ← AsDmabuf::export ← DrmCompositor::find_supported_format ← DrmCompositor::new ←
initialize_output` and `1× [15] <small>` (cursor). So it is **definitively NOT Leak B**
(no client/`MultiTextureInternal` origin); both origins are swapchain-slot exports (the
format-probe slots simply *become* the compositor's swapchain via `test_format`, then get
imported into the main renderer on a later `render_frame`). Reading `render_frame`
(2188-2352) and `test_format` (1471+) confirms the `export()` return is a transient local,
dropped after the bind — never stored. Combined with: slot userdata clone drops with the
slot (slots freed, `live_slots`=5), `import_dmabuf` keys *weakly*, `GbmFramebuffer` holds
no dmabuf, multigpu strong-dmabuf cache is single-entry, EGL import borrows — **every
source-visible clone is transient/bounded, yet one survives (`sc=1`, 33 held fds).** The
escaped-clone holder is invisible to static analysis, heaptrack (CPU/no reverse-refs), and
all census/registry instruments. This is a reverse-reference question.

**ENDGAME (the holder needs reverse-ref tooling, not more backtraces).** Options:
(1) **GDB pointer-search** — add a census line printing `Arc::as_ptr` of a leaked main-cache
`Dmabuf` (the `DmabufInternal` address), then on the live compositor `gdb -p`, `find` that
address across mapped memory → the struct that points to it = the holder. A debug build
(symbols + stable layout) makes identifying the containing struct feasible. Definitive but
involved in the two-actor setup. (2) **Upstream smithay report** — the leak is entirely
within smithay's `DrmCompositor` render-target export/import path; file with this precise
localization. (3) **Pragmatic mitigation** — none clean: the GPU buffer is pinned by the
escaped `Dmabuf`'s fds, so force-clearing the main renderer's EGLImage cache would not free
it; the fix must release the escaped clone, which requires naming it first.

---

**2026-06-18: LEAK A (surface-thread stranding on connector disconnect) IS FIXED.
A separate, client-independent full-screen render-target leak in the long-lived
main renderer's GLES `dmabuf_cache` remains — and it is NOT downstream of Leak A
(the 2026-06-16 "likely downstream" guess is superseded).**

Validation loop on the new build (PID 2948247), three snapshots:

| signal | login baseline (13:08, `toplevels=1`) | after 3–4 cycles (13:40, `toplevels=3`) | Warp-only after cycles (13:56, `toplevels=1`) |
|---|---|---|---|
| `live surface threads` | 2 (== outputs) | **2** | **2** |
| 4K `count=1` orphans (3840×2176) | 0 | 8 | **9** |
| 1440p `count=1` orphans | 0 | 1 | 1 |
| `main` dmabuf_cache | 4 | 14 | **14** |
| `surface[HDMI-A-1]` / `surface[DP-2]` | 9 / 5 | 10 / 8 | 10 / 5 |
| `egl_images` live (created/destroyed) | 76 | 392 (698/306) | **453 (810/357)** |
| cosmic-comp nvidia-smi MiB | 71 (→204 settled) | 366 | 338 |

**Leak A fixed — proof.** `live surface threads` (RAII `SurfaceThreadHandle`,
`renderer_cache_probe.rs:38`, created at `surface/mod.rs:508` as a sibling stack
local to `state`/`state.compositor`) stays at 2 == connected outputs across every
monitor off→on cycle. A count of 2 is a hard guarantee that no disconnected
output's surface thread — and therefore no old `compositor`/swapchain — is
stranded. The `drop_and_join()` at `device.rs:337` (connector disconnect) works.

**Residual leak — confirmed independent of Leak A.** Closing every client but
Warp did NOT free the orphans (4K `count=1` orphans 8→9, `egl_images` 392→453,
`main` cache flat at 14) — so they are the compositor's *own* full-screen render
targets (exactly 3840×2176×4 = 33423360 B and 2560×1664×4 = 17039360 B), not
client buffers. It lives in the **main** renderer specifically: across the session
`surface[*]` caches stayed ~flat while `main` grew 4→14. The main `GlesRenderer`
is never recreated and test-imports each output's swapchain slot as a render-target
texture during `apply_config_for_outputs` (`kms/mod.rs:1015` `initialize_output`,
`:1125` `use_mode`, `:1171` `try_to_restore_modifiers` → `render_frame` →
`renderer.bind(slot dmabuf)` → `import_dmabuf` insert at smithay `gles/mod.rs:1282`).

**Why eviction never fires.** The only drain is the 2 s-throttled
`cleanup_renderer_caches` (`kms/mod.rs:606`) → smithay `cleanup()` →
`dmabuf_cache.retain(|k,_| !k.is_gone())` (`gles/mod.rs:791`); `is_gone()` ==
source `Dmabuf` `strong_count==0`. The cached `GlesTexture` value holds the
`EGLImage` (pins the kernel dmabuf at refcount 1) but does NOT hold a strong
`Dmabuf`, so the cache is not self-pinning. The cache held steady at 14 for ~16 min
(~480 cleanup passes) without shrinking ⇒ the entries are NOT dead-keys-awaiting-
retain; their source `Dmabuf`s are genuinely **alive**, held by main-thread /
shared `DrmOutputManager` state (live compositors' swapchain `Arc<Slot>`s + the
`current_frame`/`pending_frame`/`queued_frame` slots that `set_format` does not
clear, `compositor/mod.rs:2774`).

**Refuted hypothesis (do not re-pursue without new evidence):** a subagent trace
concluded the survivor is a stranded thread's `SurfaceThreadState.compositor` via
the non-joining drop at `kms/mod.rs:899`. The `live surface threads=2` counter
refutes this — no thread is stranded. The survivor is in live shared compositor
state, not a surface thread.

**UNBOUNDED — confirmed (2026-06-18, 6-cycle run, snapshot `…-142257`).** Census
trajectory `main dmabuf_cache` 4 → 14 → **24 → 33 → 41** across cycles (~4–5
entries ≈ one swapchain per cycle); 4K `count=1` orphans 0 → 9 → **26**;
cosmic-comp 71 → **594 MiB**. (The 41/26 figures are mid-cycling, no final settle
census; the firm *settled* evidence is 13:56 idle: cache **14**, **9** live 4K
slots — and a live swapchain has only `SLOT_CAP=4`, so ≥2 OLD swapchain
generations are Rust-strong-alive at idle. Definitively not bounded high-water.)

**TRIGGER CORRECTION — it is NOT the disconnect path.** The journal shows the
monitor off→on cycles fire `Failed to destroy old mode property blob` / Xwayland
mode messages with ZERO connector-disconnect or "Thread … terminated" events and
`outputs=2` constant. So the cycles are **mode/config re-applies on persistent,
never-torn-down surfaces** (`apply_config_for_outputs` → `use_mode` branch,
`kms/mod.rs:1125`), not disconnects. The in-tree `drop_and_join()` disconnect fix
(`device.rs:337`) **never runs in this scenario** — it is correct for physical
unplug but does not address the user's actual trigger.

**Mechanism (confirmed by code).** On every `apply_config`, `kms/mod.rs:1169`
calls smithay `DrmOutputManager::try_to_restore_modifiers` (`output.rs:541`).
When a compositor is on implicit modifiers (`[DrmModifier::Invalid]` — common on
nvidia) it calls `compositor.set_format(...)` → recreates `self.swapchain`
(`compositor/mod.rs:2791`) and re-renders via the **main** renderer
(`submit_composited_frame`), importing the new swapchain slots into the main
`dmabuf_cache`. `use_mode`→`swapchain.resize` is a no-op at unchanged resolution
(`swapchain.rs:224`), so `set_format` is the per-cycle churn. Each new generation's
slots are imported into the long-lived main renderer; the OLD generations' slots
stay Rust-strong-alive (so `is_gone()` never fires and the 2 s cleanup can't
reclaim them), accumulating ~4–5/cycle.

**Open question (only one left): the exact unbounded retainer.** `set_format`
replaces `self.swapchain` but does not clear the frame states; frame-state
retention (`current_frame`/`pending_frame`/`queued_frame`/`next_frame`) is bounded
~3, yet ≥9 old 4K slots survive at idle — so an additional, accumulating holder of
old `Arc<Slot>`/`Dmabuf` exists inside the live `DrmCompositor` (candidate:
per-overlay/cursor-plane state not reset by `set_format`, or a frame/feedback
container) or in cosmic-comp's frame-result handling. Resolve by trace + targeted
instrumentation (main-cache `is_gone`/alive split + cached-texture sizes +
per-plane retained-slot count) before the fix.

**Candidate fixes (after the retainer is pinned):** (a) root — ensure `set_format`/
the reconfigure path releases prior-generation swapchain slots (all planes); or
(b) bounded — since the main renderer only draws one-shot during `apply_config`,
actively prune its render-target imports (or its whole `dmabuf_cache`) at the end
of `apply_config_for_outputs` rather than relying on passive `is_gone()` eviction.

**Still open — output-disable path (`kms/mod.rs:899`)**: the non-joining
`surfaces.retain(...)` inside `apply_config_for_outputs` runs under the write lock
where join/suspend deadlock; same teardown bug as the (now-fixed) disconnect path
but a rarer trigger. Independent of the render-target cache leak above.

---

**[SUPERSEDED 2026-06-18 — Leak A is now fixed (see above); the dmabuf orphan count
on the older build was inflated by Leak A's thread stranding, but the residual
render-target leak persists on the main renderer independently.]**

**2026-06-16 (night): LEAK A IS NOT FIXED — the "confirmed fixed" reading was a
false negative, and the root cause + a fix are now in-tree.** The new-build idle
snapshot `leak-snapshot-20260616-221044` (PID 1715928, 691 MiB, only Warp open,
`toplevels=1`) still holds the full-screen render-target signature, *worse* than
the pre-rework build:

| full-screen buffer | old build (727 MiB) | new build (691 MiB) | healthy |
|---|---|---|---|
| 33.4 MiB (4K 3840×2176 render target) | 21 | **26** | ~3–4/output |
| 16.25 MiB (1440p 2560×1664 render target) | 10 | **8** | ~3–4/output |

`renderbuffers=0 framebuffers=0` looked clean only because the upstream
`Bind`-as-textures rework moved render targets out of the counted `self.buffers`
Vec into dmabuf-backed textures the renderbuffer counter never sees. The dmabuf
fd inventory — which measures the actual pinned buffers — is unchanged. **The
proxy counter was wrong; the buffers still leak.**

**Decisive new evidence — the dmabuf kernel refcount splits the 4K buffers in
two:** **24 of 26 have `count=1` (single owner) and only 4 have `count=4`** (the
live swapchain, referenced by GBM+scanout+framebuffer+renderer). So ~24 of the
4K buffers are *single-owner orphans*, not active swapchain slots — leaked
full-screen offscreen/render-target buffers, ≈ the dominant residual.

**Root cause (confirmed by code reading + two subagent traces): the non-joining
`Surface::drop` strands surface-render threads on connector disconnect /
reconfigure.** Each output renders on its own thread whose `SurfaceThreadState`
owns a full `GpuManager` renderer (EGL ctx + dmabuf_cache), the `GbmDrmOutput`
compositor handle (its ≤4-buffer swapchain), and full-screen `postprocess_textures`.
`Surface::drop` (`kms/surface/mod.rs:476`) only *signals* `ThreadCommand::End`
and detaches the thread without joining (the join is commented out: "deadlocks on
`apply_config_for_outputs`"). A detached thread that is mid-`redraw()` blocks on
the DRM-compositor **read** lock; if a reconfigure has taken the **write** lock
(`KmsGuard` from `lock_devices()`, held across all of `apply_config_for_outputs`),
the thread never returns to its loop, never sees `End`, and **leaks its entire
GPU state** — exactly the ~24 single-owner full-screen buffers + the `main`
renderer's `dmabuf_cache` climbing 14→35 (it test-imports every output's swapchain
during `apply_config`, so it tracks live swapchain buffers). The two paths that
already join (`device.rs:412` reopen, `:615` device_removed) don't leak; the two
non-joining paths do: connector disconnect (`device.rs:328`) and output-disable
(`kms/mod.rs:899`).

**Fix applied (cosmic-comp, this branch, uncommitted):**
- `device.rs:328` (connector disconnect — the user's monitor-off-at-the-switch
  trigger) now `surfaces.remove(&crtc).unwrap().drop_and_join()`, joining the
  thread synchronously so its renderer+swapchain+postprocess free *now*, before
  any reconfigure can grab the write lock and strand it. Safe because this path
  holds no compositor lock (same as the working `:412`/`:615` sites).
- **Census instrumentation:** `renderer_cache_probe::SurfaceThreadHandle` (an RAII
  counter held on each surface thread's stack) + a new census line `live surface
  threads=N`. A stranded thread never drops its handle, so `N` > connected-output
  count is the direct, unambiguous leak signal the per-label cache probe couldn't
  show (stranded thread + replacement share an output label).
- **Still open — output-disable path (`kms/mod.rs:899`)** runs *inside*
  `apply_config_for_outputs` under the `KmsGuard` write lock, where both `join()`
  and `suspend()` deadlock. The complete fix is to drain/teardown removed+disabled
  surfaces *before* `lock_devices()` (so `drop_and_join` is safe everywhere); left
  for a follow-up as it's a rarer trigger than physical connect/disconnect.

**Leak B (orphaned EGLImages) — still open, likely downstream of Leak A.**
`egl_images=1874` live (created 3394 / destroyed 1520) with only ~50 in any
`dmabuf_cache` and ~50 open fds → ~1824 EGLImages alive with their source dmabuf
fd already closed. Held by per-surface `MultiTextureInternal` in the wl_surface
`data_map`, which (unlike `RendererSurfaceState`, `utils/wayland.rs:393`) has **no
destruction hook**, so it is freed only when the last `WlSurface` ref drops. Two
non-exclusive mechanisms: (a) cosmic-comp retains destroyed surfaces' `WlSurface`/
`Window` refs; (b) **each stranded surface-thread renderer is a fresh
`ErasedContextId`, and `MultiTextureInternal.textures` clears only on size/format
change — so every leaked thread strands one stale `GlesTexture`(→EGLImage) per
live surface**, which would make Leak B partly a *consequence* of Leak A. A clean
fix needs a coordinated smithay change (expose a "clear cached multigpu textures
for surface" + register a destruction hook — note `add_destruction_hook<D>`
requires the compositor state type `D`, so it cannot live in the generic
`MultiRenderer`). **Deferred pending the next capture**, which the `live surface
threads` counter now lets us read directly: if fixing Leak A drops the thread
count to the output count and `egl_images` falls with it, Leak B was downstream;
if `egl_images` stays high with a correct thread count, hunt the cosmic-comp
`WlSurface` retainer.

**Build to install:** `target/release/cosmic-comp` (rebuilt with the fix +
counter; markers present). **Next capture:** `sudo make install` + re-login,
restart the sampler, do **3–4 monitor off→on cycles** then normal use, fire a
census, and `sh /tmp/leak-snapshot.sh`. **Pass = `live surface threads` equals the
connected-output count (not climbing per cycle), the 33.4/16.25 MiB dmabuf counts
stay at ~3–4/output, and the idle floor returns near the login baseline.**

> Superseded status (the "fixed" reading) follows.

**2026-06-16 (later): advanced both repos to latest — and latest smithay
REMOVES Leak A's data structure.** Rebased the smithay fork `vram-pbo-fix` onto
its synced `origin/master` (`de005503`, = upstream Smithay latest) and cosmic-comp
`pr-2095` onto the fork's `origin/master` (`e86df7b2`, synced with pop-os
`db0b1afe`). Backups: `backup/vram-pbo-fix-2026-06-16`, `backup/pr-2095-2026-06-16`.

- **Leak A is structurally gone upstream.** Smithay commit `fa509c7b`
  ("renderer/gles: Bind Dmabufs as textures instead of renderbuffers", −131 lines)
  **deletes `GlesBufferInner` and the `GlesRenderer.buffers` Vec** — the exact
  render-target struct that leaked (renderbuffers/framebuffers 6→32). `Bind<Dmabuf>`
  now does `import_dmabuf` → caches the result in `dmabuf_cache` as a texture,
  pruned by the same `retain(!is_gone())`. So the byte-dominant full-screen
  render-target leak should be fixed; `renderbuffers`/`framebuffers`/`buffers`
  counters will now read ~0. **Leak B (client-import EGLImage retention in
  `dmabuf_cache`) is the open question to re-confirm on the new code.**
- **Migration cost was tiny:** the only cosmic-comp breakage across the smithay
  jump was the winit 0.31 `pump_events` import (`winit::platform::pump_events`
  → `winit::event_loop::pump_events`, `src/backend/winit.rs`) and the
  `X11Surface::is_popup`→`is_modal` rename (pure alias; `src/shell/layout/mod.rs`).
  Build green, zero warnings.
- **Instrumentation adapted to the rework:** the fork accessor is now
  `GlesRenderer::debug_dmabuf_cache_len() -> usize` (+ `GlowRenderer`
  passthrough) — `self.buffers` no longer exists. cosmic-comp's
  `renderer_cache_probe` now stores a single `usize`; the census prints
  `renderer caches <label>: dmabuf_cache=N`. The smithay PBO/damage/counter
  commits replayed (one conflict in `gles/mod.rs`, resolved by taking upstream's
  texture-based `Bind<Dmabuf>`).
- **Build to install:** `target/release/cosmic-comp` (markers verified present).
  **Next capture:** install, re-run the monitor off/on test + normal use. Expect
  `renderbuffers`/`framebuffers` to stay flat (Leak A fixed); watch `egl_images`
  and the per-renderer `dmabuf_cache=` line for whether Leak B survives.

**2026-06-16 (later still): LEAK A CONFIRMED FIXED on the new build.** Installed +
re-login (new PID 1715928) + **3 monitor off-at-switch cycles**, census fired:
`GL live objects: textures=48 egl_images=135 renderbuffers=0 framebuffers=0
buffers=6`, raw `renderbuffers_created=0 renderbuffers_destroyed=0`. The off/on
cycles that on the old build (PID 104096) stranded +3–4 renderbuffers each (6→32,
~700 MiB) now strand **zero** — the upstream `Bind`-as-textures rework removed the
allocation entirely. (`framebuffers_created` runs to ~77k but `created==destroyed`,
live 0–1: a transient per-frame FBO, not a leak.) The per-renderer probe works and
gives the topology for Leak B: `main dmabuf_cache=14`, `surface[DP-2]=5→8`,
`surface[HDMI-A-1]=10` — small/bounded so far. **Leak B still open**: `egl_images`
135 and ramping during login (not the old 2932); note 135 live EGLImages vs 32
total across the dmabuf_caches — the held-outside-cache gap to watch. Decide with
1–2 h normal use → census + per-renderer `dmabuf_cache=` trend.

## Fix validation (2026-06-16)

Does each leak fix on this branch actually hold? The census was instrumented to
track exactly the containers the fixes target, and the **23 h leak session ran on
a build that already contained every committed fix** (PID 104096,
`leak-snapshot-20260616-095410`) — so its 26 hourly censuses are a real test.
Every fix's target container stayed clean **under load**:

| Fix | Target container | Telemetry across 26 censuses | Verdict |
|---|---|---|---|
| Weak refs in `ToplevelHandleState`/`ImageCaptureSourceKind`, capture-session cleanup (`e264f8fb`,`99b63c3e`,`6cbef259`,`bfe64193`,`b06e7b91`) | `sessions`, `cursor_sessions` | `sessions=0 cursor_sessions=0` all 26 (output/ws/surface) | **Works** |
| Eager `ImageCopyCaptureState::cleanup()` + free renderbuffers (`e8227133`) | `offscreen_renderbuffers` | `offscreen=0` all 26 | **Works** |
| Capture/activation/postprocess leaks (`ae991c50`) | `pending_activations`, postprocess | `pending_activations=0` all 26 | **Works** |
| Dead minimized windows / cursor cache (`e1623cc8`) | `minimized_windows` | `=0` (one transient `1`) | **Works** |
| Reference cycle Output→ZoomState→IcedElement (`916d9bca`) | `output_zoom_states`, `iced_elements` | `zoom=0` all 26; `iced` 3–11 tracks toplevels, collapses on close | **Works** — no orphaned IcedElements |
| Main-thread cleanup-queue drain (`afa9d11c`) | GL cleanup queue depth | depth `0` all 26; `queued_texture=24487 == drained` (heavily exercised) | **Works at its goal** |
| smithay free PBO on ExportMem error (`b557e4c8`) | PBOs / mappings | `buffers` created==destroyed (bar 6 VBOs); `queued_mapping=403 == drained` | **Works** — no PBO leak |
| smithay clamp shm damage (`661a6ca6`) | cursor `GL_INVALID_VALUE` (correctness) | — | Correctness fix, not a leak |
| remove disconnected client from all DRM devices (`4d9bda6d`) | per-client resources, multi-GPU | single-GPU 2-output here — not directly observable | Can't validate on this HW |

**These are real bugs and the fixes hold** — every guarded container stays
zero/bounded even under heavy traffic (24 487 textures cycled the drain queue and
all freed; 403 PBO readbacks all freed). They protect capture-heavy / overview /
zoom / minimize workloads.

**But none was the *dominant* leak.** The 727 MiB leaked at ~28 MiB/h *through*
all of them (unchanged rate). The dominant leak lived in two places no fix above
— and none of the original census counters — could see, which is why the new GL
object counters were needed:

- **Leak A (~700 MiB):** smithay render-target renderbuffers (`self.buffers`).
  **Fixed by the upstream `Bind`-as-textures rework** pulled in 2026-06-16
  (renderbuffers 32→0), *not* by our changes.
- **Leak B:** smithay `dmabuf_cache` EGLImages (46→2932). Still under test on the
  new build.

**2026-06-16: ~23 h counter session captured the leak in flight; localised to
pinned client-dmabuf EGLImages.** Sampler ran 06-15 10:41 (t0, 79 MiB) → 06-16
09:34 (**727 MiB**), +648 MiB over 22.9 h ≈ **28 MiB/h** (same rate as 06-08/06-12).
Two confirmed facts from data the agent can read (`/tmp/vram.csv` + the t0 census
in `leak-snapshot-20260615-104200/journal.txt`):

1. **The leak is activity-driven, not wall-clock-driven.** The overnight stretch
   (06-15 21:00 → 06-16 06:00) sat *dead-flat at 824 MiB for ~9 h* — identical
   sample after sample. Every climb in the curve lines up with active-use bursts
   (window open/resize/close); idle (likely DPMS-off, no compositing) leaks
   nothing. Closing all apps this morning walked it only 824→727, leaving ~650
   MiB stuck vs the 79 MiB / 3-toplevel login floor. Return-to-idle leak
   reproduces, larger than ever.

2. **The leaking class is EGLImages from client dmabuf imports.** t0 census:
   `egl_images_created=47, egl_images_destroyed=1` (and `queued_egl_image=1`) —
   EGLImages are created on every client dmabuf import but essentially never
   destroyed, while textures churn healthily (`created=73 destroyed=51`, net live
   flat) and renderbuffers/framebuffers/buffers are fixed at 6. A leaked EGLImage
   pins the underlying GPU buffer (EGL refs the buffer independently of the
   client's dmabuf fds), charged to cosmic-comp's nvidia-smi row → exactly the
   signature. Per-unique-buffer accumulation (new buffer per resize/open, none on
   idle) explains the activity-driven shape.

**Code localisation (smithay fork `vram-pbo-fix`):** the strong holder is
`GlesRenderer.dmabuf_cache: HashMap<WeakDmabuf, GlesTexture>` (`gles/mod.rs:417`,
insert `:1319`). It stores a **strong** `GlesTexture` (→ owns the `EGLImage`,
`texture.rs:133`) keyed by a **weak** dmabuf. It is pruned only in `cleanup()`
(`gles/mod.rs:838-841`: `retain(!is_gone())` then queue-drain), reached via
`cleanup_texture_cache()` (`:2367`) — called per-frame on surface threads and
every 2 s on the main-thread renderers (`KmsState::cleanup_renderer_caches`,
`kms/mod.rs:606`, gated on `session.is_active()`). The `GlesTextureInternal::Drop`
that queues the EGLImage for `DestroyImageKHR` fires only when the texture's last
`Arc` drops.

**727-MiB census captured (`leak-snapshot-20260616-095410`) — TWO distinct
leaks, both monotonic, both pause overnight (confirming activity-driven):**

| GL live object | t0 (79 MiB) | now (727 MiB) | raw created/destroyed now |
|---|---|---|---|
| textures | 22 | 50 | 24537 / 24487 (clean churn, not leaking) |
| **egl_images** | 46 | **2932** | 5366 / 2434 |
| **renderbuffers** | 6 | **32** | 451 / 419 |
| **framebuffers** | 6 | **32** | 451 / 419 |
| buffers (PBO+VBO) | 6 | 6 | 425 / 419 (clean; PBO-free-on-error works) |

All cleanup-queue depths are **0** and `queued==drained` for every class, so this
is **not** an undrained queue — it is **Rust-side strong-ref retention** (the
leaked wrappers never reach `Drop`/the destruction queue). Topology: single
NVIDIA GPU (595.71.05), 2 outputs `HDMI-A-1` + `DP-2`; **no** capture sessions
(`sessions=0`, `offscreen=0`) and **no** zoom at all 26 censuses → rules out
multi-GPU copy and live screencast.

**Leak A — render-target dmabufs (BYTE-DOMINANT, ≈ the 727 MiB).** Open
dmabuf-fdinfo: 49 fds / **988.9 MiB**, dominated by **23 × 33,423,360 B
(3840×2176×4, the 4K output) + 13 × 17,039,360 B (2560×1664×4, the 1440p
output)**. `renderbuffers`+`framebuffers` are incremented *only* by the
`Bind<Dmabuf>` render-target path (`gles/mod.rs:1570-1639`, rbo+fbo+EGLImage
cached in `GlesRenderer.buffers`, pruned by `self.buffers.retain(!is_gone())`).
So ~26 full-screen render-target dmabufs are pinned because their backing strong
`Dmabuf` is retained. Correlation: the **+8 morning renderbuffer leaks happened
with no PBO readbacks** and match the **8× "Failed to destroy old mode property
blob"** modeset warnings — i.e. modeset / swapchain turnover strands a full-screen
render target each time. (The earlier hours also leaked render targets that *did*
ride PBO readbacks — `renderbuffers_created` tracked `queued_mapping` ~1:1 for the
first ~403 ops, then readbacks stopped; that early burst's source is unconfirmed —
`sessions=0` throughout, so not a live image-copy session.)

**Leak B — client-import EGLImages (COUNT-DOMINANT, byte-small).** `egl_images`
46→2932; `egl_created` grows ~600/h largely independent of readbacks → residual
of *normal compositing* client-buffer imports. Decisive: only **49 open dmabuf
fds** back **2932** live EGLImages, so **~2880 EGLImages outlived their `Dmabuf`
(`is_gone()`==true) yet were never pruned** → a renderer's `dmabuf_cache`
accumulating dead-keyed entries without running `retain`, **or** a second strong
`GlesTexture` ref (candidate: per-surface `MultiTextureInternal` in the wl_surface
`data_map`, `multigpu/mod.rs:1696-1726`, cleared only on size/format change).
These are mostly small surfaces (closing apps dropped VRAM 824→727 while
`egl_live` *rose* 2494→2932), so Leak B is the lesser VRAM contributor but the
larger object-count leak.

**Leak A trigger confirmed by the user (2026-06-16):** the only display change
all session was **turning the monitors off at the physical switch** overnight and
"a couple of times during the day" — i.e. connector disconnect/reconnect (true
hotplug, not DPMS), matching the **8 modeset events** (≈4 off→on cycles × 2). So
Leak A = **each monitor off→on cycle strands that output's full-screen swapchain
render targets.** This is a *fast* repro: toggle monitors, no day-long wait.

**Per-renderer cache-size instrumentation added (2026-06-16) — built, ready to
install.** The process-global counters can't say *which* `GlesRenderer` holds the
leak, and the surface renderers live on per-output threads the SIGUSR1 census
(main thread) can't reach. Added:

- **smithay fork** (`vram-pbo-fix`): `GlesRenderer::debug_cache_sizes() ->
  (dmabuf_cache.len(), buffers.len())` (`gles/mod.rs`, just after `cleanup()`) +
  a `GlowRenderer` passthrough (`glow.rs`). Upstreamable; discrete from the leak
  fix to come.
- **cosmic-comp**: `src/utils/renderer_cache_probe.rs` — a process-global
  `Mutex<BTreeMap<label,(dmabuf_cache,buffers)>>` each render thread writes after
  its cleanup (`surface[<output>] <node>` from the surface threads at
  `kms/surface/mod.rs`'s `cleanup_texture_cache` loop; `main <node>` from
  `KmsState::cleanup_renderer_caches`, `kms/mod.rs`). The census
  (`vram_dump.rs::dump_renderer_cache_sizes`) prints one
  `renderer caches <label>: dmabuf_cache=… buffers=…` line per renderer.

**How it localises:** a `dmabuf_cache` that climbs to ~hundreds/thousands on one
label names the renderer holding Leak B (then ask why its `retain` doesn't prune /
who holds a second `GlesTexture` ref); a `buffers` that climbs past swapchain
depth names Leak A's renderer. If *no* label shows the counts, the objects are
held outside the renderer caches (a cosmic-comp second strong ref) — points the
fix at cosmic-comp, not the fork.

**Build to install:** `target/release/cosmic-comp` (this instrumentation +
everything prior). `sudo make install` + re-login (per the pre-flight below).

**Next capture (2026-06-16):**
1. Install + re-login; restart `/tmp/vram-watch.sh`; `kill -USR1` a baseline census.
2. **Leak A (fast):** turn both monitors off at the switch, wait ~5 s, back on;
   repeat 3-4×. Fire a census. Expect one renderer's `buffers` to step up per
   cycle and not recover.
3. **Leak B (≈1-2 h):** normal use (the leak shows fast — `egl_live` hit 415 in
   hour 1 last time). Fire a census.
4. `sh /tmp/leak-snapshot.sh`; hand off the snapshot path. The agent diffs the
   per-renderer `dmabuf_cache`/`buffers` lines to name the leaking renderer for
   each leak, then writes the fix (smithay fork if renderer-side; cosmic-comp if a
   held second ref / missing cleanup call).

> Superseded status follows.

**2026-06-15: the running compositor is the stock Fedora RPM, NOT our counter
build — every capture since the 06-13 boot has been blind.** `/usr/bin/cosmic-comp`
is `cosmic-comp-1.0.16^git20260612.52b3f93-1.fc44` (pristine: `rpm -Vf` reports no
content discrepancy; it contains none of our marker strings — `VRAM/resource
census`, `SIGUSR1 dumper installed`, `GL_KHR_debug callback installed`). A `dnf`
operation on 2026-06-13 01:00 overwrote the instrumented build we installed; the
PID running since today's login is that distro package, so SIGUSR1 does nothing
and `journalctl | grep "GL_KHR_debug callback installed"` prints nothing. The
counter build is intact at `target/release/cosmic-comp` (2026-06-12 19:35, all
markers present) with the smithay fork wired into `Cargo.toml`. **Resume = `sudo
make install` (copies that binary) + log out / back in, then verify the build
identity (see below) BEFORE trusting any census.** `/etc/environment` already
carries all three vars (`COSMIC_GL_DEBUG=1`, `COSMIC_GL_DEBUG_SYNC=1`,
`RUST_LOG=…gl_debug=warn…`) — no edit needed. The `/tmp` capture scripts now have
canonical copies in [`vram-tools/`](./vram-tools/) so a future wipe/clobber can't
cost another reconstruction. Prior status (still the live hypothesis) follows.

**2026-06-15 counter build verified LIVE + t0 baseline captured.** After `sudo
make install` (installed binary now SHA-matches `target/release`; `rpm -Vf` =
`S.5....T.`) and re-login, PID 104096 logs `GL_KHR_debug callback installed
(sync=true)` and `SIGUSR1 dumper installed`, and the census now emits the
per-class GL block. Baseline at 10:42 (fresh login, 3 toplevels, 2 outputs,
snapshot `/tmp/leak-snapshot-20260615-104200`):

| metric | t0 value |
|---|---|
| cosmic-comp VRAM (nvidia-smi row) | **79 MiB** |
| dma-buf fds / total | **21 / 212.3 MiB** |
| VmRSS / VmHWM | **218.6 / 220.9 MiB** |
| open fds | 231 |
| GL live: textures / egl_images / renderbuffers / framebuffers / buffers | **22 / 46 / 6 / 6 / 6** |
| GL cleanup-queue depth (all types) | **0** |
| raw: egl_images created / destroyed | **47 / 1** |

Two early reads: (1) **cleanup-queue depth is 0** — the 06-10 main-thread drain
fix is functioning; the leak is *not* an undrained queue. (2) **`egl_images` is
the class to watch** — 46 live with destroyed stuck at 1, while textures churn
healthily (created≈destroyed, net live flat at 22) and renderbuffers/framebuffers/
buffers are fixed per-renderer (6 = 2 outputs). If the residual is pinned client
dmabuf imports (the leading hypothesis), `egl_images_created` and the dma-buf
total should climb through the day while `egl_images_destroyed` stays ~flat. The
end-of-day snapshot (close all but Warp) diffed against this row decides it.

**2026-06-12: verification FAILED — the main-thread drain fix is live but the
leak persists at an unchanged rate.** The compositor running since 2026-06-11
07:46 is byte-identical to the fixed 2026-06-10 21:50 build (hash-verified),
yet after ~29 h of normal use the user measured **1.1 GB with all apps closed
except Warp** — (1100 − ~210 fresh-boot floor) / 29 h ≈ **31 MiB/h, the same
rate as 2026-06-08**. Decisive detail: this session involved **no display
reconfigures**, which the main-thread-queue mechanism requires. That mechanism
was real but marginal; the dominant leak is on the **normal per-frame /
client-churn path**, GPU-side, with no tracked CPU handle. We are now in
runbook §4's "counters flat, VRAM climbs" leaf → §5 (GL allocate/delete
imbalance), mechanized in-process (see "2026-06-12 instrumentation" below).

**New instrumentation build ready (`target/release/cosmic-comp`, 2026-06-12
19:01) — install + re-login, then the SIGUSR1 census directly names the
leaking GL object class:**

- **smithay fork wired in** via `[patch.crates-io] smithay = { path =
  "../smithay" }` (branch `vram-pbo-fix`, now 3 commits): PBO free-on-error
  (`a92b0da2`), shm damage clamp (`6f9c66c2` — the cursor `GL_INVALID_VALUE`
  noise disappears), and **global GL object counters** (`fb27b5fb`):
  process-wide atomics counting created/destroyed per class (textures,
  EGLImages, renderbuffers, framebuffers, buffers/PBOs) and queued/drained per
  `CleanupResource` variant, exposed as
  `smithay::backend::renderer::gles::vram_counters()`. Caveats from the
  coverage audit: `buffers` baseline = 2 VBOs × live renderers; sync/program
  counters cover only the cleanup queue, not totals.
- **`gl_debug.rs` template histogram**: the debug callback now also unmutes
  NOTIFICATION-severity messages (`glDebugMessageControl` enable-all) and
  keeps an in-memory histogram of digit-normalized message templates (cap 512,
  severity-tagged keys) — NVIDIA's per-allocation "detailed info" survives
  without the journald trace firehose. Dumped via `dump_histogram()`.
- **Census extended** (`vram_dump.rs`): every SIGUSR1 dump now prints GL live
  objects per class, cleanup-queue depth per type, raw counters, and the GL
  message-template histogram.

**How the next capture localises the leak:** diff two censuses hours apart at
comparable window sets. A *live count* that grows names the leaking class
(then hunt the retainer of its Rust wrapper); a *queue depth* that grows means
an undrained context (drain bug); all wrapper counts flat while the NVIDIA
histogram's allocate templates outpace deletes means a raw-GL or driver-level
allocation bypassing smithay's wrappers.

> Superseded 2026-06-10 status follows.
>
> **2026-06-10: root cause identified and fixed in-tree; awaiting verification
capture.** The leak is the **main-thread renderers' GL destruction queue, which
is never drained**: every output (re-)configuration (`initialize_output` /
`use_mode`, `src/backend/kms/mod.rs`) renders via the main-thread
`KmsState::api` renderer, whose `output_elements(..)` call **imports every
visible surface** (client dmabufs → EGLImage+texture, SHM → full texture copy)
into the main-thread GL context. When those textures are later dropped (next
commit of each surface, or surface destruction), their GL names are pushed onto
the context's `GlesCleanup` mpsc queue — but that queue is only drained by a
frame finishing **on that same context** (`GlesRenderer::cleanup()`), which for
the main-thread renderer happens only at the *next* reconfigure. Each
reconfigure therefore drains the previous event's garbage and pins a fresh
window-set; steady-state holds ~one full window-set of client buffers
(~130–280 MiB), and **closing all apps moves the dead set onto the queue where
it sits forever** — exactly the measured residuals (+278 MiB on 06-08,
+129 MiB on 06-10).

Queue scoping that makes this possible: `GlesCleanup` lives in the EGLContext
`user_data`, shared **only** between explicitly shared contexts
(smithay `egl/context.rs:340-345`); each surface thread and the main thread
create separate `GpuManager`s → separate contexts → separate queues. The
surface threads drain per-frame (`cleanup_texture_cache()` backstop at
`kms/surface/mod.rs`); the main thread drained never.

**Fix (in-tree, uncommitted):** `KmsState::cleanup_renderer_caches()`
(`src/backend/kms/mod.rs`) drains the main-thread renderers via
`cleanup_texture_cache()`, throttled to 2 s, called from `refresh()` in
`src/lib.rs`. `cargo check` clean.

> **2026-06-09: the first diagnostic capture produced no data — caught and root-caused.**
> The build was correctly installed and running (`/usr/bin/cosmic-comp`,
> byte-identical to our release, SIGUSR1 dumper present), but the journal had
> **zero** GL output: no `GL_KHR_debug callback installed`, no `GL:` traces, no
> `GL error call site:` backtraces, no smithay `[GL]` errors. Root cause: the
> `COSMIC_GL_DEBUG` / `RUST_LOG` env vars **never reached the compositor.**
> `cosmic-session` launches `cosmic-comp` *first* (`starting process
> ' COSMIC_SESSION_SOCK=12 cosmic-comp '`, inheriting only cosmic-session's raw
> env), then reads `~/.config/environment.d` and injects the augmented env into
> every *later* child — so `environment.d` reaches the panel/launcher/etc. but
> **not the compositor we instrument.** Corroborated: cosmic-comp ran at the
> default `warn` filter (no `Version:` info line → `RUST_LOG` absent too), and
> `try_install` returned silently at its `if !enabled()` guard. **Fix: put the
> vars in `/etc/environment`** (PAM login env, above `cosmic-session`) — see
> runbook §5, which now documents this and an instant `/proc/<pid>/environ`
> verification so a bad env never costs another hour. The session was also only
> ~78 min of light early-morning use (no window-op bursts), so even with working
> instrumentation it would have surfaced little — the redo must reproduce the
> provoking workload.

## The 2026-06-10 diagnostic capture — what it showed

~4.3 h session (16:56–21:15), diagnostic build live (backtraces worked), fresh
boot baseline 210 MiB, end-of-session after closing all apps 339 MiB →
**+129 MiB residual**, reproducing the leak.

- **All 11 GL errors share one identical backtrace**: smithay
  `import_shm_buffer` ← `import_surface` ← `WaylandSurfaceRenderElement::from_surface`
  ← `cosmic_comp::backend::render::cursor::draw_cursor` ← surface-thread
  `redraw`. The **client cursor-surface SHM import** — *neither* 2026-06-08
  static-analysis candidate (capture PBO readback was candidate A, titlebar
  `update_memory` was candidate B). Lesson recorded: the NVIDIA "Size and/or
  offset out of range" message is *not* PBO-specific; `TexSubImage2D` emits it
  too.
- **Cursor-error root cause (confirmed in code):** `damage_since()` unions
  damage across commits, each rect clamped only to *its own* commit's buffer
  dimensions (`utils/wayland.rs:198-211`). A cursor buffer that shrinks
  between commits (pointer crossing outputs with different scales → cursor
  re-rendered at new scale) yields rects exceeding the current texture →
  `TexSubImage2D` → `GL_INVALID_VALUE`, upload skipped, stale cursor pixels.
  **No allocation happens on this path — the errors are not the leak**, they
  are an activity beacon for cross-scale pointer movement. Fixed on the local
  smithay fork (`vram-pbo-fix` branch, commit `6f9c66c2`: clamp accumulated
  damage to current buffer size).
- **VRAM trajectory correlations** (sampler at 60 s): persistent up-steps at
  window-open bursts (+153 @18:06, +104 @18:48, +81 @19:35 — legitimate
  working-set growth, recovered at app close −218 @21:13); big swings at
  output reconfigure events (−44 @17:36, −223/+50 @20:35, +65 @21:03 —
  swapchain turnover + main-thread import churn; "Failed to destroy old mode
  property blob" lines mark these). Census counters **zero/flat at all five
  hourly dumps** throughout, confirming again the leak has no tracked CPU
  handle.
- Client dmabuf imports **are charged to cosmic-comp's nvidia-smi row**
  (comp's row jumped +168 MiB the minute Chrome/Chromium opened at 16:56,
  while those clients also have their own rows). This is why pinned imports
  show up — and why the residual exactly tracks the dead window-set.

## The decisive measurement — return-to-idle test (2026-06-08)

Same near-idle desktop state (only Warp terminal + COSMIC system components
running), measured ~9 h apart. Only `cosmic-comp`'s own `nvidia-smi` process row
is counted; other apps (incl. the GPU-accelerated Warp terminal) are separate
rows and excluded.

| | 08:01 baseline | 16:58 after closing all apps | 
|---|---|---|
| **cosmic-comp VRAM** | **350 MiB** | **628 MiB** |
| iced_elements | 5 | 2 |
| toplevels | 5 | 1 |
| sessions / cursor_sessions | 0 | 0 |
| offscreen_renderbuffers | 0 | 0 |
| pending_frames | 0 | 0 |
| minimized_windows / output_zoom_states | 0 | 0 |
| pending_windows/layers/activations | 0 | 0 |

The after-close state has **fewer** windows/elements than baseline yet holds
**278 MiB more VRAM**, with **every tracked counter at or below baseline**. This
is the runbook §4 leaf: *all counters flat but VRAM elevated → GPU-side leak with
no counted CPU handle.* The leaked objects are raw GL names never stored in any
cosmic-comp container, so nothing counts them and nothing frees them.

### Floor trajectory (rolling 50-sample lows, ~50 min/window)

```
349 → 547 → 547 → 651 → 656 → 721 → 909 → 927 → 927 → 904   MiB
```

The floor (lowest VRAM per window) rose monotonically through the morning, then
**plateaued ~900–930** for the last ~3 h — bounded oscillation (band 904–1117) on
a stable floor, consistent with a working-set that ramped up and stabilised, plus
the slow residual leak underneath. On closing apps the floor dropped to 628, i.e.
278 above the true idle baseline. (Note: a partial last-window low can read high
and falsely look like continued climbing — always compute floors over full
50-sample windows.)

### Per-process VRAM at peak (~16:44, before closing apps)

cosmic-comp 954 · jcef_chromium 346 · warp-terminal 264 · cosmic-files 207 ·
chrome gpu-process 107 · xdg-desktop-portal-cosmic 40 · cosmic-panel 18 ·
Xwayland 9. After close: cosmic-comp 628 · warp 264 · portal 40 · panel 20 ·
Xwayland 8.

## Confirmed facts

- **Leak is real and GPU-side with no CPU handle** (see test above).
- **`copy_framebuffer` / `copy_texture` leak their PBO on the GL-error path** —
  verified bug in pinned smithay `rev 85f83ab`,
  `src/backend/renderer/gles/mod.rs:1354-1466`: `GenBuffers`+`BufferData`
  allocate a region-sized PBO, then any non-`NO_ERROR` branch returns `Err` and
  drops the bare `pbo` GLuint; the `GlesMapping` that frees it on `Drop` is built
  only on success. Real bug, but **its callers appear excluded as this session's
  trigger** (see ruled-out). Worth an upstream fix regardless (free PBO on every
  error branch). smithay is a git dep with no `[patch]` override, so a fork/patch
  would be needed to fix it in-tree.

## Ruled out (with evidence)

- **Popups** — 207× `surface missing from known popups` is smithay
  protocol-state churn (`…/wayland/shell/xdg/mod.rs:1982-2058`), not a GPU leak;
  popup GPU buffers live in the wl_surface `data_map` and free independently.
  Churn indicator only.
- **IcedElements** — `iced_elements` tracked `toplevels` 1:1 all day (5→13) and
  both collapsed on close (13→2, 13→1). The earlier orphaned-IcedElement
  hypothesis is disproven.
- **Screenshot menu (`src/utils/screenshot.rs`)** — its `copy_framebuffer` never
  errored: **zero** `Failed to take screenshot` warnings (any error there
  propagates via `?` to that log). Not the GL-error source.
- **Screen-capture / `image_copy_capture` path** — no `screencopy`/`screencast`/
  `image_copy` session lines in the journal; census `sessions=0` at all 11 hourly
  snapshots; the GL errors are **bursty, not the per-frame stream** a screencast
  produces. User confirms no screen-capture app used this session. The capture
  path also keeps its offscreen FB and copy region in sync
  (`image_copy_capture/render.rs:213-230` vs `:147`), so it shouldn't pass an
  out-of-range region.
- **Workspace overview / alt-tab thumbnails** — user confirms neither was used
  this session, despite the burst-size ≈ window-count correlation below.

## Open leads

- ~~48× `GL_INVALID_VALUE` errors~~ **Resolved 2026-06-10**: cursor SHM import
  with unclamped cross-commit damage (see capture section). Not a leak; not
  per-window — the burst-size ≈ window-count correlation was coincidence
  (bursts track pointer crossings during drag/reconfigure activity).
- Secondary verified-but-low-frequency leak: **EGLImage orphaned on
  `import_dmabuf` error path** (smithay `gles/mod.rs:1250-1287`).

## Static analysis (2026-06-08 evening) — GL error localised to 2 candidates

Three independent static investigations of the smithay renderer + cosmic-comp
render path. Net result: the **48 `GL_INVALID_VALUE "Size and/or offset out of
range"` errors have exactly two possible sources**, and — importantly — the
more likely one **does not leak VRAM**, so the errors may be a red herring for
the 278 MiB. The backtrace diagnostic will discriminate.

**Candidate A — capture path (smithay `copy_framebuffer`/`copy_texture` PBO
readback).** The only GL ops that emit "Size and/or offset out of range"
against a *buffer object* are the two PBO `ReadPixels` readbacks
(`gles/mod.rs:1375/1382`, `:1445/1452`) + `map_texture`'s `MapBufferRange`
(`:1507`). Trigger: missing `GL_PACK_ALIGNMENT`/`PACK_ROW_LENGTH` setup +
variable `bpp` (3 for RGB, 8 for RGBA16F) + a hardcoded `*4` in `map_texture`.
This is the **capture/screenshot path — already ruled out as the live trigger**
(sessions=0 all day, no capture apps). The PBO leak fix (smithay fork
`a92b0da2`) covers this path.

**Candidate B — normal per-window path (titlebar `IcedElement` → smithay
`update_memory` `TexSubImage2D`). LIKELY source, but does NOT leak.** Each
toplevel renders its SSD titlebar as an `IcedElement` (explains census
`iced_elements ≈ toplevels` 1:1). It uploads via `update_memory`
(smithay `gles/mod.rs:1104-1148`) → `TexSubImage2D(region.loc, region.size)`
against a **stale texture size**. Root cause **confirmed in code**:
`MemoryBuffer::resize` (`element/memory.rs:190-202`) returns `true` only when
the buffer **byte length** changes; `MemoryRenderBufferInner::resize`
(`:296-303`) only clears the cached texture/`damage_bag` on that `true`. So a
dimension change at **equal byte length** (a fractional-scale rounding
collision) leaves the old-sized texture cached and `import_texture`
(`:321-328`) takes the Occupied branch → `TexSubImage2D` whose region exceeds
the old texture → `GL_INVALID_VALUE`. Bursty (one per window when a global
re-layout/scale change walks every titlebar through the rounding boundary →
matches "9-14 errors vs 9-12 toplevels"); in the **normal render path**
(not overview/alt-tab, both ruled out).
**But `update_memory` allocates nothing and never checks `glGetError`** — after
its early format/size guards (which `Err` *before* any GL call), it issues the
`TexSubImage2D` and returns `Ok(())` unconditionally. The failed upload just
leaves a stale titlebar texture; **no GPU object is allocated or orphaned.**
So if the backtrace points here, **the 48 errors are not the leak** — a real
correctness bug worth fixing (narrow: needs a byte-length collision), but the
278 MiB is elsewhere.

**smithay GL-object destruction queue — ruled out as the leak.** Drop impls
never call `glDelete*`; they push `CleanupResource` onto an mpsc queue held in
`EGLContext` user-data, **shared per-GPU** across all renderers
(`egl/context.rs:345`), drained every frame in `GlesFrame::finish_internal`
(`gles/mod.rs:2524`) + a cosmic-comp backstop after each redraw
(`kms/surface/mod.rs:1405`). Sound on a single NVIDIA GPU. Real-but-secondary
gaps: `GlesRenderer::Drop` doesn't drain (`gles/mod.rs:1878` — teardown-only
leak, not steady growth); the llvmpipe `software_renderer` is a separate
un-shared context (`kms/mod.rs:70` — CPU memory, wrong domain).

**Implication for the capture:** because the likely error source (B) doesn't
leak, the next capture must collect the **GL allocate/delete imbalance** (the
direct leak signal, runbook §5) *as well as* the error backtraces — both come
from one session with `COSMIC_GL_DEBUG=1 COSMIC_GL_DEBUG_SYNC=1` and
`cosmic_comp::utils::gl_debug=trace`. The leak is a GL object allocated whose
Rust wrapper is leaked/retained (so its name never reaches the destruction
queue) **or** created on a path that doesn't use the wrappers — most likely an
offscreen target/renderbuffer/texture retained in a container the census
doesn't instrument (next: audit `Offscreen::create_buffer` sites —
`image_copy_capture/render.rs:230`, `screenshot.rs:42`, `kms/surface/mod.rs:1720`).

## 2026-06-10 install attempt — login failure was NOT the build

Installing the 21:50 build (+ a dnf update in the same window) was followed by an
unbootable system; the user reverted to btrfs snapshot 4441. Investigated from
the failed boot's journal (boot `-1`, survived the revert because `log` is its
own subvolume): the boot dropped to **emergency mode 9 s in** because
`/etc/fstab` in the original root subvol had filesystem type `ntfs3-3g` (a typo;
valid types are `ntfs3` or `ntfs-3g`) on `/mnt/c`, `/mnt/data` and `/mnt/user` —
all three mounts failed, none are `nofail`, so `local-fs.target` failed →
emergency shell. **greetd/cosmic-comp never started; the nvidia module loaded
fine; same kernel as the working boot.** The new build is exonerated — it was
never executed. Recovery: fix the fstab typo in the original root subvol and
boot back into it (keeps the dnf update + installed build), then resume the
verification capture (step 4 below).

## Build/install state (2026-06-10 evening)

- **Branch `pr-2095` rebased onto `upstream/master` `0312f9a2`** (new tip
  `fb8997e2`, 9 ahead / 0 behind). The 11 new upstream commits (image-copy
  cursor fixes, focus border, dbus) rebased + stash-pop of the uncommitted WIP
  with **zero conflicts**. Pre-rebase state preserved on branch
  `backup/pr-2095-2026-06-10`. **Not pushed** — force-push is the user's call.
- **Build to install: `target/release/cosmic-comp` (2026-06-10 21:50)** =
  rebased branch + diagnostic instrumentation + the **main-thread renderer
  drain fix** (uncommitted WIP). smithay still pinned at `rev 85f83ab`
  (fork fixes not wired in). `sudo make install` copies it (the Makefile
  `install` target only copies, never rebuilds).
- **smithay PBO fix** lives on the local fork (`/mnt/work/projects/cosmic-de/
  smithay` branch `vram-pbo-fix`, commit `a92b0da2`) — held in reserve to
  **validate** once a leak source is confirmed; apply via `Cargo.toml`
  `[patch.crates-io] smithay = { path = … }` (reversible, do not commit).

## Instrumentation added this session

- **`src/utils/gl_debug.rs`** — under `COSMIC_GL_DEBUG_SYNC=1`, the debug
  callback now captures `std::backtrace::Backtrace::force_capture()` on every
  high-severity GL **error** and logs it as `GL error call site:`. Sync mode runs
  the callback inline on the offending call's thread, so the backtrace names the
  exact Rust call site — the one thing the census can't give. No-op unless
  `COSMIC_GL_DEBUG_SYNC=1`. `cargo check` clean.

## Next steps (2026-06-12)

1. **Capture the live 1.1 GB residual state before logging out** (user):
   run `/tmp/leak-snapshot.sh` (census + nvidia-smi + dmabuf-fd inventory +
   journal → `/tmp/leak-snapshot-<ts>/`, world-readable). The dmabuf summary
   discriminates *pinned client buffers* (many dmabuf fds, large total) from
   *compositor-internal GL objects* (near-zero) — this forks the hunt.
2. **Install the counter build** (user): `sudo make install`, then change the
   `gl_debug=error` directive in `/etc/environment` to `gl_debug=warn`
   (runbook §5 Pass A filter — `=error` hides the install confirmation), then
   re-login and pre-flight per runbook §0 (expect
   `GL_KHR_debug callback installed (sync=true)`).
3. **Counter capture session** (user): restart the sampler, fire
   `kill -USR1 $(pgrep -x cosmic-comp)` right after login and again every few
   hours of normal use, and once more after closing all apps; then dump the
   journal to `/tmp` world-readable. The agent diffs the censuses — see
   "How the next capture localises the leak" above.
4. Once the class is named, second-pass instrumentation (e.g. sampled
   backtraces on the leaking class's allocation site) pinpoints the call site.

## Next steps (2026-06-10, superseded)

1. ~~Deploy the diagnostic build~~ **Done 2026-06-10.**
2. ~~Read the backtraces / name the error source~~ **Done 2026-06-10** — cursor
   SHM import; not the leak (see capture section above).
3. ~~Identify the leak mechanism~~ **Done 2026-06-10** — undrained main-thread
   renderer cleanup queue (see Current status).
4. **Verify the fix** (user, after `sudo make install` of the fixed build +
   logout/login):
   - Note the fresh-login `nvidia-smi` floor for cosmic-comp.
   - Perform 3–4 display reconfigures (scale or resolution toggles) with a
     normal window set open, plus some cross-output drags.
   - Close all apps except Warp, wait ≥10 s (two cleanup intervals), and
     compare: **pass = cosmic-comp returns to within ~30 MiB of the floor**
     (vs +129/+278 MiB before). The sampler (`/tmp/vram-watch.sh`) gives the
     same answer from `/tmp/vram.csv`.
   - The cursor `GL_INVALID_VALUE` errors will still appear (smithay clamp fix
     is on the fork, not wired into `Cargo.toml`) — they are harmless and
     expected.
5. **Upstream both smithay fixes** from the fork (`vram-pbo-fix`): PBO
   free-on-error (`a92b0da2`) and shm damage clamp (`6f9c66c2`). Consider
   upstreaming the main-thread drain to cosmic-comp as part of PR 2095 or
   separately.

## How to resume the sampler

Canonical scripts now live in [`vram-tools/`](./vram-tools/) (`vram-watch.sh`,
`leak-snapshot.sh`) — redeploy after any reboot rather than reconstructing:
```bash
cp docs/vram-tools/*.sh /tmp/ && chmod a+rx /tmp/*.sh
setsid /tmp/vram-watch.sh & disown    # sampler + hourly census; stop: pkill -f vram-watch.sh
```
`vram-watch.sh` (`flock`-guarded single instance) samples cosmic-comp's VRAM
every 60 s into `/tmp/vram.csv` and fires a SIGUSR1 census at start + hourly.
**The user (andy, uid 1000) runs it** — the agent (uid 1001) cannot signal the
compositor or read its journal; hand-off is via world-readable `/tmp` files
(`chmod a+r`). **Both are no-ops unless the running `/usr/bin/cosmic-comp` is our
instrumented build** — verify first (a stock `dnf` package has zero
instrumentation):
```bash
strings /usr/bin/cosmic-comp | grep -E 'VRAM/resource census|SIGUSR1 dumper installed'  # must hit
```

## Known latent bug (not the leak) — FIXED 2026-07-30

`image_copy_capture/render.rs:299` → `Output::remove_session` (`user_data.rs:171`)
does `.get::<ImageCopySessionsData>().unwrap()`, which panics if the output never
had an image-copy session — a screencast client can crash the compositor on a
capture-failure path. Guard the `get`. Did not fire this session.

**Fixed in `5bbb12e8` (all-fixes, 2026-07-30)** — the root cause was deeper than
the missing guard: `render_workspace_to_buffer`'s constraints-failure path removed
a *workspace*-scope session from the *Output*'s holder, where it never lived. On
outputs without `ImageCopySessionsData` that unwrap panicked; on outputs with it,
the `retain()` was a silent no-op, so the client never received `stopped` and the
dead session lingered in `Workspace::image_copy` until client-side destroy. The
fix removes from the workspace and guards all four holder `remove_*` unwraps.
The same commit also fixes a second latent panic family the audit surfaced:
`constraints_for_output`/`constraints_for_toplevel` unwrapped
`offscreen_renderer()`, so renderer-creation failure (e.g. after a GPU reset)
panicked during constraint negotiation; both now fail the capture instead.
The bug exists verbatim on upstream/master — being sent as its own upstream PR,
separate from the leak PR. No VRAM verdict is affected: the path never fired in
any capture session (a panic is loud), and no counter or census reads changed.
