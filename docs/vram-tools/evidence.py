"""Strict run manifests and phase-local resource assertions."""

import json
import re
import uuid
from itertools import pairwise
from pathlib import Path

QUEUES = (
    "texture",
    "framebuffer",
    "renderbuffer",
    "egl_image",
    "mapping",
    "program",
    "sync",
)
SETTLED = (
    "sessions",
    "cursor_sessions",
    "ws_sessions",
    "surface_sessions",
    "ws_cursor_sessions",
    "surface_cursor_sessions",
    "pending_frames",
    "offscreen_renderbuffers",
    "ws_offscreen",
    "surface_offscreen",
    "retest.stale_lock_surfaces",
    "retest.cleanup_pending",
    "retest.renderer_enumeration_pending",
)
REQUIRED = (
    set(SETTLED)
    | {
        "outputs",
        "surface_threads",
        "dead",
        "minimized_windows",
        "live_slots",
        "retest.expected_surface_threads",
        "retest.session_active",
    }
    | {
        f"raw.{operation}_{queue}"
        for queue in QUEUES
        for operation in ("queued", "drained", "discarded")
    }
    | {
        f"queue_progress.{queue}_{field}"
        for queue in QUEUES
        for field in ("submitted", "oldest", "pending")
    }
)
KINDS = {"delta", "peak_above", "return", "equals", "journal", "generations", "pointer"}


def validate_manifest(manifest):
    if manifest.get("schema") != 1:
        raise ValueError("unsupported manifest schema")
    uuid.UUID(manifest["run_id"])
    if not re.fullmatch("[a-f0-9]{64}", manifest["build_sha256"]):
        raise ValueError("missing binary SHA256 identity")
    if (
        type(manifest["pid"]) is not int
        or manifest["pid"] <= 0
        or not manifest["start_ticks"]
    ):
        raise ValueError("missing process identity")
    if manifest["mode"] not in ("normal", "fault", "hardware"):
        raise ValueError("unknown suite mode")
    if manifest.get("legacy_capture_fault"):
        raise ValueError(
            "persistent legacy capture fault must be disabled; use declared runtime fault arms"
        )
    if not Path(manifest["fault_control"]).is_absolute():
        raise ValueError("fault control must be absolute")
    phases = manifest["phases"]
    names = [phase["name"] for phase in phases]
    if not names or len(names) != len(set(names)):
        raise ValueError("missing or duplicate phases")
    for phase in phases:
        if not re.fullmatch("[a-z][a-z0-9-]*", phase["name"]) or not phase["checks"]:
            raise ValueError("phase requires a name and explicit checks")
        for check in phase["checks"]:
            kind = check["kind"]
            if kind not in KINDS:
                raise ValueError(f"unknown check: {kind}")
            if kind in ("delta", "peak_above") and (
                type(check.get("minimum")) is not int or check["minimum"] <= 0
            ):
                raise ValueError("activity minimum must be a positive integer")
            if kind not in ("journal", "generations", "pointer") and not check.get(
                "counter"
            ):
                raise ValueError("check requires a counter")
            if kind == "equals" and type(check.get("value")) is not int:
                raise ValueError("equals requires an integer value")
            if kind == "journal":
                re.compile(check["pattern"])


def check_phase(phase, span):
    errors = []
    first, last = span[0]["counters"], span[-1]["counters"]
    for check in phase["checks"]:
        kind, key = check["kind"], check.get("counter")
        if kind == "pointer":
            from pointer_evidence import validate_transition

            records = span[-1].get("pointer_transitions", {})
            record = records.get(check["name"])
            if not record or record.get("kind") != check["mode"]:
                errors.append(f"missing pointer transition {check['name']}")
            else:
                errors.extend(validate_transition(record, span))
            continue
        if key and any(key not in sample["counters"] for sample in span):
            errors.append(f"missing counter {key}")
            continue
        values = [sample["counters"][key] for sample in span] if key else []
        if kind == "delta" and (
            any(b < a for a, b in pairwise(values))
            or values[-1] - values[0] < check["minimum"]
        ):
            errors.append(f"{key} did not advance by {check['minimum']}")
        elif kind == "peak_above" and max(values) - values[0] < check["minimum"]:
            errors.append(f"{key} did not peak above baseline by {check['minimum']}")
        elif kind == "return" and values[-1] != values[0]:
            errors.append(f"{key} did not return to baseline")
        elif kind == "equals" and values[-1] != check["value"]:
            errors.append(f"{key} != {check['value']}")
        elif kind == "journal" and not re.search(
            check["pattern"], "\n".join(s["journal"] for s in span[1:])
        ):
            errors.append("required phase-local journal evidence absent")
        elif kind == "generations":
            before, after = first.get("_generations", {}), last.get("_generations", {})
            if (
                not before
                or not after
                or not any(after.get(k, 0) > v for k, v in before.items())
            ):
                errors.append("swapchain generations did not advance")
            if last["live_slots"] > first["live_slots"]:
                errors.append("swapchain slots retained after phase")
    if last["minimized_windows"] != first["minimized_windows"]:
        errors.append("minimized windows did not return to phase baseline")
    for queue in QUEUES:
        watermark = first[f"queue_progress.{queue}_submitted"]
        oldest = last[f"queue_progress.{queue}_oldest"]
        if oldest and oldest <= watermark:
            errors.append(
                f"{queue} cleanup has not passed baseline enqueue watermark {watermark}"
            )
    return errors


