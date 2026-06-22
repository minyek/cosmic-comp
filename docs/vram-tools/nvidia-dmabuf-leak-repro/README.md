# NVIDIA cross-process dmabuf-sample VRAM leak reproducer

Standalone reproducers (no cosmic-comp / smithay code) that isolate the residual
VRAM growth seen in the compositor to an NVIDIA driver behaviour: GPU memory that
is **not reclaimed when a sampled cross-process dmabuf import is destroyed**.

## What each program does

- `repro_same_process.c` — allocates a GBM buffer, exports it to a dmabuf, imports
  it as an `EGLImage`, binds it to a GLES texture, then destroys everything, in a
  loop. The buffer is allocated by the *same* process that imports it.
- `repro_cross_process.c` — forks a **producer** that allocates GBM buffers and
  passes their dmabuf fds over a unix socket (`SCM_RIGHTS`), and an **importer**
  that imports each as an `EGLImage`, optionally **samples it in a draw**, then
  destroys it. This matches the Wayland client→compositor flow (the importer holds
  a *foreign* dmabuf). `argv`: `<iters> <draw 0|1> <w> <h> <vary 0|1>`.

Both sample the importer process's own `nvidia-smi` GPU memory as they run.

## Build

```sh
gcc -O2 -o repro_same_process  repro_same_process.c  $(pkg-config --cflags --libs gbm egl glesv2)
gcc -O2 -o repro_cross_process repro_cross_process.c $(pkg-config --cflags --libs gbm egl glesv2)
```

## Result (NVIDIA 595.71.05, 2026-06-22)

| scenario | importer GPU memory after ~20k imports |
|---|---|
| same-process import + sample | flat ~6 MiB (no leak) |
| cross-process import, **no** sample | flat ~6 MiB (no leak) |
| cross-process import **+ sample**, one size | jumps to ~2.2 GB then **plateaus** |
| cross-process import **+ sample**, varying sizes | oscillates 1.5–2.2 GB (still bounded) |

**Conclusion:** the leak is triggered only by **sampling a foreign (cross-process)
dmabuf**. The driver makes a private VRAM copy of the foreign buffer on first
sample and pools it rather than freeing it when the `EGLImage`/texture is
destroyed. The pool is bounded (~2 GB even under extreme churn) but large and slow
to release. A compositor cannot avoid this — it must import and sample client
buffers to composite them. It is not a cosmic-comp/smithay object leak: every
compositor-side GL resource is created and destroyed in balance.

This directory is the artifact to attach to an NVIDIA driver bug report.
