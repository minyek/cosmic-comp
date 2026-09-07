import importlib
import os
import queue
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class Suites(unittest.TestCase):
    def test_managed_state_wait_rejects_earlier_unminimized_state(self):
        suite = importlib.import_module("suite")
        client = suite.Client.__new__(suite.Client)
        client.events = queue.Queue()
        client.pending = [
            {"event": "managed-state", "detail": {"states": [2]}},
            {"event": "managed-state", "detail": {"states": [1, 4]}},
        ]
        self.assertEqual(client.wait_state(1, 4)["detail"]["states"], [1, 4])

    def test_pointer_leave_event_has_its_own_required_coordinate_check(self):
        suite = importlib.import_module("suite")
        phase = next(
            phase for phase in suite.plan("normal", 1) if phase["name"] == "constraints"
        )
        self.assertIn(
            {"kind": "pointer", "name": "leave-event", "mode": "unchanged"},
            phase["checks"],
        )

    def test_standalone_fault_phase_uses_fault_mode(self):
        suite = importlib.import_module("suite")
        self.assertEqual(suite.mode_for("config"), "fault")

    def test_locked_physical_disconnect_is_a_distinct_phase(self):
        suite = importlib.import_module("suite")
        phases = {phase["name"] for phase in suite.plan("hardware", 2)}
        self.assertIn("locked-disconnect", phases)

    def test_failed_output_change_restores_saved_configuration(self):
        suite = importlib.import_module("suite")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command = root / "cosmic-randr"
            command.write_text(
                '#!/bin/bash\ncase "$1" in\nlist) echo saved-config;;\ndisable) exit 7;;\nkdl) cat > "$RESTORED";;\nesac\n'
            )
            command.chmod(0o755)
            driver = suite.Driver(SimpleNamespace(directory=directory, output="DP-1"))
            driver.phase = "monitors"
            outputs = [
                SimpleNamespace(
                    parent=SimpleNamespace(name=f"card0-{name}"),
                    read_text=lambda: "connected",
                )
                for name in ("DP-1", "DP-2")
            ]
            with (
                patch.dict(
                    os.environ,
                    PATH=directory + os.pathsep + os.environ["PATH"],
                    RESTORED=str(root / "restored"),
                ),
                patch("suite.Path.glob", return_value=outputs),
                self.assertRaises(subprocess.CalledProcessError),
            ):
                driver.outputs()
            self.assertEqual((root / "restored").read_text(), "saved-config\n")

    def test_normal_declares_protocol_and_recording_phases(self):
        suite = importlib.import_module("suite")
        names = {p["name"] for p in suite.plan("normal", 2)}
        self.assertTrue(
            {"constraints", "cursor", "minimize", "sticky", "recording", "activation"}
            <= names
        )

    def test_faults_have_execution_and_invalidation_checks(self):
        suite = importlib.import_module("suite")
        phases = {p["name"]: p for p in suite.plan("fault", 2)}
        for name in ("config", "scanout"):
            counters = {c.get("counter") for c in phases[name]["checks"]}
            self.assertTrue(
                {
                    f"retest.{name}_faults",
                    f"retest.{name}_invalidations",
                    f"retest.{name}_errors_preserved",
                }
                <= counters
            )

    def test_hardware_is_explicit_suite(self):
        suite = importlib.import_module("suite")
        self.assertEqual(
            {p["name"] for p in suite.plan("hardware", 2)},
            {
                "physical-disconnect",
                "multi-gpu",
                "vt-deactivate",
                "session-lock",
                "locked-disconnect",
            },
        )


if __name__ == "__main__":
    unittest.main()
