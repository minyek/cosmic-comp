// SPDX-License-Identifier: GPL-3.0-only

use std::{
    path::Path,
    sync::atomic::{AtomicU64, Ordering},
};

fn consume_arm(path: &Path) -> bool {
    std::fs::remove_file(path).is_ok()
}

pub fn fault(kind: &str) -> bool {
    std::env::var_os("COSMIC_RETEST_CONTROL")
        .is_some_and(|root| consume_arm(&Path::new(&root).join(format!("arm-{kind}"))))
}

pub fn armed(kind: &str) -> bool {
    std::env::var_os("COSMIC_RETEST_CONTROL")
        .is_some_and(|root| Path::new(&root).join(format!("arm-{kind}")).is_file())
}

macro_rules! counters {
    ($($name:ident),+ $(,)?) => {
        $(pub static $name: AtomicU64 = AtomicU64::new(0);)+

        pub fn snapshot() -> String {
            [$(format!("{}={}", stringify!($name).to_ascii_lowercase(),
                $name.load(Ordering::Relaxed))),+].join(" ")
        }
    };
}

counters!(
    CLEANUP_REQUESTS,
    CLEANUP_ATTEMPTS,
    CLEANUP_SUCCESSES,
    CLEANUP_FAILURES,
    CLEANUP_INACTIVE,
    CONFIG_FAULTS,
    CONFIG_INVALIDATIONS,
    CONFIG_ERRORS_PRESERVED,
    SCANOUT_FAULTS,
    SCANOUT_INVALIDATIONS,
    SCANOUT_ERRORS_PRESERVED,
    CURSOR_SHAPE_CHANGES,
    CURSOR_CACHE_HITS,
    CURSOR_CACHE_MISSES,
    CURSOR_FRAMES_EVICTED,
    CURSOR_MAGNIFIED_EVICTED,
    OUTPUT_REMOVALS,
    LOCK_SURFACE_REMOVALS,
    CLIENT_DISCONNECTS,
    CLIENT_GPU_REMOVALS,
    CLEANUP_PENDING,
    POINTER_HINT_APPLIED,
    POINTER_HINT_REJECTED,
    POINTER_CONSTRAINT_LEAVES,
    CAPTURE_WORKSPACE_FAULTS,
    CAPTURE_TOPLEVEL_FAULTS,
    X11_ACTIVATIONS_INSERTED,
    X11_ACTIVATIONS_PRUNED,
    WAYLAND_ACTIVATIONS_PRUNED,
);

pub fn add(counter: &AtomicU64, amount: usize) {
    counter.fetch_add(amount as u64, Ordering::Relaxed);
}

pub fn set(counter: &AtomicU64, value: usize) {
    counter.store(value as u64, Ordering::Relaxed);
}

#[derive(Debug)]
pub struct InjectedFault(pub &'static str);

impl std::fmt::Display for InjectedFault {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "Injected {} failure", self.0)
    }
}

impl std::error::Error for InjectedFault {}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fault_token_is_consumed_exactly_once() {
        let root = std::path::Path::new("/mnt/logs/temp")
            .join(format!("cosmic-retest-token-{}", std::process::id()));
        std::fs::create_dir_all(&root).unwrap();
        let token = root.join("arm-cleanup");
        std::fs::write(&token, b"test").unwrap();
        assert!(consume_arm(&token));
        assert!(!consume_arm(&token));
        std::fs::remove_dir(&root).unwrap();
    }

    #[test]
    fn missing_token_does_not_inject_a_fault() {
        assert!(!consume_arm(std::path::Path::new(
            "/nonexistent-retest-token"
        )));
    }
}
