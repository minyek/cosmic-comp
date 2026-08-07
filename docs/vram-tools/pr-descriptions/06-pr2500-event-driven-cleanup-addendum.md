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
(`all-fixes-instrumented-invalidate` branches) by a scripted full-desktop pass:
27 censuses over 2.5 hours on a dual-output NVIDIA system, each workflow driven
in isolation. Main-thread import caches and destruction queues stayed bounded
across client exits, captures and reconfigures — the cleanup queue was empty in
every census, no cache retained a dead entry, three output power-cycles
regenerated ~338 swapchain slot generations against 5 live slots, and 10 client
open/close cycles created and destroyed 99 EGLImages while leaving every cache
value unchanged.

The reconfigure figures carry one caveat: the instrumented build also holds a
connector-removal `drop_and_join()` that is not on the clean branch (it sits
inside `95411f26`, a "do not merge" commit). Monitor power-cycles take that
path, so either hoist the fix onto the shipped branch or re-measure before
relying on those numbers.
