# PR: renderer: add `Renderer::invalidate_caches` to drop all cached imports

Branch: `renderer/invalidate-caches` → Smithay/smithay `master`

---

## Problem

`Renderer::cleanup_texture_cache` only frees resources whose source buffers
have already been dropped — it retains cache entries whose buffers are still
alive. A renderer that renders only infrequently (e.g. a main-thread renderer
that runs solely during output (re-)configuration) therefore keeps its cached
imports, and the GPU memory behind them, pinned between renders — releasing
them only lazily, once something else drains the cache after the buffers go
away.

## Fix

Add `Renderer::invalidate_caches`, which discards every cached import
unconditionally instead of only the ones whose source buffer is already gone.
For an idle renderer, warm import caches provide no benefit, so dropping them
right after a render frees the underlying client buffers immediately rather
than pinning them until the next render re-imports.

The method defaults to `cleanup_texture_cache`, so external `Renderer`
implementors keep their existing behaviour unchanged. `GlesRenderer`,
`GlowRenderer`, `PixmanRenderer`, and `MultiRenderer` override it to drop
their caches in full: the import caches and the per-dmabuf bind-target caches
(EGLImage + renderbuffer + framebuffer object). `MultiRenderer` propagates to
every device — target, render, and each "other" renderer used for buffers that
can't import directly on the render node — and drops the cached shared
framebuffer used to copy between render and target node. The multi-device
invalidation is best-effort: every device is attempted even if one fails and
the first error is returned, so one lost context can't leave the other
devices' imports pinned. As a drive-by fix, `MultiRenderer`'s
`cleanup_texture_cache` now also visits the "other" renderers, which it
previously skipped.

This only affects caches owned by the renderer itself. Textures cached
per-surface in surface user-data (`RendererSurfaceState`, the multigpu
per-surface texture cache) hold their own references and are released on
surface destruction or buffer replacement, not by this call.

Finally, `GpuManager::cleanup_texture_cache` and `GpuManager::invalidate_caches`,
which apply the corresponding `Renderer` method to every enumerated device.
Outside a draw a compositor holds a `GpuManager`, not a `MultiRenderer`, so
without these it must hand-roll a loop over `devices_mut` — and such a loop
cannot reach the buffers cached for copying between a render and a target node,
which belong to the manager, are keyed by node pair, and are reachable only
through a `MultiRenderer` built for that pair. `invalidate_caches` clears those
copy buffers too: one full output-sized dmabuf per node pair, which no renderer
owns. This is the entry point the consumer below actually calls.

## Consumer / motivation

Written to support a cosmic-comp fix (event-driven main-thread renderer-cache
cleanup, replacing a periodic 2s poll — pop-os/cosmic-comp#2500; local detail
in PR description 06).
cosmic-comp calls `invalidate_caches()` on its main-thread renderer right after
each infrequent render (output reconfiguration, DRM-lease scanout test,
one-shot screenshots), so the imports that render just cached don't linger in
VRAM until the next reconfigure.

## Testing

`cargo fmt --check`, `cargo clippy --all-features --all-targets`, and
`cargo test --all-features` (unit + doctests) all pass clean, rebased onto
current `upstream/master`. Unit tests pin the pixman semantics — the contrast
between `cleanup_texture_cache` (retains live entries, prunes dead ones) and
`invalidate_caches` (drops live entries too) — using a memfd-backed `Dmabuf`.
The GL backends need a real context and remain untested, like
`cleanup_texture_cache` itself.

Runtime-verified indirectly via the consuming cosmic-comp fix, which calls
`GpuManager::invalidate_caches()` after each infrequent main-thread render. The
most recent pass was a scripted full-desktop run on a dual-output NVIDIA system —
15 GPU-resource censuses, nine workflows each driven and censused in isolation so
the deltas are attributable, scored by an automated verdict:

- **Output reconfiguration**, the case this method exists for: three cycles of
  one output advanced its swapchain from generation 4 to 112 while `live_slots`
  stayed pinned at 4 (two per output) and the untouched output stayed at
  generation 3. Every superseded generation's imports were released rather than
  accumulating, which is what invalidating right after an infrequent render is
  supposed to achieve.
- **Screencopy**: 12 renderbuffers created and 12 freed across 6 captures, with
  no capture session, offscreen renderbuffer or pending frame outliving its
  client.
- **Client churn**: 86 EGLImages and 268 textures created *and* destroyed across
  10 client open/close cycles, with every renderer cache value identical before
  and after.

The renderer's GL cleanup queues drained in all 15 censuses and no cache held a
dead entry. Each workflow is separately evidenced by a counter only that workload
can move, so a phase that drove nothing fails the run rather than passing on flat
counters — a distinction generic GL churn cannot make, since an idle desktop
creates textures fast enough to satisfy any such threshold.

**Not covered:** the machine has a single GPU, so `MultiRenderer`'s
cross-device invalidation was exercised only in its render/target form, not
across separate render and target *devices*. The pixman unit tests remain the
only direct coverage of the semantics.

**On the reconfiguration figures:** an earlier revision of this description
warned they might flatter the consumer, because the instrumented build also
carries a synchronous surface-thread join on connector removal. That does not
apply — the pass reconfigures through `cosmic-randr`, which takes cosmic-comp's
`apply_config_for_outputs` path, whereas the join is on the connector-*removal*
path a physical unplug takes. The figures are free of it. Physical hotplug
correspondingly remains unmeasured here.
