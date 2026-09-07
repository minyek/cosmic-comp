use std::process::Command;

#[test]
fn rejects_missing_deadline_before_connecting() {
    let output = Command::new(env!("CARGO_BIN_EXE_vram-retest-client"))
        .output()
        .unwrap();
    assert!(!output.status.success());
    let event: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(event["event"], "error");
}

#[test]
fn unavailable_display_cannot_report_ready_or_complete() {
    let directory = tempfile::tempdir().unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_vram-retest-client"))
        .arg("2")
        .env_remove("WAYLAND_SOCKET")
        .env("XDG_RUNTIME_DIR", directory.path())
        .env("WAYLAND_DISPLAY", "absent-test-socket")
        .output()
        .unwrap();
    assert!(!output.status.success());
    let event: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(event["event"], "error");
}

#[test]
fn rejects_unbounded_deadlines() {
    for deadline in ["0", "3601", "-1", "invalid"] {
        let output = Command::new(env!("CARGO_BIN_EXE_vram-retest-client"))
            .arg(deadline)
            .output()
            .unwrap();
        assert!(!output.status.success(), "deadline {deadline}");
    }
}
