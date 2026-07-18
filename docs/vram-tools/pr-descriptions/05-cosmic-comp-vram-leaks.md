# PR: Fix VRAM/memory leaks in capture, overview, zoom, minimize, and toplevel-handle paths

Branch: `vram-leak-fixes` → pop-os/cosmic-comp `master`
(cleaned-up form of #2095: review feedback folded in, instrumentation/tooling and
the local build-config dropped, rebased onto current `master`, 12 focused commits)

---

## Summary

A family of related VRAM and memory leaks in the capture / image-copy,
workspace-overview, zoom, minimize, and toplevel-info paths. Each keeps GPU
buffers or compositor objects alive after the window or session that needed them
is gone, so memory climbs during capture-heavy, overview, zoom, and minimize
workflows and does not return to the idle baseline.

Note on scope: this is the compositor-side set. On NVIDIA the byte-dominant
render-target leak turned out to be a separate smithay bug (thread-affine
`UserData` caching of exported `Dmabuf`s, fixed in a smithay PR), and a residual
is an NVIDIA driver issue (VRAM not reclaimed for sampled cross-process dmabuf
imports). These commits fix the secondary, compositor-owned leaks.

## What's fixed

- **Toplevel-handle retention** — `ToplevelHandleState` and
  `ImageCaptureSourceKind::Toplevel` held strong `Window`/surface references,
  keeping destroyed toplevels (and their buffers) alive; switched to weak
  references (and removed the `WindowWeak` trait in favour of a static method).
- **Capture sessions** — stop sessions before dropping a toplevel (overview leak)
  and call `ImageCopyCaptureState::cleanup()` + free renderbuffers eagerly instead
  of waiting for drop.
- **Reference cycle** — `Output → OutputZoomState → IcedElement → Output` never
  dropped; broken with a weak edge.
- **Capture / activation / postprocess** — additional leaks in capture sessions,
  pending activations, and postprocessing.
- **Dead minimized windows & cursor cache** — cleaned up on removal.
- **Main-thread renderer cleanup queue** — only drained on reconfigure, pinning
  imported client buffers in the long-lived main renderers; now handled
  event-driven: caches are invalidated right after each main-thread render
  (output reconfiguration, lease scanout tests, screenshots), capture renders
  drain the queue, and surface/buffer destruction schedules a drain on the next
  refresh — so a client exiting long after the last reconfigure is released
  promptly instead of pinning VRAM until the next mode change.
- **Disconnected clients** — removed from all DRM devices (multi-GPU), not just
  one.

## Testing

Validated with a SIGUSR1 resource census over multi-hour dual-output NVIDIA
sessions: every targeted container stayed zero/bounded under load — capture and
cursor sessions, offscreen renderbuffers, pending activations/frames, zoom states,
and minimized windows all `0`; the main-thread cleanup queue drained fully (24k+
textures cycled through it and freed, 400+ PBO readbacks all freed). Compiles
against the pinned smithay (rev `85f83ab`).
