# VRAM leak investigation toolkit

Hooks added to this branch for hunting the residual VRAM growth that survives
the cleanup work in `42760e60`, `bcaf8f20`, `58308a2d`, and `738e6203`.

For the step-by-step capture-and-analyse procedure (who runs what, and how the
census deltas are mapped to a leaking subsystem), see the companion
[`vram-leak-runbook.md`](./vram-leak-runbook.md). For what the hunt has actually
established so far — confirmed facts, ruled-out hypotheses, open leads — see
[`vram-leak-findings.md`](./vram-leak-findings.md).

## 1. SIGUSR1 resource census

Always compiled in. Send `SIGUSR1` to dump per-output and per-workspace counts
of capture sessions, offscreen renderbuffers, pending frames, zoom states, and
suspect pending lists to the log.

```sh
kill -USR1 $(pgrep cosmic-comp)
```

Inspect the log (e.g. `journalctl --user -u cosmic-session -f` under
cosmic-session, or stderr if launched standalone) for blocks like:

```
=== VRAM/resource census (SIGUSR1) ===
pending_windows=0, pending_layers=0, pending_activations=0, override_redirect_windows=0, idle_inhibiting_surfaces=0
outputs=1
  output Some("DP-1"): sessions=2, cursor_sessions=1, pending_frames=0, offscreen=2, zoom=false
workspaces=4, minimized_windows=0, ws_sessions=0, ws_cursor_sessions=0, ws_offscreen=0
toplevels=7, surface_sessions=0, surface_cursor_sessions=0, surface_offscreen=0
TOTALS (outputs only): sessions=2, cursor_sessions=1, pending_frames=0, offscreen_renderbuffers=2, output_zoom_states=0
```

**Workflow:** dump → exercise the suspect flow (e.g. open/close a screencast,
toggle zoom 50×, open/close many windows) → dump again. Any count that grew
identifies a leaking container. `offscreen_renderbuffers` growth is the
clearest VRAM signal — each one is a GLES renderbuffer attached to a dead
session.

Source: `src/utils/vram_dump.rs`. Add fields by extending `dump_shell()`.

## 2. GL_KHR_debug callback (env-gated)

Enable with `COSMIC_GL_DEBUG=1`. Optionally `COSMIC_GL_DEBUG_SYNC=1` to make
the callback synchronous (slower; backtraces at the GL call site are
meaningful).

```sh
COSMIC_GL_DEBUG=1 COSMIC_GL_DEBUG_SYNC=1 cosmic-comp
```

The NVIDIA driver emits `Buffer detailed info`, `Texture state`, and
`Framebuffer detailed info` notifications on most allocate/delete calls. Pipe
to a file and sum allocate vs. delete by GL object type:

```sh
grep -E '^.*GL: (Buffer|Texture|Framebuffer|Renderbuffer)' run.log \
  | awk '{print $NF}' | sort | uniq -c | sort -n
```

Anything generated more often than deleted is your leak class.

Source: `src/utils/gl_debug.rs`. Zero-cost when the env var is unset.

## 3. Tracy GPU profiling (existing)

Already wired via the `profile-with-tracy-gpu` Cargo feature. Build once:

```sh
cargo build --release --features profile-with-tracy-gpu
```

Run a Tracy server (`tracy-profiler`) on the same machine or via TCP. GPU
zones show up alongside CPU zones and the memory pane tags Rust allocations
by call site. For long sessions, expect Tracy buffers to be the limiting
factor — restart between runs.

## 4. CPU-side heap profiling (no build change)

Most VRAM leaks here have been CPU-side handles holding GL resources alive.
Confirm with `heaptrack`:

```sh
heaptrack ./target/release/cosmic-comp
# ... reproduce the leak workflow ...
# stop the compositor; analyze:
heaptrack_gui heaptrack.cosmic-comp.<pid>.zst
```

If `heaptrack` shows CPU heap flat while `nvidia-smi --query-compute-apps`
shows VRAM climbing, the leak is GPU-side with no Rust handle — go to (2).

## 5. DMA-BUF debugfs (kernel-dependent)

`/sys/kernel/debug/dma_buf/bufinfo` lists every dma-buf with size, exporter,
and attached devices. Requires `CONFIG_DMABUF_DEBUG=y` and `debugfs` mounted.
Not present on stock Fedora kernel as of 2026-05; rebuild kernel to use.

When available, this directly answers "is the leak in client-passed DMA-BUFs
the compositor never released?" — a common screencopy/screencast leak shape.

## Suggested investigation order

1. `nvidia-smi --query-compute-apps=pid,used_memory --format=csv -l 5` to
   confirm the growth rate and identify which workflow triggers it.
2. SIGUSR1 dumps before/after that workflow. Look for monotonic growth in any
   counter. If found → fix in cosmic-comp.
3. If counts are stable but VRAM still grows → enable `COSMIC_GL_DEBUG=1` and
   re-run; look for GL allocator/deleter imbalance. Likely a smithay-internal
   leak that needs to be upstreamed.
4. `heaptrack` in parallel with (2)/(3) — independent signal.
