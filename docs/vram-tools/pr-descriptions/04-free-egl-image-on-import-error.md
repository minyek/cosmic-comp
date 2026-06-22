# PR: backend/renderer/gles: free orphaned EGLImage on import_egl_image failure

Branch: `fix/free-egl-image-on-import-error` → Smithay/smithay `master`

---

## Problem

A failed dmabuf import leaks an `EGLImage` (and, on drivers that back each import
with a GPU allocation, the memory behind it).

## Root cause

`import_dmabuf` creates an `EGLImage` with `create_image_from_dmabuf`, then binds
it to a texture with `import_egl_image`. If the bind returns an error, the
already-created image handle is dropped without ever being owned by a
`GlesTexture`, so it is never queued for `DestroyImageKHR`.

## Fix

On the error path, hand the orphaned `EGLImage` to the same deferred-destruction
queue a `GlesTexture` uses on drop, so it is freed on a current context in
`cleanup`.

## Testing

Defensive fix; compiles cleanly. The error path was not observed to trigger in our
sessions, so normal operation is unchanged — it closes a leak on an error path
that currently drops the handle.
