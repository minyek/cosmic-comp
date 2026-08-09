// SPDX-License-Identifier: GPL-3.0-only

//! On-demand faults for capture paths a live desktop cannot reach.
//!
//! `constraints_for_output` and `constraints_for_toplevel` fail only when an output has no
//! current mode or an offscreen renderer cannot be created. Every output carries a mode
//! once `apply_config_for_outputs` has run, so a scripted workload never reaches the
//! removal-and-stop branches behind those failures, and they ship without coverage.
//!
//! `COSMIC_FAULT_CAPTURE_CONSTRAINTS=1` fails the constraints query made while rendering a
//! frame, and forces every frame down the constraint-mismatch branch, so an ordinary
//! capture client drives those branches. The query made from `capture_constraints` when a
//! session is created is spared: failing there stops the session before any frame arrives,
//! and the frame path is the one carrying the code under test.
//!
//! Every fault is logged, because in a census "the fault never fired" and "the path stayed
//! clean" are the same picture.

use std::sync::OnceLock;

use tracing::warn;

use super::env::bool_var;

const CAPTURE_CONSTRAINTS: &str = "COSMIC_FAULT_CAPTURE_CONSTRAINTS";

pub fn capture_constraints_armed() -> bool {
    static ARMED: OnceLock<bool> = OnceLock::new();
    *ARMED.get_or_init(|| bool_var(CAPTURE_CONSTRAINTS).unwrap_or(false))
}

/// Run `query`, or fail it in place when the capture fault is armed.
pub fn capture_constraints<T>(scope: &str, query: impl FnOnce() -> Option<T>) -> Option<T> {
    if capture_constraints_armed() {
        warn!("Failing screencopy constraints for {scope} (fault injection)");
        return None;
    }
    query()
}

/// Report armed faults at startup, so a round whose environment never reached the
/// compositor is distinguishable from one whose faults simply never fired.
pub fn report_armed() {
    if capture_constraints_armed() {
        warn!("Fault armed: {CAPTURE_CONSTRAINTS} — screencopy frames will fail their constraints");
    }
}
