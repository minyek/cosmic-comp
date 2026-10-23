# Project check result

`just check --offline --frozen` first exited 101 because the default Cargo
cache lacked Smithay revision `8a270232` (journal lines 3035533–3035535).
After the RPM build populated `target/rpm-build/cargo-home`, the same check
completed with `rc=0` and `result=clean` in 2 seconds. The retry set both
`CARGO_HOME` and `CARGO_TARGET_DIR` under `target/rpm-build`, so it changed no
system configuration and kept generated files inside the project build tree.
Existing pedantic Clippy warnings remain.
