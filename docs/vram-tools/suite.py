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
            delta("retest.wayland_activations_pruned"),
            peak("retest.pending_wayland_activations"),
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
            delta("retest.pointer_constraint_leaves"),
            {"kind": "pointer", "name": "matching", "mode": "hint"},
            {"kind": "pointer", "name": "different-focus", "mode": "unchanged"},
            {"kind": "pointer", "name": "leave", "mode": "unchanged"},
            {"kind": "pointer", "name": "leave-event", "mode": "unchanged"},
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
            peak("retest.gpu_clients"),
        ],
        "multi-gpu": [
            {"kind": "peak_above", "counter": "retest.gpu_clients", "minimum": 2},
            delta("retest.client_gpu_removals", 2),
            retained("retest.gpu_clients"),
        ],
        "vt-deactivate": [
            delta("retest.cleanup_inactive"),
            delta("retest.cleanup_successes"),
        ],
        "session-lock": [
            peak("retest.lock_surfaces"),
            delta("retest.lock_surface_removals"),
        ],
        "locked-disconnect": [
            peak("retest.lock_surfaces"),
            delta("retest.output_removals"),
            delta("retest.lock_surface_removals"),
        ],
    }
    catalog = {"normal": normal, "fault": fault, "hardware": hardware}
    checks = catalog.get(mode)
    if checks is None:
        checks = {mode: (normal | fault | hardware)[mode]}
    return [{"name": name, "checks": values} for name, values in checks.items()]


