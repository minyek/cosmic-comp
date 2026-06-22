# PR: allocator: cache exported Dmabuf/GbmFramebuffer in thread-safe UserData

Branch: `fix/threadsafe-userdata-caches` → Smithay/smithay `master`

---

## Problem

In a multi-threaded compositor, GPU memory grows steadily across output
reconfigures: each exported swapchain slot can leak its cached `Dmabuf`, pinning
the underlying GBM buffer for the lifetime of the process.

## Root cause

`Slot::export` caches the exported `Dmabuf` in the slot's `UserDataMap` using
`insert_if_missing`, which stamps the entry with the id of the thread that first
exported the slot. `UserData::drop` runs the value's destructor only when the slot
is dropped on that same thread; on any other thread it deliberately skips the drop
(to avoid dropping possibly-`!Send` data off-thread).

When a slot is exported on one thread (e.g. the main renderer during a
reconfigure) but dropped on another (a per-output render thread), the cached
`Dmabuf`'s `Arc` strong count is never decremented and its GBM buffer leaks. The
`GbmFramebuffer` caches in `GbmBufferedSurface` have the same defect.

## Fix

Cache both with `insert_if_missing_threadsafe`. `Dmabuf` is `Send + Sync`, and
`GbmFramebuffer`'s `Drop` (`destroy_framebuffer`) is safe on any thread, so neither
needs thread-affine storage — and the sibling framebuffer cache in `compositor`
already uses the thread-safe variant.

## Testing

Reproduced in a dual-output session on the proprietary NVIDIA driver, where the
main renderer exports slots during `apply_config` and per-output threads drop
them. With the fix the compositor's dma-buf inventory dropped from ~635 MiB to
~196 MiB and its per-process GPU memory from 234 MiB to 178 MiB; the orphaned
render-target `Dmabuf`s no longer accumulate across monitor power-cycles.
