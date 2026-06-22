# Upstream PR notes (smithay)

Factual notes for the four upstream-ready smithay fixes. **smithay policy: do not
use AI to write issue/PR prose** — these are facts to write your own PR
descriptions from, not copy-paste text. Each branch is one commit on
`origin/master` (`6eeb8a76`), compiles (`cargo check`, default features), and
carries an AI-assistance disclosure line in its commit message.

Push each branch to your smithay fork and open a PR against `Smithay/smithay`
master. They are independent — no ordering between them.

cosmic-comp needs no change for these (the dmabuf leak is entirely in smithay); the
cosmic-comp-side leak fixes are already on `all-fixes` / `pr-2095`.

---

## 1. `fix/threadsafe-userdata-caches` — the headline leak (Leak A)

- Files: `src/backend/allocator/swapchain.rs`, `src/backend/drm/surface/gbm.rs`.
- Bug: `Slot::export()` caches the exported `Dmabuf` in the slot's `UserDataMap`
  via **`insert_if_missing`** (non-thread-safe), which records the *creating
  thread's* id. `UserData::drop` only runs `ManuallyDrop::drop` when dropped on
  that same thread — off-thread it deliberately skips the drop ("leak it
  otherwise") to avoid dropping possibly-`!Send` data on the wrong thread.
- Trigger: the main renderer exports these slots on the **main thread**
  (apply_config), but the `InternalSlot`s are later dropped on a **different**
  thread → the cached `Dmabuf`'s `Drop` never runs → its `Arc` strong count is
  orphaned and the GBM buffer is pinned in VRAM forever.
- Why the fix is correct: `Dmabuf` is `Send + Sync`, so it never needed
  thread-affine storage — and the sibling framebuffer cache one line away
  (`compositor/mod.rs`) already uses `insert_if_missing_threadsafe`.
- Fix: `insert_if_missing` → `insert_if_missing_threadsafe` for the `Dmabuf` cache
  (swapchain) and the `fb` cache (gbm surface).
- Evidence (NVIDIA RTX 4090, dual output): dma-buf fd inventory **635.7 MiB →
  196.1 MiB**, cosmic-comp `nvidia-smi` **234 → 178 MiB**; the orphaned
  render-target dmabuf clone-site groups disappeared (every tracked dmabuf became
  a live swapchain slot).

## 2. `fix/free-pbo-on-exportmem-error` — PBO leak on error path

- File: `src/backend/renderer/gles/mod.rs` (`copy_framebuffer` / `copy_texture`).
- Bug: `GenBuffers` + `BufferData` allocate a region-sized PBO; on any
  non-`NO_ERROR` branch the function returns `Err` and drops the bare `pbo`
  `GLuint`. The `GlesMapping` that frees it on `Drop` is only constructed on the
  success path → the PBO leaks on every error return.
- Fix: free the PBO on all error branches.
- Note: error-path correctness fix, not the dominant leak.

## 3. `fix/clamp-shm-damage` — out-of-range TexSubImage2D on shrinking shm buffers

- File: `src/backend/renderer/gles/mod.rs` (shm import / accumulated damage).
- Bug: accumulated damage is unioned across commits, each rect clamped only to
  *its own* commit's buffer dimensions. An shm buffer that **shrinks** between
  commits (e.g. a cursor re-rendered at a smaller scale when the pointer crosses
  outputs) yields damage rects exceeding the current texture →
  `TexSubImage2D` → `GL_INVALID_VALUE`, upload skipped, stale pixels.
- Fix: clamp accumulated damage to the current buffer size before upload.
- Note: correctness fix (not a leak).

## 4. `fix/free-egl-image-on-import-error` — orphaned EGLImage on import error

- File: `src/backend/renderer/gles/mod.rs` (`import_dmabuf`).
- Bug: `create_image_from_dmabuf` allocates an `EGLImage`; if the following
  `import_egl_image` bind returns `Err`, the bare image handle is dropped — no
  `GlesTexture` ever owns it, so it is never queued for `DestroyImageKHR`, leaking
  the handle (and, on the proprietary NVIDIA driver, the GPU allocation behind it).
- Fix: on the error path, send the orphan `EGLImage` to the same
  deferred-destruction queue a `GlesTexture` uses on drop.
- Note: defensive — instrumentation showed this path did **not** fire at runtime
  in our sessions (counter read 0). It is a real resource-cleanup gap, but not a
  demonstrated source of the observed VRAM.

---

## Held back (not in the upstream set)

- `fix/multigpu-texture-cleanup` (Leak B): adds a surface-destruction hook to free
  the multigpu per-surface texture cache. **Not filed as a leak fix** — it is
  multi-GPU-specific, the single-GPU 25 h census showed the cache's textures stay
  bounded (live textures = 45), and the unbounded variant was downstream of Leak A
  (stranded-thread `ErasedContextId` churn), now fixed. If filed at all, frame it
  as a defensive consistency change (mirror `RendererSurfaceState`'s cleanup), not
  a leak fix.
- The NVIDIA driver VRAM-reclaim issue is not a smithay/cosmic-comp bug — see
  [`nvidia-dmabuf-leak-repro/`](./nvidia-dmabuf-leak-repro/).
