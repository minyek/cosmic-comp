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

Runtime-verified indirectly via the consuming cosmic-comp fix: with
`invalidate_caches()` wired into the main-thread renderer, isolated tests of
reconfigure cycles, capture-render drains, and destruction-scheduled drains
all show the renderer's GL cleanup queue and dmabuf import caches staying flat
and bounded across the exercised event, with no residual growth over a 15
minute idle window after activity.
