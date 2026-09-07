# VRAM retest requirements

The user requested all recommended corrections to the automated retest workflow
after additional compositor fixes were added.

- Validate the rebased combined instrumented build without losing its separate
  capture teardown and weak-handle fixes. Identify the functional delta from the
  PR rather than claiming PR-only validation from a combined build.
- Pin exact binary/process identities, immutable declared phases, request-specific
  acknowledgements, complete censuses and explicit suite completion. Missing or
  unsupported evidence is incomplete, never PASS.
- Require observed activity and post-phase cleanup for minimized normal, sticky
  and fullscreen windows; pending activations; changed and unchanged named
  cursors, zoom and shake magnification;
  pointer hints across matching focus, different focus and leave; capture and
  recording; output reconfiguration; inactive-session cleanup and retry;
  renderer error-path invalidation; session-lock output removal and multi-GPU
  cleanup where hardware permits.
- Queue evidence must identify outstanding work across independent renderer
  queues. Process-wide enqueue/drain totals cannot establish FIFO progress.
- Separate normal, one-shot fault and operator-assisted hardware rounds. Restore
  output configuration and clean up only owned clients and fault controls.
- Add synthetic negative fixtures and protocol-helper tests before live runs.
  Keep timing-sensitive desktop rounds separate from the ordinary functional gate.
- Preserve historical measurements and document which conclusions require reruns.
  Do not install, signal the user's compositor, push, or post PR comments.

The desktop user must deploy and run privileged/session-bound steps. Missing
hardware limits measured coverage; it does not authorize fabricating evidence.
