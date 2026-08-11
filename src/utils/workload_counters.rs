// SPDX-License-Identifier: GPL-3.0-only

//! Counters that evidence a driven workload reached the compositor.
//!
//! A regression phase passes on flat resource counters, which is also what a phase that
//! never ran produces, so each phase must separately show that its workload arrived. GL
//! churn cannot carry that proof: an idle desktop creates textures and EGL images at rates
//! comparable to the quieter phases, so a delta threshold over any of them scores elapsed
//! time rather than work. These counters move only when the corresponding input actually
//! reaches the compositor, which is the claim the phases need.

use std::sync::atomic::{AtomicU64, Ordering};

static POINTER_MOTIONS: AtomicU64 = AtomicU64::new(0);
static WORKSPACE_ACTIVATIONS: AtomicU64 = AtomicU64::new(0);
static ZOOM_CHANGES: AtomicU64 = AtomicU64::new(0);

pub struct WorkloadCounters {
    pub pointer_motions: u64,
    pub workspace_activations: u64,
    pub zoom_changes: u64,
}

pub fn record_pointer_motion() {
    POINTER_MOTIONS.fetch_add(1, Ordering::Relaxed);
}

pub fn record_workspace_activation() {
    WORKSPACE_ACTIVATIONS.fetch_add(1, Ordering::Relaxed);
}

/// Counted rather than read from `OutputZoomState`, whose presence saturates: smithay's
/// `UserDataMap` has no removal API, so the state and the census `zoom=` flag stay true
/// once zoom has been used at all, and neither can evidence a later zoom.
pub fn record_zoom_change() {
    ZOOM_CHANGES.fetch_add(1, Ordering::Relaxed);
}

pub fn workload_counters() -> WorkloadCounters {
    WorkloadCounters {
        pointer_motions: POINTER_MOTIONS.load(Ordering::Relaxed),
        workspace_activations: WORKSPACE_ACTIVATIONS.load(Ordering::Relaxed),
        zoom_changes: ZOOM_CHANGES.load(Ordering::Relaxed),
    }
}
