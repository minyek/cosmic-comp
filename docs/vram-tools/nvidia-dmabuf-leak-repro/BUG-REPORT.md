# DRAFT NVIDIA bug report — VRAM not reclaimed for sampled cross-process dmabuf imports

> Draft for review before posting to the NVIDIA Linux Graphics forum / bug tracker.
> Attach `repro_same_process.c` and `repro_cross_process.c` from this directory.

## Title

EGLImage import of a cross-process dma_buf leaks VRAM when the imported texture is
sampled — memory not reclaimed on eglDestroyImageKHR/glDeleteTextures (Wayland
compositor working set grows unbounded across a session)

## Environment

- GPU: NVIDIA GeForce RTX 4090
- Driver: 595.71.05 (CUDA 13.2)
- Kernel: 7.0.12-201.fc44.x86_64
- Distro: Fedora Linux 44 (Workstation Edition)
- Display: Wayland (COSMIC compositor), but reproduced with a standalone program
  that uses **no compositor** — just GBM + EGL + GLES2.

## Summary

When a process imports a dma_buf that was **allocated by a different process** as an
`EGLImage` (via `EGL_EXT_image_dma_buf_import`), binds it to a GLES texture, and
**samples it in a draw**, the driver allocates GPU memory that is **not released**
when the `EGLImage` and texture are destroyed and the source buffer is freed. The
retained memory accumulates with the number of distinct imported buffers and is
only reclaimed when the importing process exits.

This is the root cause of the well-known "Wayland compositor VRAM grows after
resizing windows / opening windows and never comes back" reports, since a
compositor imports and samples a fresh client buffer set on every window
open/resize. It reproduces on all major compositors; the attached program isolates
it from any compositor.

## Reproduction

Build:

```sh
gcc -O2 -o repro_same_process  repro_same_process.c  $(pkg-config --cflags --libs gbm egl glesv2)
gcc -O2 -o repro_cross_process repro_cross_process.c $(pkg-config --cflags --libs gbm egl glesv2)
```

`repro_cross_process` forks a producer that allocates GBM buffers
(`GBM_BO_USE_RENDERING`, ARGB8888) and passes their dma_buf fds over a unix socket
(`SCM_RIGHTS`); the importer imports each as an `EGLImage`
(`EGL_LINUX_DMA_BUF_EXT`), optionally binds + samples it, then calls
`eglDestroyImageKHR` + `glDeleteTextures`. The producer frees each buffer after the
importer acks. At most one buffer is alive at a time.

```sh
# argv: <iters> <draw 0|1> <w> <h> <vary 0|1>
./repro_cross_process 20000 1 1920 1080 0   # cross-process, sampled  -> LEAKS
./repro_cross_process 20000 0 1920 1080 0   # cross-process, no sample -> no leak
./repro_same_process  100000 2 1920 1080    # same-process, sampled   -> no leak
```

## Observed (driver 595.71.05)

Importer process GPU memory (from `nvidia-smi`, per-PID) after ~20k import/destroy
cycles, each buffer destroyed before the next:

| scenario | importer GPU memory |
|---|---|
| same-process import + sample | flat ~6 MiB (no leak) |
| cross-process import, no sample | flat ~6 MiB (no leak) |
| cross-process import + sample, fixed size | jumps to ~2.2 GB, then plateaus |
| cross-process import + sample, varying sizes | oscillates 1.5–2.2 GB |

Every `EGLImage` and texture is destroyed each iteration and every source buffer is
freed, yet GPU memory stays elevated until the process exits.

## Expected

GPU memory for imported-and-sampled cross-process dma_bufs should be reclaimed when
the `EGLImage` and the texture bound to it are destroyed (and the source buffer is
released), returning to baseline — as it already does for same-process imports and
for non-sampled imports.

## Analysis

- The leak requires **both** that the dma_buf is **foreign** (allocated by another
  process) **and** that the imported texture is **sampled** in a draw. Either alone
  does not leak. This suggests the driver materialises a private VRAM copy of the
  foreign buffer on first sample (a foreign-layout buffer cannot be sampled
  directly) and retains that copy in a pool that is not freed on
  `eglDestroyImageKHR`/`glDeleteTextures`.
- The pool is bounded (~2 GB even under extreme churn here), but for a real
  compositor it manifests as a large, activity-driven, idle-sticky working set that
  only resets on compositor restart.

## Impact

Every Wayland compositor on this driver accumulates VRAM across a session and never
returns to its idle floor without a restart, because compositing inherently imports
and samples client dma_bufs. Matches existing reports for kwin, sway, weston, niri,
mutter, and COSMIC.

## How to file

The proprietary Linux driver has no public bug tracker; the official channel is the
NVIDIA Developer Forums, Linux graphics category
(<https://forums.developer.nvidia.com/c/gpu-graphics/linux/148>), where NVIDIA's
Linux driver engineers triage. (This is a userspace EGL/GL driver issue, so the
forum — not the `open-gpu-kernel-modules` or `egl-wayland` GitHub repos — is the
right place.)

1. Collect a driver report: `sudo nvidia-bug-report.sh` → `nvidia-bug-report.log.gz`.
   NVIDIA routinely asks for it; attach it.
2. Open a new topic in the Linux graphics category with the title and body above.
3. Attach `repro_cross_process.c`, `repro_same_process.c`, and
   `nvidia-bug-report.log.gz`; include the build/run commands and the results table.
4. Lead with "minimal standalone reproducer, no compositor" — that is what makes it
   actionable: NVIDIA can build and run it directly and watch `nvidia-smi`.
5. Cross-link the existing same-symptom report so it is triaged together, and
   consider posting the minimal repro there too:
   <https://forums.developer.nvidia.com/t/multiple-wayland-compositors-not-freeing-vram-after-resizing-windows/307939>
6. Ask specifically: is the private VRAM copy made for a sampled cross-process
   dma_buf import pooled and not released, and can it be reclaimed on
   `eglDestroyImageKHR` + texture deletion?
