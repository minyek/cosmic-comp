# VRAM retest implementation validation

## Scope

The combined instrumented compositor source is committed at `17098d91`, based on
PR #2500 commit `274fca13`. Its instrumented Smithay dependency is `f1194d076`,
based on upstream `e3d461a`. Subsequent harness and documentation commits do not
change that compositor source. Existing non-PR weak toplevel-handle references,
capture-session teardown and image-copy failure-path fixes remain included.

Main `all-fixes`, the PR branch and the deployed compositor were not changed.
Nothing was pushed or installed. Historical captures, data under `/mnt/data`,
and archived artifacts were preserved.

## Verified offline

- Compositor format check, Clippy with all features and warnings denied, all
  four CI feature configurations, and all-feature tests: passed; 13 tests.
- Smithay queue channel and sequence tracker: 11 standalone tests passed,
  including out-of-order renderer completion, receiver retirement, concurrent
  send/retirement and keeping a delivered request outstanding through cleanup.
- Strict harness: 59 offline tests passed, including malformed capture files,
  first-sample queue consistency, cross-renderer stuck-request masking, pointer
  coordinates, scoped generations and expected injected restore recovery.
- Wayland/X11 helper: 10 offline tests passed; Clippy with warnings denied passed.
- DMA-BUF helper: 10 offline tests passed; Clippy with warnings denied and its
  locked executable build passed. No render node was opened during these tests.
- The repository does not contain `.claude/run-checks.sh`; the checked-in CI
  matrix was used instead.
- The `proc-macro-error2` 2.0.1 dependency emits the existing future-compatibility
  warning about its private `proc_macro` re-export. The compiler accepts it today;
  it is unrelated to the retest changes and remains dependency maintenance work.

No offline test is evidence that a desktop protocol,
physical hotplug or a driver's allocation cleanup actually ran.

## Release artifact

The final `cargo build --locked --release` passed against the source revisions
above in 5 minutes 34 seconds. The isolated job reported a 4 GiB peak. Neither
this artifact nor the earlier interim binary was installed.

```text
target/release/cosmic-comp
SHA256 f28836339f76031ed62e420c72ab2b3f3c0b59cdb105da6a0f864f98c9e54548
```

The Wayland helper binary was built with its locked standalone manifest.

## Measurement corrections and blast radius

| Finding | Affected surface | Required action |
| --- | --- | --- |
| Unknown checks, missing boundaries or completion accepted | Historical harness PASS coverage | Rerun affected phases with the strict manifest; retain old raw captures |
| Dead entries overwritten or prefixed queue lines missed | Parsed resource verdicts | Inspect raw historical logs; do not infer complete coverage from missing fields |
| Census refresh skipped or retired renderer labels retained | Cache snapshots and topology verdicts | Collect current on-request censuses with thread retirement cleanup |
| Uncounted/discarded destruction requests | Queue totals across renderer retirement | Use tracked enqueue/drain/discard accounting; discard is not proof of explicit GL deletion |
| Aggregate drain count hides another renderer's stuck request | Queue-progress conclusions | Require exact oldest-outstanding sequence evidence; totals alone cannot be rescored into it |
| Token issuance never activates an unmapped target | Pending-activation coverage | Require actual pending Wayland target and pruning; X11 requires insertion and pruning counters |
| Pointer callback counts do not prove correct coordinates | Hint/focus/leave coverage | Check observed global hint position and no-warp transitions |
| Sticky surfaces omitted from census traversal | Sticky capture-resource observations | Rerun sticky phases with the expanded surface traversal |
| SHM window movement does not register a DMA-BUF client | GPU-client cleanup coverage | Import actual DMA-BUFs on the required devices through one owned connection |
| Wire-local coordinates quantized before global comparison | Interim matching-hint oracle, never used in a desktop run | Use independently observed layout origin and client scale, not a tolerance |
| Zoom/alternating-shape activity does not prove shake or unchanged-shape behavior | Cursor coverage | Require dedicated intervals and a separate shake phase with observed magnification and expiry |

These findings qualify the affected harness conclusions. They do not retract
the independent NVIDIA reproducer or automatically invalidate every historical
leak-hunt finding. No production process, running desktop, shipped binary or
cached dataset was modified by the corrections.

## Desktop validation still required

Follow [RETEST.md](../vram-tools/RETEST.md). Run normal, one-shot fault and
operator-assisted hardware suites in fresh directories against the exact new
binary. Hardware absence and unobserved activation pruning are incomplete
coverage, not skips or PASS. The desktop user owns installation, session restart,
VT switching, locking and physical output changes.

This host currently exposes only `/dev/dri/renderD128`; no multi-GPU desktop
result can be established here. Individual hardware phases can still run with
their own manifests, but they do not constitute full hardware-suite coverage.

The compositor unit tests cover the hint-selection predicate, not a same-surface
queued-idle serial race. This revision handles destruction directly through the
updated Smithay callback; no queued-idle race coverage is claimed.
X11 normal mapping can consume an activation before client destruction; a run
that never increments the pruning counter has not tested that cleanup branch.
