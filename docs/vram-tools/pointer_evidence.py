"""Compare observed global cursor transitions with protocol-local hints."""

import math


def validate_transition(record, samples):
    try:
        labels = [sample["label"] for sample in samples]
        before_label, after_label = record["before"], record["after"]
        if labels.count(before_label) != 1 or labels.count(after_label) != 1:
            raise ValueError(
                "pointer transition requires unique before/after checkpoints"
            )
        before_index, after_index = (
            labels.index(before_label),
            labels.index(after_label),
        )
        if before_index >= after_index:
            raise ValueError("pointer transition checkpoints out of order")
        prefix = f"retest.pointer.{record['seat']}."
        before = tuple(
            samples[before_index]["counters"][prefix + axis] for axis in ("x", "y")
        )
        actual = tuple(
            samples[after_index]["counters"][prefix + axis] for axis in ("x", "y")
        )
        if record["kind"] == "unchanged":
            expected = before
        elif record["kind"] == "hint":
            geometry = samples[before_index]["counters"]
            scale = geometry[prefix + "client_scale"]
            if not math.isfinite(scale) or scale <= 0:
                raise ValueError("invalid observed client scale")
            expected = tuple(
                geometry[prefix + "focus_origin_" + axis] + record["hint"][axis] / scale
                for axis in ("x", "y")
            )
        else:
            raise ValueError("unknown pointer transition kind")
        if not all(math.isfinite(value) for value in (*before, *actual, *expected)):
            raise ValueError("non-finite pointer position")
        if actual != expected:
            return [f"pointer {record['kind']}: observed {actual}, expected {expected}"]
        return []
    except (KeyError, TypeError, ValueError) as error:
        return [f"incomplete pointer evidence: {error}"]
