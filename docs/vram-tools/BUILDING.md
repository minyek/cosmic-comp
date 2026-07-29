# Building with / without VRAM instrumentation

Two committed variants, each with its own pair of checkouts. The instrumentation
is committed rather than applied on top, so it can't smear from one variant into
the other.

| Want | cosmic-comp checkout (branch) | smithay checkout (branch) |
|------|-------------------------------|---------------------------|
| **Clean** (production-like, no debug code) | `cosmic-comp` (`all-fixes`) | `smithay` (`vram-leak-fixes`) |
| **Instrumented** (SIGUSR1 census, clone tracer, GL counters) | `cosmic-comp/.worktrees/instr-invalidate` (`all-fixes-instrumented-invalidate`) | `smithay/.worktrees/instr-invalidate` (`all-fixes-instrumented-invalidate`) |

cosmic-comp patches `smithay = { path = "../smithay" }`, which resolves to the
sibling `smithay` checkout for the clean build and, for the instrumented build,
to `cosmic-comp/.worktrees/smithay` — a symlink pointing at smithay's
`instr-invalidate` worktree. Each variant therefore builds without disturbing
the other, and neither needs a branch switch.

```bash
# --- clean build (no instrumentation) ---
cd /mnt/work/projects/cosmic-de/cosmic-comp && cargo build --release

# --- instrumented build (VRAM hunt) ---
cd /mnt/work/projects/cosmic-de/cosmic-comp/.worktrees/instr-invalidate && cargo build --release
```

Binary: `<checkout>/target/release/cosmic-comp`.

The release profile uses fat LTO, so a full build is long enough to want
`arm-run`; see the root `CLAUDE.md` for the launch-detached-and-watch pattern.

## What each variant contains

- **Clean** — the leak fixes only:
  - cosmic-comp: every VRAM-leak fix + the #2095 review-feedback fix, plus a
    "local build config" commit (smithay API migrations + the `../smithay`
    patch). No debug code.
  - smithay: `Renderer::invalidate_caches`, which drops every cached import
    across the gles, glow, pixman and multigpu renderers.
- **Instrumented** — the clean variant plus "do not merge" instrumentation
  commits (and the investigation docs on the cosmic-comp side). Adds the SIGUSR1
  resource census, dmabuf clone/drop tracer, GL object counters, the
  per-EGLImage allocation-site registry, and the per-renderer cache probe.

## Upstream PR branches (do not build from these for testing)

- cosmic-comp: `vram-leak-fixes` (clean, pinned to an upstream smithay revision)
- smithay: `renderer/invalidate-caches`
