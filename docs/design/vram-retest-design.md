# VRAM retest design

Use the PR branch as the reference base and preserve the existing combined-build
extras explicitly. Dropping those extras would simplify attribution but stop
testing all of the user's local fixes. Keeping an unidentified old mixed base
would preserve behavior but obscure which upstream revision was tested.

A desktop-user service owns signalling, incremental journal capture and one-shot
fault controls. The driver declares its suite before submitting UUID-addressed
requests. Offline verdicts check process identity, boundaries, required counters,
actual activity, teardown and completion. This adds structured evidence and tests
but makes older captures analysis-only; permissive backward-compatible PASS
would preserve the false-positive paths being fixed.

Controlled protocol clients own their surfaces and expose observed protocol
events. Compositor counters and coordinates decide whether an operation happened;
a sync acknowledgement proves ordering, not successful activation or a correct
cursor warp. Existing compositor unit tests pin matching focus, unrelated focus,
unrelated hint surfaces and missing pointer focus; they are not an event-loop
integration test for a queued-idle race.

Instrumentation lives in retest/registry collaborators. Fault hooks run at the
real error paths and preserve the returned error. Queue tracking assigns sequence
numbers across independent channels and records outstanding requests; aggregate
counts remain diagnostic only. This costs instrumentation-only memory and locking
but avoids mistaking unrelated renderer progress for cleanup of a stuck request.

Normal, fault and assisted hardware suites have independent manifests. Hardware
requirements cannot be inferred from a successful software output toggle. Offline
functional tests remain fast; load-sensitive desktop timing is exercised only
in deliberate validation rounds.

The requirements-to-design crosscheck is performed inline: identity and completion
belong to the service/verifier, observed behavior to clients plus census, safety
restoration to the driver/service, and scope/invalidations to the guides and
findings log. No design-crosscheck skill is available in this session.
