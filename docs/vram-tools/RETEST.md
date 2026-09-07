# Retest harness

Each capture directory describes one immutable declared suite and one compositor
process. Start a fresh directory for each normal, fault, or assisted hardware run.
The compositor must have an absolute `COSMIC_RETEST_CONTROL` directory owned by
the desktop user, mode 0700. Unset `COSMIC_FAULT_CAPTURE_CONSTRAINTS`; the fault
suite arms individual runtime injections through the desktop-user service.

Run preflight and the service as the desktop user:

```bash
export COMPOSITOR_PID=<exact-pid>
export CTL=/mnt/logs/temp/vram-normal-<unique-name>
bash docs/vram-tools/preflight.sh /absolute/path/to/cosmic-comp \
  --pid "$COMPOSITOR_PID" --suite normal \
  --client /absolute/path/to/vram-retest-client
bash docs/vram-tools/session.sh /absolute/path/to/cosmic-comp
```

From another terminal, with the same `CTL` and desktop Wayland/input environment:

```bash
bash docs/vram-tools/drive.sh normal --output DP-2
python3 docs/vram-tools/census.py verdict "$CTL"
```

Use `fault` or `hardware` in separate fresh sessions. Hardware also requires
repeated `--render-node /dev/dri/renderD...` arguments on distinct physical GPUs
and the standalone `gpu-client` executable (`--gpu-client` overrides its path).
The GPU helper uses one Wayland connection and DMA-BUF v6 sampling-device hints
to request imports on every specified GPU. The driver checks all imports were
accepted and requires GPU client references to appear and be retired. Physical
disconnect selects the render node belonging to the target connector and holds
its GPU helper across the unplug. The visible SHM helper proves output membership
only; it cannot populate the compositor's DMA-BUF client registry.
Missing multi-GPU hardware or DMA-BUF protocol support is incomplete.
`all` aliases `normal`;
`faultall` aliases `fault`. Individual phase names are also accepted, with their
own declared scope. `--rounds`, `--settle`, `--client`, and `--directory` configure
the driver. Hardware requires an operator and a terminal. It asks for physical
disconnect/reconnect, VT deactivation/reactivation, and a locked-session output
cycle. A separate `locked-disconnect` phase takes locked-state censuses before
physical unplug, while disconnected, and after reconnect; each step has a
15-second operator window. Missing hardware, tools, protocol events, counters, or acknowledgements
leave an incomplete run and return nonzero.

Normal rounds include owned protocol clients, minimize/sticky/fullscreen teardown,
activation tokens, cursor shape changes with zoom and expiration, pointer-hint
constraints, screenshots, and persistent `wf-recorder` capture. Recording requires
`ffprobe` to validate nonempty output. The selected output must be connected beside
another display. Output configuration is saved as KDL and restored even on failed
commands or interrupted driver execution. Custom shortcuts are never overwritten;
workload cleanup targets only child processes created by this driver.

The service writes `session.json` with run UUID, PID/start ticks, binary SHA256,
fault-control path and legacy fault environment. Before the first phase the driver
writes `manifest.json`; the service pins it on the first request. Each request has
its own UUID and exact acknowledgement, and each completed sample carries the run
and process identity, phase, label, complete census and incremental PID-filtered
journal. `completion.json` remains incomplete until every declared phase and the
final snapshot acknowledge successfully. The verifier rejects absent or malformed
contracts, undeclared checks, missing/duplicate/out-of-order boundaries, mismatched
identities, incomplete counters and unobserved workload activity.

Queue liveness requires the exact oldest outstanding enqueue ticket to advance
past the earlier submitted-ticket watermark, or the outstanding set to become
empty. Aggregate completion counts cannot establish this because later work on
another renderer can complete while the oldest work remains stuck. Context-retirement discard deltas are
reported separately because they do not establish explicit GL deletion. Current
expected surface threads come from the census at each checkpoint. Minimized state
must return to each phase's baseline. Generic idle GL churn does not prove an
otherwise unobserved protocol action.

Matching cursor-hint targets use the focused surface's census geometry and client
scale: `focus_origin + hint / client_scale`. Wire pointer coordinates are diagnostic
only because their 24.8 precision differs from the compositor's global coordinates.
No-warp checks require exact unchanged global coordinates. Same-shape cursor
checkpoints require zero shape changes and advancing cache hits within that interval.
The separate shake phase drives rapid alternating 200-pixel motions, requires
observed shaking and magnification, then waits 15 seconds and triggers a render
before checking expiration. Missing observations fail the phase.

Runtime fault requests create owned tokens. Service teardown and explicit driver
disarm requests remove only those tokens whose inode and request UUID still match;
unrelated or replacement arm files are preserved. Single fault phases use the
same fault-mode catalog as the full fault suite.

Output restoration retries the exact saved KDL once after a command failure.
A fault phase may continue only when its own census proves exactly one injected
failure and one preserved-error event; unexpected errors remain incomplete even
if the recovery restore succeeds. X11 activation uses real startup tokens and
owned mapped/destroyed X11 windows; both insertion and pruning counters must
advance. Successful token consumption by normal mapping does not prove pruning.

## Validation and limits

```bash
python3 -m unittest discover -s docs/vram-tools/tests -v
bash -n docs/vram-tools/{drive,session,preflight}.sh
```

Offline tests establish parser/verdict and transport/restoration behavior. They
do not substitute for the three desktop rounds, multi-GPU hardware or compositor
tests. A PASS applies only to the phases in its manifest. Historical captures can
still be examined with `census.py diff`, but they lack the new execution contract
and cannot establish full-suite PASS.

The corrected false-PASS paths affect historical harness conclusions: unknown
expectations, absent phase boundaries, retained minimized windows, overwritten
dead-cache counts and unparsed prefixed queue lines. Re-run affected workflows
with this harness before relying on their coverage. Existing captures are retained;
this change does not install, signal, or change the running compositor, production
data, cached artifacts or previously shipped fixes.