def mode_for(selection):
    for mode in ("normal", "fault", "hardware"):
        if selection == mode or selection in {phase["name"] for phase in plan(mode, 1)}:
            return mode
    raise ValueError(f"unknown suite or phase {selection}")


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
        self.pointer_local = None
        self.outputs = set()
        self.evidence = evidence
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.reader.start()

    def read(self):
        for line in self.process.stdout:
            self.evidence.write(line)
            self.evidence.flush()
            try:
                message = json.loads(line)
                if message["event"] in ("pointer-enter", "pointer-motion"):
                    self.pointer_local = message["detail"]
                elif message["event"] == "pointer-leave":
                    self.pointer_local = None
                elif message["event"] == "output-enter":
                    self.outputs.add(message["detail"]["name"])
                elif message["event"] == "output-leave":
                    self.outputs.discard(message["detail"]["name"])
                self.events.put(message)
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

    def send(self, command, **parameters):
        self.process.stdin.write(json.dumps({"command": command, **parameters}) + "\n")
        self.process.stdin.flush()
        return self.wait("acknowledged", command)

    def wait_state(self, *required):
        while True:
            message = self.wait("managed-state")
            if set(required) <= set(message["detail"]["states"]):
                return message

    def wait_output(self, name):
        while name not in self.outputs:
            self.wait("output-enter")

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
                    client.wait_state(4)
                elif self.phase == "fullscreen":
                    client.send("fullscreen")
                    client.wait_state(3)
                client.send("minimize")
                client.wait_state(
                    1, 4
                ) if self.phase == "sticky" else client.wait_state(1)
                time.sleep(2)
                self.mark("minimized-held")
            elif self.phase == "activation":
                client.wait("keyboard-enter")
                client.send("pending-activate")
                client.wait("pending-activation-submitted")
                client.wait("acknowledged", "pending-activate-ready")
                self.mark("activation-held")
                client.send("pending-destroy")
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
                self.constraints(client)
            else:
                self.mark("window-held")

    def constraints(self, client):
        transitions = {}

        def lock_with_hint():
            client.send("lock")
            client.wait("locked")
            client.send("hint")

        def observe_unlock(name, mode):
            before, after = name + "-before", name + "-after"
            self.mark(before)
            record = {"kind": mode, "before": before, "after": after, "seat": 0}
            if mode == "hint":
                if client.pointer_local is None:
                    raise ValueError(
                        "matching hint has no observed local pointer coordinates"
                    )
                record.update(local=dict(client.pointer_local), hint={"x": 80, "y": 80})
            client.send("unlock")
            time.sleep(0.5)
            self.mark(after)
            from pointer_evidence import validate_transition

            samples = [
                json.loads(line)
                for line in (self.root / "samples.jsonl").read_text().splitlines()
            ]
            errors = validate_transition(record, samples)
            if errors:
                raise ValueError("; ".join(errors))
            transitions[name] = record
            write_json(self.root / "pointer-transitions.json", transitions)

        lock_with_hint()
        observe_unlock("matching", "hint")
        lock_with_hint()
        with self.client() as other:
            other.send("fullscreen")
            run("ydotool", "mousemove", "-x", "1", "-y", "0")
            other.wait("pointer-enter")
            client.wait("pointer-leave")
            observe_unlock("different-focus", "unchanged")
        client.send("focus")
        run("ydotool", "mousemove", "-x", "1", "-y", "0")
        client.wait("pointer-enter")
        lock_with_hint()
        self.mark("leave-trigger-before")
        self.key(125, 17)
        try:
            client.wait("pointer-leave")
            self.mark("leave-trigger-after")
            transitions["leave-event"] = {
                "kind": "unchanged",
                "before": "leave-trigger-before",
                "after": "leave-trigger-after",
                "seat": 0,
            }
            from pointer_evidence import validate_transition

            samples = [
                json.loads(line)
                for line in (self.root / "samples.jsonl").read_text().splitlines()
            ]
            errors = validate_transition(transitions["leave-event"], samples)
            if errors:
                raise ValueError("; ".join(errors))
            write_json(self.root / "pointer-transitions.json", transitions)
            observe_unlock("leave", "unchanged")
        finally:
            self.key(1)

    def assisted(self):
        if not sys.stdin.isatty():
            raise ValueError("hardware suite requires a present operator and terminal")
        if self.phase == "physical-disconnect":
            if not self.args.output:
                raise ValueError("physical disconnect requires --output")
            with self.client() as client:
                client.send("fullscreen-target", output=self.args.output)
                client.wait_output(self.args.output)
                time.sleep(2)
                self.mark("owned-target-held")
                input(f"Physically disconnect {self.args.output}, then press Enter: ")
                self.mark("disconnected-held")
                client.send("destroy")
                client.process.wait(timeout=5)
                self.mark("disconnected-client-destroyed")
                input(f"Reconnect {self.args.output}, then press Enter: ")
        elif self.phase == "multi-gpu":
            targets = (self.args.output, self.args.other_output)
            if None in targets or len(set(targets)) != 2:
                raise ValueError(
                    "multi-gpu requires distinct --output and --other-output"
                )
            devices = set()
            for target in targets:
                paths = list(Path("/sys/class/drm").glob(f"card*-{target}/status"))
                if len(paths) != 1 or paths[0].read_text().strip() != "connected":
                    raise ValueError(f"output {target} is absent or ambiguous")
                card = paths[0].parent.name.split("-", 1)[0]
                devices.add(
                    (Path("/sys/class/drm") / card / "device").resolve(strict=True)
                )
            if len(devices) != 2:
                raise ValueError(
                    "selected outputs do not belong to distinct DRM devices"
                )
            with self.client() as client:
                for target in targets:
                    client.send("fullscreen-target", output=target)
                    client.wait_output(target)
                    time.sleep(2)
                    self.mark("rendered-" + target)
        elif self.phase == "locked-disconnect":
            print(
                "Lock now. Locked baseline census in 15 seconds; keep locked until all reconnect checkpoints finish.",
                flush=True,
            )
            time.sleep(15)
            self.mark("locked-before-disconnect")
            self.require_locked()
            print(
                "Physically unplug the secondary output now. Disconnected census in 15 seconds.",
                flush=True,
            )
            time.sleep(15)
            self.mark("locked-disconnected-held")
            self.require_locked()
            print(
                "Reconnect the secondary output now. Reconnected census in 15 seconds; remain locked.",
                flush=True,
            )
            time.sleep(15)
            self.mark("locked-reconnected-held")
            self.require_locked()
            input("Unlock the session, then press Enter: ")
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

    def require_locked(self):
        sample = json.loads((self.root / "samples.jsonl").read_text().splitlines()[-1])
        if sample["counters"]["retest.lock_active"] != 1:
            raise ValueError(
                "required checkpoint was not captured while session locked"
            )

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
        elif phase in (
            "physical-disconnect",
            "multi-gpu",
            "vt-deactivate",
            "session-lock",
            "locked-disconnect",
        ):
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
        mode = mode_for(self.args.suite)
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
            try:
                self.workload()
            finally:
                if mode == "fault":
                    self.mark("disarmed", "disarm")
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
    parser.add_argument("--other-output")
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
