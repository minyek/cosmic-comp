# Building with / without VRAM instrumentation

Two committed branch pairs (same names in both repos). Toggling is a plain
`git checkout` in **both** repos — the instrumentation is committed, so it can't
smear across branch switches.

| Want | smithay branch | cosmic-comp branch |
|------|----------------|--------------------|
| **Clean** (production-like, no debug code) | `all-fixes` | `all-fixes` |
| **Instrumented** (SIGUSR1 census, clone tracer, GL counters) | `all-fixes-instrumented` | `all-fixes-instrumented` |

cosmic-comp's `Cargo.toml` on these branches patches `smithay = { path = "../smithay" }`,
so the two repos must sit side-by-side and be on the **same-named** branch.

```bash
cd /mnt/work/projects/cosmic-de

# --- clean build (no instrumentation) ---
git -C smithay     checkout all-fixes
git -C cosmic-comp checkout all-fixes
cd cosmic-comp && cargo build --release

# --- instrumented build (VRAM hunt) ---
git -C smithay     checkout all-fixes-instrumented
git -C cosmic-comp checkout all-fixes-instrumented
cd cosmic-comp && cargo build --release
```

Binary: `cosmic-comp/target/release/cosmic-comp`.

## What each branch contains

- **`all-fixes`** — the leak fixes only:
  - cosmic-comp: every VRAM-leak fix + the #2095 review-feedback fix, plus a
    "local build config" commit (smithay-bump API migrations + the `../smithay`
    patch). No debug code.
  - smithay: the 4 leak fixes (thread-safe userdata cache, free-PBO-on-error,
    clamp-shm-damage, multigpu texture cleanup).
- **`all-fixes-instrumented`** — `all-fixes` + one "do not merge" instrumentation
  commit (+ investigation docs on the cosmic-comp side). Adds the SIGUSR1 resource
  census, dmabuf clone/drop tracer, GL object counters, per-renderer cache probe.

## Upstream PR branches (do not build from these for testing)

- cosmic-comp: `vram-leak-fixes` (clean, pinned upstream smithay)
- smithay: `fix/threadsafe-userdata-caches`, `fix/free-pbo-on-exportmem-error`,
  `fix/clamp-shm-damage`, `fix/multigpu-texture-cleanup`
