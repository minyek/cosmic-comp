// SPDX-License-Identifier: GPL-3.0-only
use std::process::Command;

fn main() {
    println!("cargo:rerun-if-env-changed=GIT_HASH");

    if let Ok(output) = Command::new("git").args(["rev-parse", "HEAD"]).output()
        && output.status.success()
    {
        let git_hash = String::from_utf8_lossy(&output.stdout);
        println!("cargo:rustc-env=GIT_HASH={}", git_hash.trim());
    }
}
