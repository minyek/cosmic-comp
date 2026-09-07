# Wayland retest client

Build and test independently of the compositor:

```sh
cargo test --locked --manifest-path docs/vram-tools/clients/Cargo.toml
cargo build --locked --manifest-path docs/vram-tools/clients/Cargo.toml
```

Run `docs/vram-tools/clients/target/debug/vram-retest-client 120` as the desktop
user. The required argument is the whole-process deadline in seconds (1–3600),
including registry discovery. Keep stdin open and send one JSON object per line:
`{"command":"shape-pointer"}`. Each process creates a green SHM-backed toplevel,
with both title and app ID `vram-retest-<pid>`. The helper never injects input,
changes unrelated windows, or decides that a regression passed.

Stdout is JSON Lines with `event`, `pid`, and `detail`. A failed precondition,
missing requested protocol, invalid request, disconnected input, unexpected
compositor close, or expired deadline emits `error` and exits nonzero. Explicit
`destroy`/`quit` requests exit zero after the compositor acknowledges teardown.
Exit zero alone proves no coverage.

## Commands and evidence

| Command | Request / precondition | Evidence to await |
|---|---|---|
| `lock` | Persistent pointer lock; configured, pointer-focused surface; no existing constraint | `locked` |
| `hint` | Commit cursor hint `(80,80)` in surface coordinates; observed lock required | `acknowledged`, then compositor hint counters when unlocked |
| `unlock` | Destroy existing constraint, including after pointer focus leaves | `acknowledged`, then compositor hint/position census |
| `shape-default`, `shape-pointer` | Set named shape using current pointer-enter serial | `acknowledged`, then compositor cursor counters |
| `fullscreen`, `unfullscreen` | Change xdg toplevel fullscreen state | `toplevel-state` or `managed-state` |
| `minimize` | Request xdg toplevel minimization | `managed-state` containing `1` |
| `unminimize` | COSMIC management capability `4` | `managed-state` without `1` |
| `sticky`, `unsticky` | COSMIC management capability `7` | `managed-state` with / without `4` |
| `focus` | COSMIC management capability `2`; own discovered toplevel only | `managed-state` containing `2`; pointer focus still needs input |
| `activate-token` | Commit xdg activation token referencing own surface | `activation-token-done`; census must prove server token acceptance and teardown |
| `destroy`, `quit` | Destroy lock, pending token, xdg toplevel and surface | `acknowledged`, `complete`; subsequent census must prove cleanup |

`submitted` means the request was issued. `acknowledged` follows a
`wl_display.sync` callback after that request; it proves request processing order,
not that the compositor honored an optional request or ran an idle callback.
`complete.detail.coverage_verdict` explicitly requires compositor/harness evidence.
Send one command and await its evidence before sending the next.

`ready` follows initial xdg configure acknowledgement and buffer attachment. It
reports `app_id`, dimensions and availability of constraints, cursor-shape and
activation protocols. `managed` means the helper found its own foreign toplevel
and requested a COSMIC handle. Wait for this before management commands.
`management-capabilities` reports the manager's advertised numeric capabilities.
`managed-state` reports COSMIC state values: maximized `0`, minimized `1`,
activated `2`, fullscreen `3`, sticky `4`. `toplevel-state` uses the distinct xdg
state enumeration (fullscreen `2`, activated `4`).

`pointer-enter` and `pointer-motion` contain surface-local `x`/`y`;
`pointer-leave`, `locked`, `unlocked` are observed protocol events. Global
pointer coordinates require the compositor census. A lock prevents ordinary
motion events, so those events cannot prove a hint was applied on another surface.

## Required desktop scenarios

The harness supplies bounded input and compares phase-local census samples.

1. Matching focus: focus the helper and move the pointer inside it; wait for
   `pointer-enter`. Send `lock`, await `locked`, send `hint`, then `unlock`.
   Require the applied-hint counter to increase and the resulting global pointer
   position to match the surface origin plus `(80,80)`.
2. Different focus: use two helper processes. Lock/hint the first, focus and move
   into the second, prove its `pointer-enter` and the first's leave, then destroy
   the first constraint. Require rejection evidence and unchanged global pointer
   coordinates across destruction after input has stopped.
3. Leave: lock/hint, cause real pointer focus leave (for example minimize the
   owned surface), prove `pointer-leave`, then destroy the constraint. Require
   no applied hint and unchanged pointer coordinates across destruction.
4. Named cursors: after `pointer-enter`, alternate the two shape commands, then
   repeat one unchanged shape. Compare the appropriate compositor counters.
   Shake magnification needs deliberate harness motion and compositor evidence.
5. Minimized lifecycle: run separate normal, confirmed fullscreen, and confirmed
   sticky cases. Minimize, prove COSMIC minimized state, destroy, and verify
   resource census returns to baseline. Requests without state confirmation fail.
6. Activation: obtain token completion, destroy the client surface, and verify
   server activation-resource counters return to baseline. A returned token alone
   is not evidence that the compositor accepted it.

The client does not implement image-copy capture. Persistent recording must be
supplied by a separately tracked recorder, with frame progress and session-census
evidence. No recorder, absent management protocols, or an unobserved state is an
incomplete phase, never a PASS. Client unit tests and compilation do not replace
running these scenarios against the user's instrumented compositor.
