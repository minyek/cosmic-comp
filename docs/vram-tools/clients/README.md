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
| `fullscreen-target` | JSON includes `"output":"DP-1"`; named live wl_output v4 required | `output-enter` naming the target plus fullscreen state |
| `minimize` | Request xdg toplevel minimization | `managed-state` containing `1` |
| `unminimize` | COSMIC management capability `4` | `managed-state` without `1` |
| `sticky`, `unsticky` | COSMIC management capability `7` | `managed-state` with / without `4` |
| `focus` | COSMIC management capability `2`; own discovered toplevel only | `managed-state` containing `2`; pointer focus still needs input |
| `activate-token` | Commit xdg activation token referencing own surface | `activation-token-done`; census must prove server token acceptance and teardown |
| `pending-activate` | Keyboard-focused requester; create unmapped target surface and activate it with returned token | `pending-activation-submitted`, then `acknowledged` for `pending-activate-ready`, then positive pending Wayland census delta |
| `pending-destroy` | Destroy that unmapped target | `acknowledged`, then pending Wayland census returns to baseline |
| `x11-activate` | Keyboard-focused requester; obtain token, set owned X11 window `_NET_STARTUP_ID`, map it | `x11-map-submitted`, then `acknowledged` for `x11-activate-ready`; compositor insertion counter required |
| `x11-destroy` | Destroy the owned startup X11 window | `acknowledged`, compositor X11 pending-prune counter required |
| `destroy`, `quit` | Destroy lock, pending token, xdg toplevel and surface | `acknowledged`, `complete`; subsequent census must prove cleanup |

`submitted` means the request was issued. `acknowledged` follows a
`wl_display.sync` callback after that request; it proves request processing order,
not that the compositor honored an optional request or ran an idle callback.
`complete.detail.coverage_verdict` explicitly requires compositor/harness evidence.
Send one command and await its evidence before sending the next.

Activation requests require an observed `keyboard-enter` event, which supplies
the keyboard serial and seat used to validate the token. Pointer focus is not
the activation serial source. `keyboard-leave` invalidates this precondition.
Tokens remain inside the client and are not written to its JSON output.

`output` reports each bound output's name; `output-enter`/`output-leave` report
actual wl_surface membership for the main mapped helper surface. Hotplugged
outputs are bound as they appear. `output-removed` reports registry removal;
removed outputs cannot be fullscreen targets. Unnamed outputs or protocols below
wl_output version 4 cannot satisfy named-output evidence.

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
6. Pending Wayland activation: focus the requester, wait for keyboard enter,
   `pending-activate`, and wait for the post-activation sync. Require the pending
   Wayland census to rise before `pending-destroy`, then return to baseline.
   This uses the compositor's Focus activation policy path; other policies may
   decline activation of unmapped surfaces and must produce an incomplete phase.
7. Pending X11 activation: repeat `x11-activate`/`x11-destroy` with fresh focus
   and token validation each time. Require positive compositor insertion AND
   pruning counters. Normal mapping may consume the pending activation before
   destruction; that does not exercise dead-window pruning. If bursts cannot
   reach pruning, this phase remains incomplete and needs a controlled Xwayland
   map/association fixture. Display-sync acknowledgements do not synchronize
   XWM handling across the X11 and Wayland connections.

Token issuance alone does not exercise the pending-activation cleanup path.
The `activate-token` command is a token lifecycle probe, never a substitute for
either pending-activation scenario.

The client does not implement image-copy capture. Persistent recording must be
supplied by a separately tracked recorder, with frame progress and session-census
evidence. No recorder, absent management protocols, or an unobserved state is an
incomplete phase, never a PASS. Client unit tests and compilation do not replace
running these scenarios against the user's instrumented compositor.
