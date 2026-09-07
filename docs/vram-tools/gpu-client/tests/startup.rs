use std::process::Command;

fn client() -> Command {
    Command::new(env!("CARGO_BIN_EXE_vram-retest-gpu-client"))
}

#[test]
fn missing_nodes_cannot_report_ready() {
    let output = client().args(["--timeout", "1"]).output().unwrap();
    assert!(!output.status.success());
    let event: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(event["event"], "error");
}

#[test]
fn unavailable_display_cannot_report_imports_or_ready() {
    let directory = tempfile::tempdir().unwrap();
    let output = client()
        .args(["--timeout", "1", "--render-node", "/dev/dri/renderD128"])
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
fn deadlines_are_explicit_and_bounded() {
    for timeout in ["0", "3601", "bad", "-1"] {
        assert!(
            !client()
                .args(["--timeout", timeout, "--render-node", "/dev/dri/renderD128"])
                .output()
                .unwrap()
                .status
                .success()
        );
    }
    assert!(
        !client()
            .args(["--render-node", "/dev/dri/renderD128"])
            .output()
            .unwrap()
            .status
            .success()
    );
}
