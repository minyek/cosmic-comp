#!/usr/bin/env python3
"""Declare and drive normal, fault and assisted hardware retest suites."""

import argparse
import contextlib
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from evidence import validate_manifest, verdict
from session import request, write_json

TOOLS = Path(__file__).resolve().parent


def delta(counter, minimum=1):
    return {"kind": "delta", "counter": counter, "minimum": minimum}


def retained(counter):
    return {"kind": "return", "counter": counter}


def peak(counter):
    return {"kind": "peak_above", "counter": counter, "minimum": 1}


def plan(mode, rounds):
    normal = {
        "selftest": [peak("ws_sessions")],
        "monitors": [{"kind": "generations"}],
        "apps": [peak("toplevels"), retained("toplevels")],
        "capture": [delta("raw.renderbuffers_created", rounds)],
        "popups": [peak("ws_sessions")],
        "zoom": [delta("workload.zoom_changes", rounds)],
        "workspaces": [delta("workload.workspace_activations", rounds)],
        "pointer": [delta("workload.pointer_motions", rounds)],
        "minimize": [peak("minimized_windows"), retained("minimized_windows")],
        "sticky": [
            peak("retest.sticky_minimized"),
            retained("retest.sticky_minimized"),
        ],
        "fullscreen": [peak("minimized_windows"), retained("minimized_windows")],
        "activation": [
            delta("retest.client_disconnects"),
            retained("pending_activations"),
            retained("retest.pending_wayland_activations"),
            retained("retest.pending_x11_activations"),
        ],
        "cursor": [
            delta("retest.cursor_shape_changes"),
            delta("retest.cursor_cache_hits"),
            delta("retest.cursor_cache_misses"),
            delta("retest.cursor_frames_evicted"),
            delta("retest.cursor_magnified_evicted"),
            peak("retest.cursor_magnified"),
            {"kind": "equals", "counter": "retest.cursor_magnified", "value": 0},
        ],
        "constraints": [
            delta("retest.pointer_hint_applied"),
            delta("retest.pointer_hint_rejected"),
        ],
        "recording": [peak("sessions"), retained("sessions")],
    }
    fault = {
        "cleanup": [
            delta("retest.cleanup_failures"),
            delta("retest.cleanup_successes"),
            {"kind": "equals", "counter": "retest.cleanup_pending", "value": 0},
        ],
        "config": [
            delta(f"retest.config_{suffix}")
            for suffix in ("faults", "invalidations", "errors_preserved")
        ],
        "scanout": [
            delta(f"retest.scanout_{suffix}")
            for suffix in ("faults", "invalidations", "errors_preserved")
        ],
        "faultcapture": [
            delta("retest.capture_workspace_faults"),
            delta("retest.capture_toplevel_faults"),
        ],
    }
    hardware = {
        "physical-disconnect": [
            delta("retest.output_removals"),
            delta("retest.client_gpu_removals"),
        ],
        "vt-deactivate": [
            delta("retest.cleanup_inactive"),
            delta("retest.cleanup_successes"),
        ],
        "session-lock": [
            peak("retest.lock_surfaces"),
            delta("retest.lock_surface_removals"),
        ],
    }
    catalog = {"normal": normal, "fault": fault, "hardware": hardware}
    checks = catalog.get(mode)
    if checks is None:
        checks = {mode: (normal | fault | hardware)[mode]}
    return [{"name": name, "checks": values} for name, values in checks.items()]


def run(*command, **kwargs):
    return subprocess.run(command, check=True, timeout=30, **kwargs)


