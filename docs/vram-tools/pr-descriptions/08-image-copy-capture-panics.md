# PR: image-copy: fix panics on capture failure paths

Branch: `fix-image-copy-capture-panics` → pop-os/cosmic-comp `master`
(one commit, on current `master`; independent of #2500)

---

## Problem

Three unwraps on the image-copy capture failure paths, all reached when a
capture cannot proceed:

- `render_workspace_to_buffer`'s constraints-failure path removed the session
  from the workspace's `Output`, but workspace-scope sessions live in
  `Workspace::image_copy`. On an output that never hosted an output-scope
  capture the `ImageCopySessionsData` unwrap **panics** — a workspace-scope
  screencast frame arriving while the output has no current mode takes down the
  compositor. On an output that had, the `retain()` is a silent no-op: the
  workspace's owned `Session` is never dropped, the client never receives
  `stopped`, and the dead session lingers until client-side destroy.
- `constraints_for_output` / `constraints_for_toplevel` unwrapped
  `offscreen_renderer()`, so renderer-creation failure — a GPU reset, say —
  panics rather than failing the capture, unlike the render-path callers.

## Fix

Remove the session from the workspace that owns it. Make
`SessionHolder::remove_session` / `remove_cursor_session` on `Output` and
`CosmicSurface` tolerate a missing `ImageCopySessionsData` instead of
unwrapping, so removal is idempotent. Fail the capture on renderer-creation
failure instead of unwrapping, matching the render path.

## Testing

These branches cannot be reached by any desktop workload: `constraints_for_*`
return `None` only when an output has no current mode or an offscreen renderer
cannot be built, and `apply_config_for_outputs` gives every output a mode —
disabled ones included — before a client can bind a capture source, with
smithay's `Output` never clearing `current_mode` once set. A normal capture pass
therefore runs to completion without executing a line of this fix.

They were covered instead by fault injection, on an instrumented build of the
same tree that fails the constraints query made while rendering a frame. Against
a dual-output NVIDIA session, six workspace-overview cycles plus three
screenshots drove **56 workspace-scope and 58 toplevel-scope constraints
failures**: no panic, and a GPU-resource census at each boundary showed every
capture session torn down, no offscreen renderbuffer outliving its client, and
the renderer cleanup queues drained. The overview holds workspace- and
toplevel-scope sessions simultaneously, so one workload drives both branches.

The idempotent-removal hardening is defence-in-depth for callers that remove
twice; no caller does so today, so that property remains verified by inspection.
The 58 toplevel failures did run through the hardened `remove_session` without
panicking, but whether the missing-data branch itself was taken is not evidenced.
