# cosmic-comp — project notes

## Fork workflow

This fork (`minyek/cosmic-comp`) follows the `fork-upstream-pr-workflow` skill.

- **Remotes.** `upstream` is `pop-os/cosmic-comp`, `origin` is the fork.
- **`master` is the derived fork main:** upstream plus the fork's own commits, linear, with no merge
  commit from upstream. `/sync-upstream` rebases it. It replaced the old local-only `all-fixes` branch,
  whose last state is the tag `backup/all-fixes-20261119T212530Z`. GitHub's "Sync fork" button and the
  scheduled `merge-upstream` workflow merge into `master`, so the workflow
  (`.github/workflows/sync-upstream.yml`) is disabled and removed; sync only with `/sync-upstream`.
- **Pull requests.** Upstream accepts them (PR #2500 is open). A fix is a `fix/<slug>` branch cut from
  `upstream/master`, one issue each, stable once posted. PR descriptions and update comments live in
  `/mnt/work/projects/cosmic-de/pr-descriptions/` (`cosmic-comp-*.md`); the draft for the open PR also
  sits in the `vram-leak-fixes` worktree (`pr2500-*.md`). The stack index, when branches stack, goes in
  that directory as `cosmic-comp-stack.md` (none exists yet: the open PR is independent). Posting,
  editing or commenting on an upstream PR needs the user's go-ahead.
- **Fork-only paths** (their own commits, never on a fix branch): `docs/`, `packaging/`, `CLAUDE.md`,
  and the fork's `README.md` and `build.rs` edits for the RPM build (`Add Fedora 45 RPM packaging`).
- **Gate.** Upstream's CI (`.github/workflows/ci.yml`), run detached through `arm-run` from the
  worktree: `cargo fmt --all -- --check`, `cargo clippy --all-features -- -D warnings`,
  `cargo check --no-default-features`, `cargo check --features debug`, `cargo test`.
- **Install after a change lands on `master`.** Build the RPM with `packaging/rpm/build-rpm.sh` (detached,
  from the main checkout; it builds committed `HEAD`), then the user installs it:
  `sudo dnf5 install ./target/rpm-build/cosmic-comp.rpm`. The agent cannot restart the compositor, so a
  landed change is not live until the user installs and restarts it (see Two-actor setup).

## Syncing from upstream

`/sync-upstream` rebases `master` onto `upstream/master`; there is no merge commit from upstream.
Resolve a conflict in the fork commit it belongs to. An adaptation the gate needs that belongs to one
commit is a `fixup!` commit folded in the sync worktree before the push
(`git rebase --autosquash upstream/master`; `git diff <tip before the fold> HEAD` must print nothing), so
the gated tree is the one pushed; one that spans several commits goes on top as its own commit. A
landing after a sync restacks its worktree branch onto the new `master`.

## VRAM leak work: hunt resolved, upstreaming in progress

The leak hunt itself is **resolved**: compositor-side leaks are fixed and the
residual is an NVIDIA driver leak (see the findings doc). Current phase is
validating the fixed builds and getting the fixes merged upstream.

### Branch / worktree layout

| Branch | Checkout | Purpose |
|---|---|---|
| `master` | main worktree | Derived fork main: `upstream/master` + the fork's commits not yet merged upstream. |
| `all-fixes-instrumented-invalidate` | `.worktrees/instr-invalidate` | The old `all-fixes` + census/GL-debug instrumentation + the `docs/vram-leak-*.md` docs. Instrumented builds and doc updates happen here. Still predates the pointer-constraints fix and keeps `path = "../smithay"` (it needs the instrumented smithay); restack onto `master` before the next validation round. |
| `vram-leak-fixes` | `.worktrees/pr2500-conflict` | Backs upstream PR pop-os/cosmic-comp#2500 (the leak fixes). |
| `fix-image-copy-capture-panics` | `.worktrees/image-copy-panics` | Standalone crash-fix PR: panics on image-copy capture failure paths. |

### Upstream status

- Both smithay PRs are **merged**. `Renderer::invalidate_caches` and the
  `GpuManager` cache methods are in Smithay master at `347b2b3`, which every
  local branch now pins (`[patch.crates-io] smithay = { git = ".../smithay.git",
  rev = "347b2b3" }`) — no more `path = "../smithay"`.
- cosmic-comp PR #2500 (leak fixes): rebased on `314fc670`, now also carrying
  the surface-thread join fix (its standalone branch/worktree are retired), and
  **ready to force-push**; the draft update comment is
  `.worktrees/pr2500-conflict/pr2500-update-comment.md`.
- Not yet submitted: the image-copy panics branch above, and the toplevel-handle
  weak-reference plus capture-session-teardown fixes, which live only in
  `master`'s squashed leak commit.
- Several earlier standalone fixes are already in `upstream/master`
  (corner_radius layer-surface check, pointer-edge clamp, GPU-reset renderer
  panic).
- `master` and `upstream/master` now pin the same smithay (`8a27023`). The one
  adaptation the newer smithay needed, `PointerConstraintRef::deactivate` /
  `remove_constraint` (its call site deadlocks unless the cursor hint is applied
  from an idle callback), is the fork commit `fix(pointer): apply the cursor hint
  from an idle callback`.

### Docs (tracked on the instrumented branch, not `master`)

- **`.worktrees/instr-invalidate/docs/vram-leak-findings.md`** — living log of
  what's confirmed, ruled out, and resolved. **Read this first** for current state.
- `.worktrees/instr-invalidate/docs/vram-leak-runbook.md` — the capture-and-analyse
  procedure (two-actor protocol, census-diff decision tree).
- `.worktrees/instr-invalidate/docs/vram-leak-investigation.md` — the diagnostic
  toolkit (SIGUSR1 census, `COSMIC_GL_DEBUG`, Tracy, heaptrack, dma-buf debugfs).
- `.worktrees/instr-invalidate/docs/vram-tools/BUILDING.md` — the worktree-based
  clean/instrumented build layout.

### Key constraints

- **Two-actor setup.** The compositor runs as the desktop user (`andy`, uid 1000);
  the agent runs as `agent` (uid 1001, no sudo, not in `systemd-journal`). The agent
  **cannot** signal the compositor or read its journal — the user runs anything that
  touches the running compositor and hands off via world-readable `/tmp` files
  (`chmod a+r`). See the runbook's "two-actor setup" section.
- **Instrumentation lives on the instrumented branch only:** `src/utils/vram_dump.rs`
  (SIGUSR1 census, logs at `warn!`), `src/utils/gl_debug.rs` (`COSMIC_GL_DEBUG=1`;
  `COSMIC_GL_DEBUG_SYNC=1` also captures a backtrace at each GL error's call site).
  `master` and the PR branches carry no instrumentation.
- **VRAM sampler:** `/tmp/vram-watch.sh` (run by the user) → `/tmp/vram.csv`.