class Client:
    def __init__(self, executable, evidence):
        self.process = subprocess.Popen(
            [str(executable), "180"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=evidence,
            text=True,
        )
        self.events = queue.Queue()
        self.pending = []
        self.evidence = evidence
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.reader.start()

    def read(self):
        for line in self.process.stdout:
            self.evidence.write(line)
            self.evidence.flush()
            try:
                self.events.put(json.loads(line))
            except ValueError:
                self.events.put({"event": "error", "detail": line})
        self.events.put({"event": "error", "detail": "client exited"})

    def wait(self, event, command=None):
        def matches(message):
            return message["event"] == event and (
                command is None or message["detail"].get("command") == command
            )

        for index, message in enumerate(self.pending):
            if matches(message):
                return self.pending.pop(index)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                message = self.events.get(
                    timeout=max(0.01, deadline - time.monotonic())
                )
            except queue.Empty as error:
                raise TimeoutError(f"client event absent: {event}") from error
            if message["event"] == "error":
                raise RuntimeError(str(message))
            if matches(message):
                return message
            self.pending.append(message)
        raise TimeoutError(f"client event absent: {event}")

    def send(self, command):
        self.process.stdin.write(json.dumps({"command": command}) + "\n")
        self.process.stdin.flush()
        return self.wait("acknowledged", command)

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.process.stdin.close()
        self.process.stdout.close()


class Driver:
    def __init__(self, args):
        self.args = args
        self.root = Path(args.directory)
        self.phase = ""

    def mark(self, label, verb="census"):
        request(self.root, verb, self.phase, label, 60)

    def key(self, *keys):
        codes = [f"{key}:1" for key in keys] + [f"{key}:0" for key in reversed(keys)]
        run("ydotool", "key", *codes)

    @contextlib.contextmanager
    def client(self):
        with (self.root / f"{self.phase}-client.jsonl").open("a") as log:
            client = Client(self.args.client, log)
            try:
                client.wait("ready")
                client.wait("managed")
                client.send("focus")
                yield client
                if client.process.poll() is None:
                    client.send("destroy")
                if client.process.wait(timeout=5):
                    raise RuntimeError("helper failed")
            finally:
                client.close()

    def outputs(self):
        if not self.args.output:
            raise ValueError("--output must identify a secondary connected output")
        connected = [
            p.parent.name.split("-", 1)[1]
            for p in Path("/sys/class/drm").glob("card*-*/status")
            if p.read_text().strip() == "connected"
        ]
        if len(connected) < 2 or self.args.output not in connected:
            raise ValueError("target must be connected alongside another display")
        configuration = run(
            "cosmic-randr", "list", "--kdl", capture_output=True, text=True
        ).stdout
        if not configuration.strip():
            raise ValueError("output configuration snapshot is empty")
        (self.root / f"{self.phase}-outputs.kdl").write_text(configuration)
        try:
            run("cosmic-randr", "disable", self.args.output)
            time.sleep(3)
            self.mark("output-disabled")
        finally:
            run("cosmic-randr", "kdl", input=configuration, text=True)
        time.sleep(5)

    def record(self):
        if not self.args.output:
            raise ValueError("recording requires --output")
        output = self.root / "recording.mkv"
        with (self.root / "recording.log").open("w") as log:
            process = subprocess.Popen(
                ["wf-recorder", "-o", self.args.output, "-f", str(output)],
                stdout=log,
                stderr=log,
            )
            try:
                time.sleep(5)
                if process.poll() is not None:
                    raise RuntimeError("recorder exited before held checkpoint")
                self.mark("recording-held")
                self.outputs()
            finally:
                if process.poll() is None:
                    process.send_signal(signal.SIGINT)
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                        raise
            if process.returncode:
                raise RuntimeError("recording failed")
        probe = run(
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(output),
            capture_output=True,
            text=True,
        )
        if float(json.loads(probe.stdout)["format"]["duration"]) <= 0:
            raise ValueError("recording contains no frames")

    def protocol(self):
        with self.client() as client:
            if self.phase in ("cursor", "constraints"):
                client.send("fullscreen")
                run("ydotool", "mousemove", "-x", "1", "-y", "0")
                client.wait("pointer-enter")
            if self.phase in ("minimize", "sticky", "fullscreen"):
                if self.phase == "sticky":
                    client.send("sticky")
                elif self.phase == "fullscreen":
                    client.send("fullscreen")
                client.send("minimize")
                time.sleep(2)
                self.mark("minimized-held")
            elif self.phase == "activation":
                client.send("activate-token")
                client.wait("activation-token-done")
                self.mark("activation-held")
            elif self.phase == "cursor":
                self.key(125, 13)
                try:
                    for _ in range(self.args.rounds):
                        client.send("shape-pointer")
                        time.sleep(0.2)
                        client.send("shape-default")
                        time.sleep(0.2)
                    self.mark("cursor-held")
                finally:
                    self.key(125, 12)
                time.sleep(12)
                run("ydotool", "mousemove", "-x", "1", "-y", "0")
                time.sleep(1)
                self.mark("cursor-expired")
            elif self.phase == "constraints":
                client.send("lock")
                client.wait("locked")
                client.send("hint")
                client.send("unlock")
                time.sleep(0.5)
                self.mark("hint-applied")
                client.send("lock")
                client.wait("locked")
                client.send("hint")
                with self.client() as other:
                    other.send("fullscreen")
                    run("ydotool", "mousemove", "-x", "1", "-y", "0")
                    other.wait("pointer-enter")
                    client.send("unlock")
                    time.sleep(0.5)
                    self.mark("hint-rejected")
            else:
                self.mark("window-held")

    def assisted(self):
        if not sys.stdin.isatty():
            raise ValueError("hardware suite requires a present operator and terminal")
        if self.phase == "physical-disconnect":
            input("Physically disconnect the secondary output, then press Enter: ")
            self.mark("disconnected-held")
            input("Reconnect the output, then press Enter: ")
        elif self.phase == "vt-deactivate":
            with self.client() as client:
                print(
                    "Switch to another VT now; automatic inactive census in 15 seconds. Return after 30 seconds.",
                    flush=True,
                )
                time.sleep(15)
                self.mark("inactive-held")
                sample = json.loads(
                    (self.root / "samples.jsonl").read_text().splitlines()[-1]
                )
                if sample["counters"]["retest.session_active"] != 0:
                    raise ValueError("session did not deactivate")
                client.send("destroy")
                client.process.wait(timeout=5)
                self.mark("inactive-destroyed")
                after = json.loads(
                    (self.root / "samples.jsonl").read_text().splitlines()[-1]
                )
                before = sample["counters"]
                state = after["counters"]
                if (
                    state["retest.cleanup_pending"] != 1
                    or state["retest.cleanup_requests"]
                    <= before["retest.cleanup_requests"]
                    or state["retest.cleanup_attempts"]
                    != before["retest.cleanup_attempts"]
                ):
                    raise ValueError("inactive cleanup was not deferred and retained")
                input("Return to the graphical VT, then press Enter: ")
        else:
            print(
                "Lock the session now; output cycle begins in 15 seconds. Unlock after the output returns.",
                flush=True,
            )
            time.sleep(15)
            self.mark("locked-held")
            self.outputs()
            input("Unlock the session, then press Enter: ")

    def workload(self):
        phase = self.phase
        if phase in (
            "apps",
            "minimize",
            "sticky",
            "fullscreen",
            "activation",
            "cursor",
            "constraints",
        ):
            self.protocol()
        elif phase == "monitors":
            for _ in range(self.args.rounds):
                self.outputs()
        elif phase == "recording":
            self.record()
        elif phase in ("physical-disconnect", "vt-deactivate", "session-lock"):
            self.assisted()
        elif phase in ("cleanup", "config", "scanout"):
            self.mark("armed", "arm-" + phase)
            self.outputs()
        elif phase == "faultcapture":
            with self.client():
                self.mark("armed-workspace", "arm-capture-workspace")
                self.mark("armed-toplevel", "arm-capture-toplevel")
                self.key(125, 17)
                try:
                    time.sleep(3)
                    self.mark("fault-overview-held")
                finally:
                    self.key(1)
        elif phase in ("selftest", "popups"):
            self.key(125, 17)
            try:
                time.sleep(3)
                self.mark("overview-held")
            finally:
                self.key(1)
        elif phase == "capture":
            for _ in range(self.args.rounds):
                run(
                    "cosmic-screenshot",
                    "--interactive=false",
                    "--notify=false",
                    "--save-dir",
                    str(self.root),
                )
        elif phase == "zoom":
            for _ in range(self.args.rounds):
                self.key(125, 13)
                self.key(125, 12)
        elif phase == "workspaces":
            for _ in range(self.args.rounds):
                self.key(125, 3)
                self.key(125, 2)
        elif phase == "pointer":
            for _ in range(self.args.rounds):
                run("ydotool", "mousemove", "-x", "400", "-y", "0")
                run("ydotool", "mousemove", "-x", "-400", "-y", "0")
        else:
            raise ValueError(f"unknown phase {phase}")

    def execute(self):
        session = json.loads((self.root / "session.json").read_text())
        phases = plan(self.args.suite, self.args.rounds)
        mode = (
            self.args.suite
            if self.args.suite in ("normal", "fault", "hardware")
            else "normal"
        )
        manifest = dict(session, schema=1, mode=mode, phases=phases)
        validate_manifest(manifest)
        if (self.root / "manifest.json").exists():
            raise ValueError("run already declared; start a fresh session directory")
        write_json(self.root / "manifest.json", manifest)
        write_json(
            self.root / "completion.json",
            {"run_id": session["run_id"], "status": "incomplete"},
        )
        for phase in phases:
            self.phase = phase["name"]
            self.mark("pre-" + self.phase)
            self.workload()
            time.sleep(5)
            self.mark("post-" + self.phase)
        time.sleep(self.args.settle)
        self.mark("settle", "snapshot")
        write_json(
            self.root / "completion.json",
            {"run_id": session["run_id"], "status": "complete"},
        )
        return verdict(self.root)


def main():
    def interrupted(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suite")
    parser.add_argument("--directory", default=os.environ.get("CTL"))
    parser.add_argument("--rounds", type=int, default=8)
    parser.add_argument("--settle", type=int, default=90)
    parser.add_argument("--output", default=os.environ.get("TARGET_OUTPUT"))
    parser.add_argument(
        "--client", type=Path, default=TOOLS / "clients/target/debug/vram-retest-client"
    )
    args = parser.parse_args()
    if not args.directory or args.rounds < 1 or args.settle < 1:
        parser.error("directory, positive rounds and positive settle duration required")
    args.suite = {"all": "normal", "faultall": "fault"}.get(args.suite, args.suite)
    try:
        return Driver(args).execute()
    except (
        OSError,
        ValueError,
        RuntimeError,
        TimeoutError,
        KeyError,
        EOFError,
        subprocess.SubprocessError,
    ) as error:
        print(f"INCOMPLETE: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
