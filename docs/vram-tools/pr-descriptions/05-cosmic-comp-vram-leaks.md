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
- **Panics on capture failure paths** — the workspace constraints-failure path
  removed the session from the `Output`, but workspace-scope sessions live in
  `Workspace::image_copy`: an unwrap panic on an output that never hosted an
  output-scope capture, and a silently leaked session on one that had. Removal is
  now idempotent, and `constraints_for_*` fail the capture instead of unwrapping a
  missing offscreen renderer.

## Testing

Machine-checked regression pass on an installed dual-output NVIDIA session: nine
workflows — output reconfiguration, client churn, capture, popups, zoom, workspace
switching, pointer motion, minimize — each censused at its own boundaries and
scored per phase.

**Result: pass.** In every census the cleanup queues drained, no renderer cache
retained a dead entry, no capture session or offscreen renderbuffer outlived its
client, `surface_threads == outputs`, and `live_slots` held at 4 across three
reconfigurations while swapchain generations advanced — recycling, not
accumulation. Per phase: 10 client open/close cycles created and destroyed 86
EGLImages, six screenshots created and freed 12 renderbuffers, both balanced to
zero.

Each phase is evidenced by a counter only its own workload can move — input
events, capture sessions, minimized windows, toplevels above the phase's own
baseline — so "counters stayed flat" cannot be mistaken for "the workload never
ran". A generic GL-churn threshold cannot make that distinction, because an idle
desktop creates textures fast enough to satisfy one.

The capture *failure* paths are unreachable from any desktop workload, since every
output carries a mode before a client can bind a capture source, so they are
covered by fault injection: 56 workspace-scope and 58 toplevel-scope constraints
failures, no panic, every session torn down and its client notified.

One residual, not a regression in this set: live EGLImages end a session ~10 above
its opening baseline, arising in the popup phase (582 created against 569
destroyed) and then holding flat across the remaining workflows rather than
growing. Unexplained, and tracked separately from these fixes.
