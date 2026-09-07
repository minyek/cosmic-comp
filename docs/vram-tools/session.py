#!/usr/bin/env python3
"""Desktop-user census service with per-request acknowledgements."""

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

from census import censuses


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True) + "\n")
    temporary.chmod(0o644)
    temporary.replace(path)


def identity(pid, build):
    process = Path(f"/proc/{pid}")
    with (
        (process / "exe").open("rb") as running,
        Path(build).open("rb") as expected_build,
    ):
        actual = hashlib.file_digest(running, "sha256").hexdigest()
        expected = hashlib.file_digest(expected_build, "sha256").hexdigest()
    if actual != expected:
        raise ValueError("running executable differs from requested build")
    environment = dict(
        entry.split("=", 1)
        for entry in (process / "environ").read_text().split("\0")
        if "=" in entry
    )
    control = environment.get("COSMIC_RETEST_CONTROL", "")
    if not control or not Path(control).is_absolute():
        raise ValueError("running compositor requires absolute COSMIC_RETEST_CONTROL")
    stat = (process / "stat").read_text().rsplit(")", 1)[1].split()
    return {
        "pid": pid,
        "start_ticks": stat[19],
        "build_sha256": actual,
        "fault_control": control,
        "legacy_capture_fault": environment.get("COSMIC_FAULT_CAPTURE_CONSTRAINTS", ""),
    }


def request(root, verb, phase, label, timeout):
    session = json.loads((root / "session.json").read_text())
    message = {
        "request_id": str(uuid.uuid4()),
        "run_id": session["run_id"],
        "verb": verb,
        "phase": phase,
        "label": label,
    }
    filename = message["request_id"] + ".json"
    write_json(root / "requests" / filename, message)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        path = root / "acks" / filename
        if path.exists():
            reply = json.loads(path.read_text())
            if any(reply.get(key) != value for key, value in message.items()):
                raise ValueError("acknowledgement does not match exact request")
            if reply.get("status") != "ok":
                raise ValueError(reply.get("error", "collection failed"))
            print(json.dumps(reply))
            return
        time.sleep(0.05)
    raise TimeoutError(f"request {message['request_id']} timed out")


def journal(pid, cursor, count=None):
    command = [
        "journalctl",
        "--user",
        "-b",
        f"_PID={pid}",
        "--no-pager",
        "-o",
        "cat",
        f"--cursor-file={cursor}",
    ]
    if count is not None:
        command += ["-n", str(count)]
    return subprocess.run(
        command, check=True, capture_output=True, text=True, timeout=20
    ).stdout


def collect(root, session, message, cursor, timeout):
    os.kill(session["pid"], signal.SIGUSR1)
    deadline = time.monotonic() + timeout
    text = ""
    while time.monotonic() < deadline:
        text += journal(session["pid"], cursor)
        if "=== END VRAM/resource census ===" in text:
            break
        time.sleep(0.1)
    filename = "journal-" + message["request_id"] + ".txt"
    path = root / filename
    path.write_text(text)
    path.chmod(0o644)
    blocks = censuses(path)
    start = text.find("=== VRAM/resource census")
    end = text.find("=== END VRAM/resource census ===")
    if (
        len(blocks) != 1
        or start < 0
        or end < start
        or text.count("=== END VRAM/resource census ===") != 1
    ):
        raise ValueError("missing, duplicate or truncated census")
    sample = dict(
        session,
        **{key: message[key] for key in ("request_id", "phase", "label")},
        counters=blocks[0],
        journal=text,
        complete=True,
    )
    with (root / "samples.jsonl").open("a") as output:
        output.write(json.dumps(sample) + "\n")
    (root / "samples.jsonl").chmod(0o644)
    if message["verb"] == "snapshot":
        snapshot = root / ("snapshot-" + message["request_id"])
        snapshot.mkdir()
        for name in ("status", "maps"):
            (snapshot / name).write_text(
                Path(f"/proc/{session['pid']}/{name}").read_text()
            )
        (snapshot / "nvidia-smi.txt").write_text(
            subprocess.run(
                ["nvidia-smi"], check=True, capture_output=True, text=True, timeout=20
            ).stdout
        )


def serve(args):
    root = Path(args.directory)
    session = identity(args.pid, args.build)
    session["run_id"] = str(uuid.uuid4())
    root.mkdir(mode=0o755, parents=True, exist_ok=True)
    if (root / "session.json").exists():
        raise ValueError("capture directory already used; select a new run directory")
    for name in ("requests", "acks"):
        (root / name).mkdir(mode=0o777)
        (root / name).chmod(0o777)
    root.chmod(0o777)
    control = Path(session["fault_control"])
    control.mkdir(mode=0o700, exist_ok=True)
    if control.stat().st_uid != os.getuid() or control.stat().st_mode & 0o077:
        raise ValueError("fault directory must be owned by desktop user and mode 0700")
    if list(control.glob("arm-*")):
        raise ValueError("stale fault controls present")
    cursor = root / "journal.cursor"
    journal(args.pid, cursor, 0)
    write_json(root / "session.json", session)
    print(f"Session ready: {root} run={session['run_id']}", flush=True)
    seen = set()
    declared_manifest = None
    while True:
        stat = Path(f"/proc/{args.pid}/stat").read_text().rsplit(")", 1)[1].split()
        if stat[19] != session["start_ticks"]:
            raise ValueError("running process identity changed")
        for path in sorted((root / "requests").glob("*.json")):
            if path.name in seen:
                continue
            seen.add(path.name)
            message = json.loads(path.read_text())
            reply = dict(message, status="ok")
            try:
                if (
                    message["run_id"] != session["run_id"]
                    or path.stem != message["request_id"]
                ):
                    raise ValueError("request identity mismatch")
                if message["verb"] == "stop":
                    write_json(root / "acks" / path.name, reply)
                    return
                manifest = json.loads((root / "manifest.json").read_text())
                from evidence import validate_manifest

                validate_manifest(manifest)
                if declared_manifest is None:
                    declared_manifest = manifest
                elif manifest != declared_manifest:
                    raise ValueError("manifest changed after execution began")
                if any(manifest[key] != value for key, value in session.items()):
                    raise ValueError("manifest identity mismatch")
                if message["phase"] not in {
                    phase["name"] for phase in manifest["phases"]
                }:
                    raise ValueError("phase not declared before execution")
                if message["verb"] in ("census", "snapshot"):
                    collect(root, session, message, cursor, args.timeout)
                elif message["verb"] in (
                    "arm-cleanup",
                    "arm-config",
                    "arm-scanout",
                    "arm-capture-workspace",
                    "arm-capture-toplevel",
                ):
                    if manifest["mode"] != "fault":
                        raise ValueError("fault arming outside fault suite")
                    (control / message["verb"]).touch(exist_ok=False)
                else:
                    raise ValueError("unknown request verb")
            except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
                reply.update(status="error", error=str(error))
            write_json(root / "acks" / path.name, reply)
        time.sleep(0.1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    client = commands.add_parser("request")
    client.add_argument("directory")
    client.add_argument("verb")
    client.add_argument("phase")
    client.add_argument("label")
    client.add_argument("--timeout", type=float, default=60)
    server = commands.add_parser("serve")
    server.add_argument("directory")
    server.add_argument("--build", required=True)
    server.add_argument("--pid", required=True, type=int)
    server.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()
    try:
        if args.command == "request":
            request(
                Path(args.directory), args.verb, args.phase, args.label, args.timeout
            )
        else:
            serve(args)
        return 0
    except (OSError, ValueError, TimeoutError, subprocess.SubprocessError) as error:
        print(f"INCOMPLETE: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
