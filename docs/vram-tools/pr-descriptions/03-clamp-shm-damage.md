# PR: renderer/gles: clamp accumulated shm damage to current buffer size

Branch: `fix/clamp-shm-damage` → Smithay/smithay `master`

---

## Problem

When a client shrinks its shm buffer between commits — e.g. a cursor surface
re-rendered at a smaller size after a fractional-scale change — the next upload
fails with `GL_INVALID_VALUE` and the surface keeps its stale texture contents.

## Root cause

`damage_since()` unions damage rectangles across commits, clamping each rect only
to the buffer dimensions of the commit it came from. If a later commit has a
smaller buffer, the accumulated set still contains the larger rects, and
`TexSubImage2D` rejects the whole upload.

## Fix

Intersect each accumulated damage rect with the current buffer size before
uploading.

## Testing

The `GL_INVALID_VALUE` errors that occurred when moving the pointer between
outputs of different scales (cursor buffer shrinking between commits) no longer
appear, and the cursor updates correctly.
