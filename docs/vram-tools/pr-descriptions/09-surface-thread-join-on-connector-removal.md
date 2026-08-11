# PR: kms: join the surface thread when its connector is removed

Branch: `fix-surface-thread-leak-on-connector-removal` → pop-os/cosmic-comp `master`
(one commit, on current `master`; independent of #2500)

---

## Problem

Removing a connector dropped its `Surface` without joining the thread it owns. A
bare drop only *signals* the thread to end. If a later reconfigure takes the DRM
compositor write lock before the detached thread finishes its in-flight frame,
the thread blocks on the read lock, never observes `End`, and never releases its
renderer, swapchain and postprocess offscreens.

That strands a full set of output-sized render targets in VRAM for the rest of
the session: compositor VRAM does not return to its previous floor after a
display is unplugged, and each subsequent unplug adds another set.

## Fix

Join the thread, so those resources are released before the connector's removal
completes. Unlike `apply_config_for_outputs`, this path holds no compositor lock,
so the join cannot deadlock against the thread's own read lock — the two existing
`drop_and_join` call sites rely on the same property.

## Testing

The metric this fix protects is `live surface threads == connected outputs`: a
stranded thread is precisely one that outlives its connector, and it holds the
renderer and swapchain behind it. On the instrumented build that counter is an
RAII handle, so it cannot drift from the truth.

A scripted full-desktop pass on a dual-output NVIDIA system held
`surface_threads == outputs == 2` at every census, with `live_slots` pinned at 4
across three output reconfigurations while swapchain generations advanced —
superseded generations released rather than accumulating.

**That pass does not exercise this fix, and the distinction matters.** It
reconfigures through `cosmic-randr`, which goes to `apply_config_for_outputs`;
the connector-removal path this commit changes is reached only by a *physical*
unplug or power-cycle. So the evidence above establishes that the invariant holds
on the reconfiguration path, not that this fix repairs the removal path. That
was confirmed by hand during the leak hunt — compositor VRAM failing to return to
its floor across unplugs, with a surface thread surviving its output — and
verifying it in the harness needs a manual power-cycle round, which is why the
regression process calls for one periodically.
