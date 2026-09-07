# VRAM capture and regression tools

For the current full retest, use [BUILDING.md](BUILDING.md), then
[RETEST.md](RETEST.md). Run desktop operations as the desktop user; the agent
cannot signal that user's compositor or read its journal.

- `preflight.sh`: requires an exact PID, matching build SHA256 and protocol helper.
- `session.sh`: collects complete censuses with exact request acknowledgements,
  process identity and incremental journal evidence.
- `drive.sh`: declares and drives separate normal, fault and assisted hardware
  suites. Incomplete scenarios fail; each capture directory is single-use.
- `census.py verdict`: validates the immutable manifest, completion and phase-local
  evidence. `census.py diff` also reads historical captures without granting PASS.
- `clients/`: controlled protocol clients; their exit status alone is not coverage.

Fault tests use one-shot runtime controls. Do not enable the persistent legacy
`COSMIC_FAULT_CAPTURE_CONSTRAINTS` environment variable for these suites.

For exploratory investigation, see the [leak runbook](../vram-leak-runbook.md).
`vram-watch.sh` is a separate long-soak sampler; `leak-snapshot.sh` captures
point-in-time process/driver diagnostics. Do not run other SIGUSR1 samplers during
a strict retest: duplicate or unsolicited censuses invalidate request evidence.
`reverse-ref-scan.py` checks reference patterns from the original leak hunt.
