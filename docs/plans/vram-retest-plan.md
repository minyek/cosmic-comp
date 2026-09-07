# VRAM retest implementation plan

Approved scope: the preceding harness audit and user request to fix all recommended issues.

## Design and tradeoffs

Use the rebased PR as the reference tree, overlay observation-only instrumentation,
and explicitly list any separate capture fix needed for the fault round. Preserve
backup refs. Updating the old mixed branch without identifying its functional delta
would obscure which code the results validate. Fault controls belong only on the
instrumented branch and must count actual execution; a helper/client must prove
protocol events rather than infer success from generic GL activity.

Require complete manifests, phase-local evidence, exact request acknowledgements,
and explicit completion. Historical incomplete captures fail closed and remain
available for analysis. Separate normal, injected-fault, and assisted hardware
rounds; hardware absence must never become a full PASS. Tests must run against
synthetic fixtures before the real desktop is touched.

## Tasks and dependencies

- [x] Back up and rebase instrumented Smithay onto e3d461a and compositor onto
  PR 274fca13. Resolve instrumentation without replacing cleanup semantics.
- [ ] Wave 1A, harness: docs/vram-tools/{census.py,drive.sh,session.sh,preflight.sh},
  new harness utilities/tests. Pin false passes first, then implement strict run
  evidence, transport, safety restoration, suite orchestration and hardware phases.
- [ ] Wave 1B, clients: docs/vram-tools/clients/** only. Build a standalone Wayland
  test client for pointer constraints, cursor shapes, minimized variants and
  activation teardown; deterministic JSON results and bounded waits. No desktop run.
- [ ] Wave 1C, instrumentation: src/** and Cargo files only. Add scoped fault
  controls/counters and resource census for lock teardown, cursor eviction,
  scheduling/retry/invalidation, activation and multi-GPU client cleanup.
- [ ] Merge isolated wave branches serially after targeted tests/review. Wire
  client/census contracts and add integration fixtures before full validation.
- [ ] Run harness tests, client build, compositor CI matrix, then instrumented
  release build through arm-run. Do not install or run interactive desktop tests.
- [ ] Update building/process/findings docs with exact revisions, scope, runnable
  commands and required user-assisted hardware validation. Preserve old measurements
  but retract unsupported coverage, no push or external posting.

Wave tasks have no shared files and each implementer uses an isolated worktree.
The orchestrator performs final integration and the full gate once. Functional
offline tests are separate from deliberate timing-sensitive desktop rounds.