def validate(manifest, samples, completion):
    try:
        validate_manifest(manifest)
        return _validate(manifest, samples, completion)
    except (KeyError, TypeError, ValueError, IndexError) as error:
        return [f"incomplete or invalid evidence: {error}"]


def _validate(manifest, samples, completion):
    errors = []
    if completion != {"status": "complete", "run_id": manifest["run_id"]}:
        errors.append("suite completion missing or failed")
    if not samples or samples[-1]["label"] != "settle":
        errors.append("final settle checkpoint missing")
    requests = [sample["request_id"] for sample in samples]
    if len(requests) != len(set(requests)):
        errors.append("duplicate request acknowledgement")
    for sample in samples:
        for identity in ("run_id", "pid", "build_sha256", "start_ticks"):
            if sample[identity] != manifest[identity]:
                errors.append(f"{sample['label']}: wrong {identity}")
        if not sample["complete"]:
            errors.append(f"{sample['label']}: truncated census")
        counters = sample["counters"]
        missing = REQUIRED - counters.keys()
        if missing:
            errors.append(f"{sample['label']}: missing counters {sorted(missing)}")
            continue
        if counters["dead"] or counters["retest.stale_lock_surfaces"]:
            errors.append(f"{sample['label']}: retained dead resources")
        if counters["surface_threads"] != counters["retest.expected_surface_threads"]:
            errors.append(f"{sample['label']}: stranded or missing surface threads")
        if sample["label"].startswith(("pre-", "post-")) or sample["label"] == "settle":
            for key in SETTLED:
                if counters[key]:
                    errors.append(f"{sample['label']}: {key} not settled")
        if re.search(r"panicked at|panic occurred", sample["journal"]):
            errors.append(f"{sample['label']}: compositor panic")
    if errors:
        return errors
    for earlier, later in pairwise(samples):
        for queue in QUEUES:
            submitted = f"queue_progress.{queue}_submitted"
            oldest = later["counters"][f"queue_progress.{queue}_oldest"]
            pending = later["counters"][f"queue_progress.{queue}_pending"]
            if bool(oldest) != bool(pending) or oldest > later["counters"][submitted]:
                errors.append(
                    f"{later['label']}: inconsistent {queue} outstanding queue snapshot"
                )
            if (
                later["counters"]["retest.session_active"]
                and oldest
                and oldest <= earlier["counters"][submitted]
            ):
                errors.append(
                    f"{later['label']}: {queue} has not passed previous enqueue watermark"
                )
            if any(
                later["counters"][key] < earlier["counters"][key]
                for key in (submitted,)
            ):
                errors.append(f"{later['label']}: {queue} cumulative counter decreased")
    previous_end = -1
    declared = {phase["name"] for phase in manifest["phases"]}
    if any(sample["phase"] not in declared for sample in samples):
        errors.append("undeclared phase evidence")
    for phase in manifest["phases"]:
        name = phase["name"]
        starts = [i for i, s in enumerate(samples) if s["label"] == f"pre-{name}"]
        ends = [i for i, s in enumerate(samples) if s["label"] == f"post-{name}"]
        if (
            len(starts) != 1
            or len(ends) != 1
            or starts[0] <= previous_end
            or ends[0] <= starts[0]
        ):
            errors.append(
                f"{name}: missing, duplicate, unordered or short phase boundaries"
            )
            continue
        span = samples[starts[0] : ends[0] + 1]
        previous_end = ends[0]
        if any(sample["phase"] != name for sample in span):
            errors.append(f"{name}: interleaved phase evidence")
            continue
        errors.extend(f"{name}: {error}" for error in check_phase(phase, span))
    return errors


def verdict(directory):
    root = Path(directory)
    try:
        manifest = json.loads((root / "manifest.json").read_text())
        samples = [
            json.loads(line)
            for line in (root / "samples.jsonl").read_text().splitlines()
        ]
        completion = json.loads((root / "completion.json").read_text())
        if any(
            check.get("kind") == "pointer"
            for phase in manifest["phases"]
            for check in phase["checks"]
        ):
            transitions = json.loads((root / "pointer-transitions.json").read_text())
            for sample in samples:
                if sample["label"] == "post-constraints":
                    sample["pointer_transitions"] = transitions
        errors = validate(manifest, samples, completion)
        if samples:
            for queue in QUEUES:
                key = f"raw.discarded_{queue}"
                if key in samples[0]["counters"] and key in samples[-1]["counters"]:
                    discarded = (
                        samples[-1]["counters"][key] - samples[0]["counters"][key]
                    )
                    if discarded:
                        print(
                            f"note: {queue} discarded during context retirement: {discarded}; explicit deletion unproven"
                        )
    except (OSError, ValueError) as error:
        errors = [f"incomplete evidence: {error}"]
    print("VERDICT: FAIL" if errors else "VERDICT: PASS (declared suite only)")
    for error in errors:
        print(f"  {error}")
    return int(bool(errors))
