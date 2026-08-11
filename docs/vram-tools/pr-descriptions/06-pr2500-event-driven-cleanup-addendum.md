# PR 2500 addendum: periodic renderer drain → event-driven cache management

Response to Drakulix's review feedback on #2500 asking to replace the 2s
`cleanup_texture_cache` poll with an explicit cache invalidation after the
(re-)configuration render. New commit on `vram-leak-fixes`:
`fix(kms): replace periodic renderer cleanup with event-driven cache management`.

## What changed vs. the shipped poll

The poll (`cleanup_renderer_caches`, every 2s from `refresh()`) is gone, replaced
by three event-driven mechanisms:

1. **Invalidate after each infrequent main-thread render.** Output
   (re-)configuration (`apply_config_for_outputs`) and the DRM-lease scanout
   test (`allow_frame_flags`) call the new smithay
   `Renderer::invalidate_caches()` right after rendering, dropping the imports
   the render just cached. One-shot screenshots do the same.
2. **Drain after capture renders.** Screencopy sessions
   (output/window/cursor → buffer) drain the renderer's GL destruction queue
   after every capture render (`cleanup_texture_cache` — cheap, keeps the
   import cache warm for streaming sessions).
3. **Destruction-scheduled drain.** Surface and buffer destruction set a flag;
   the next `refresh()` drains all main-thread device renderers once.

## Why invalidate-after-config alone is not enough

Mechanism 3 is what actually bounds the "client exits long after the last
configuration" leak. Per-surface caches (smithay's `RendererSurfaceState` and
the multigpu per-surface texture cache) hold the imported textures for as long
as the surface lives, and dropping them at surface destruction merely *queues*
the GL deletions on the importing context. An idle main-thread renderer never
flushes that queue on its own, so without a drain tied to the destruction
event a dead client's buffers stay pinned in VRAM until the next mode change —
the very scenario the poll originally fixed. The destruction-scheduled drain
releases them within one refresh cycle, with no polling.

## Dependency

Requires smithay's `Renderer::invalidate_caches` (upstream PR from
`renderer/invalidate-caches`). The smithay rev pin (`efeb597`) is left
unchanged; bump it once the smithay PR lands. Verified locally that the new
commit compiles clean against `efeb597` + only the invalidate-caches commit
(which cherry-picks onto `efeb597` without conflicts), so no other API skew is
involved.

## Verification

Compile-verified as above. Runtime-verified on the instrumented census build
(`all-fixes-instrumented-invalidate`) by a scripted full-desktop pass on a
dual-output NVIDIA system: 15 censuses, nine workflows each driven and censused
in isolation, scored by an automated verdict.

Across client exits, captures and reconfigures, with no polling anywhere:

- The main-thread cleanup queues drained in every census, and no renderer cache
  retained a dead entry.
- `live_slots` held at 4 (two per output) through three reconfigurations while
  swapchain generations advanced — recycling, not accumulation.
- One surface thread per connected output throughout.
- 10 client open/close cycles created and destroyed 86 EGLImages; six screenshots
  created and freed 12 renderbuffers. Both balanced to zero, which is mechanism 3
  doing its job: the destruction-scheduled drain releases a dead client's imports
  within one refresh, with no mode change involved.

Each phase is separately evidenced by a counter only its own workload moves, so a
phase that drove nothing fails the run instead of passing on flat counters.

Two scope notes. The pass reconfigures through `cosmic-randr`
(`apply_config_for_outputs`), which is on this branch — the connector-removal
`drop_and_join()` that exists only in the instrumented build is reached by a
*physical* power-cycle, which this pass does not perform, so these numbers are
free of it; physical hotplug remains unmeasured on the shipped branch. And live
EGLImages end a session ~10 above its opening baseline, arising during popup churn
and then flat across the remaining workflows — unexplained, but not on the paths
this change touches.
