# Building the VRAM retest variants

| Variant | Compositor checkout | Smithay dependency |
| --- | --- | --- |
| PR only | `.worktrees/pr2500-conflict`, `vram-leak-fixes` | Upstream git pin `e3d461a` |
| Combined instrumented retest | `.worktrees/instr-invalidate`, `all-fixes-instrumented-invalidate` | Local instrumented Smithay worktree, based on `e3d461a` |
| Existing local combined branch | Main checkout, `all-fixes` | Its committed git pin; not refreshed by this retest task |

The instrumented compositor is based on PR commit `274fca13`. It also retains
the existing weak toplevel-handle references, capture-session teardown and
image-copy failure-path fixes from the previous combined build. These extras
are not all in PR #2500. A combined-build desktop PASS therefore does not, by
itself, establish a PR-only PASS.

Only the instrumented checkout uses `smithay = { path = "../smithay" }`.
Its sibling `.worktrees/smithay` symlink resolves to
`/mnt/work/projects/cosmic-de/smithay/.worktrees/instr-invalidate`.
Both working trees must be clean before identifying a release by git revisions.
The retest service additionally matches the running executable's SHA256 and
process start time against the requested build.

Build from the instrumented checkout:

```bash
arm-free
arm-run --wait --cpu-light --mutex cargo -m 14G -l vram-release -- \
  cargo build --locked --release \
  --manifest-path /mnt/work/projects/cosmic-de/cosmic-comp/.worktrees/instr-invalidate/Cargo.toml
cargo build --locked --manifest-path docs/vram-tools/clients/Cargo.toml
cargo build --locked --manifest-path docs/vram-tools/gpu-client/Cargo.toml
```

Fat LTO may take longer than an interactive tool's budget. In that case launch
the release build detached with `arm-run --resume-on-done` instead of `--wait`;
keep the absolute manifest path. The binary is `target/release/cosmic-comp` in
the instrumented checkout. This command does not install it.

The desktop user owns deployment and logout/login. Before starting the new
session, arrange an absolute, desktop-user-owned, mode-0700 directory in
`COSMIC_RETEST_CONTROL`. Unset `COSMIC_FAULT_CAPTURE_CONSTRAINTS`.
Then follow [RETEST.md](RETEST.md), using a fresh capture directory for each
normal, fault and assisted hardware suite. Do not send SIGUSR1 to an
unverified distro binary: its default action can terminate the compositor.

The DMA-BUF helper needs GBM development libraries and read/write access to each
requested render node. It requires linux-dmabuf protocol version 6 for explicit
sampling-device attribution; a shared-memory fallback cannot test GPU-client
registration and is deliberately not used.
