# PR: renderer/gles: free PBO on ExportMem error paths

Branch: `fix/free-pbo-on-exportmem-error` → Smithay/smithay `master`

---

## Problem

Every failed `copy_framebuffer` / `copy_texture` readback leaks a region-sized GL
buffer object.

## Root cause

Both functions allocate a `PIXEL_PACK_BUFFER`, then construct the owning
`GlesMapping` (which deletes the buffer in its `Drop`) only on the success path. On
any `glGetError` failure the function returns `Err` and the bare buffer handle goes
out of scope without being deleted.

## Fix

Delete the buffer on the error branches, and compute the bytes-per-pixel before
allocating it so an early return cannot leak it either.

## Testing

Error-path fix; verified by inspection and compiles cleanly. The success path is
unchanged, so normal readbacks behave as before; the change only adds the missing
delete on failure.
