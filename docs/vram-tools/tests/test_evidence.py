import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class EvidenceContract(unittest.TestCase):
    def setUp(self):
        import evidence

        self.evidence = evidence
        self.manifest = {
            "schema": 1,
            "run_id": "ec071691-0e54-461e-a676-fba29ab2c6ec",
            "build_sha256": "a" * 64,
            "pid": 123,
            "start_ticks": "42",
            "mode": "normal",
            "fault_control": "/run/user/1000/retest",
            "phases": [
                {
                    "name": "minimize",
                    "checks": [
                        {
                            "kind": "peak_above",
                            "counter": "minimized_windows",
                            "minimum": 1,
                        },
                        {"kind": "return", "counter": "minimized_windows"},
                    ],
                }
            ],
        }
        base = {key: 0 for key in evidence.REQUIRED}
        base.update(outputs=1, surface_threads=1)
        base["retest.session_active"] = 1
        base["retest.expected_surface_threads"] = 1
        self.samples = []
        for index, label in enumerate(
            ["pre-minimize", "held", "post-minimize", "settle"]
        ):
            counters = dict(base)
            counters["minimized_windows"] = int(label == "held")
            self.samples.append(
                {
                    "request_id": str(index),
                    "run_id": self.manifest["run_id"],
                    "phase": "minimize",
                    "label": label,
                    "counters": counters,
                    "journal": "",
                    "complete": True,
                    "pid": 123,
                    "build_sha256": "a" * 64,
                    "start_ticks": "42",
                }
            )

    def errors(self):
        return self.evidence.validate(
            self.manifest,
            self.samples,
            {"status": "complete", "run_id": self.manifest["run_id"]},
        )

    def test_complete_minimize_passes(self):
        self.assertEqual(self.errors(), [])

    def test_unknown_check_fails(self):
        self.manifest["phases"][0]["checks"][0]["kind"] = "typo"
        self.assertTrue(self.errors())

    def test_missing_boundary_fails(self):
        self.samples.pop(2)
        self.assertTrue(self.errors())

    def test_minimized_retained_fails(self):
        self.samples[2]["counters"]["minimized_windows"] = 1
        self.assertTrue(self.errors())

    def test_missing_required_counter_fails(self):
        del self.samples[0]["counters"]["sessions"]
        self.assertTrue(self.errors())

    def test_malformed_manifest_file_returns_controlled_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "manifest.json").write_text("{}")
            (root / "samples.jsonl").write_text("{}\n")
            (root / "completion.json").write_text("{}")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(self.evidence.verdict(root), 1)

    def test_baseline_outstanding_queue_snapshot_must_be_consistent(self):
        self.samples[0]["counters"]["queue_progress.texture_pending"] = 1
        self.assertTrue(self.errors())

    def test_truncated_census_fails(self):
        self.samples[1]["complete"] = False
        self.assertTrue(self.errors())

    def test_wrong_run_fails(self):
        self.samples[1]["run_id"] = "old"
        self.assertTrue(self.errors())

    def test_threads_use_current_expected_count(self):
        self.samples[1]["counters"].update(outputs=2, surface_threads=2)
        self.samples[1]["counters"]["retest.expected_surface_threads"] = 2
        self.assertEqual(self.errors(), [])

    def test_queue_watermark_allows_new_items(self):
        for index, sample in enumerate(self.samples):
            sample["counters"]["raw.queued_texture"] = index + 1
            sample["counters"]["raw.drained_texture"] = index
            sample["counters"].update(
                {
                    "queue_progress.texture_submitted": index + 1,
                    "queue_progress.texture_oldest": index + 1,
                    "queue_progress.texture_pending": 1,
                }
            )
        self.assertEqual(self.errors(), [])

    def test_queue_watermark_detects_undrained_old_items(self):
        self.samples[0]["counters"]["raw.queued_texture"] = 3
        self.samples[0]["counters"]["queue_progress.texture_submitted"] = 3
        for sample in self.samples[1:]:
            sample["counters"].update(
                {
                    "queue_progress.texture_submitted": 3,
                    "queue_progress.texture_oldest": 1,
                    "queue_progress.texture_pending": 1,
                }
            )
        self.assertTrue(self.errors())

    def test_new_completions_cannot_mask_stuck_earliest_item(self):
        for index, sample in enumerate(self.samples):
            sample["counters"].update(
                {
                    "raw.queued_texture": index + 10,
                    "raw.drained_texture": index + 9,
                    "queue_progress.texture_submitted": index + 10,
                    "queue_progress.texture_oldest": 1,
                    "queue_progress.texture_pending": 1,
                }
            )
        self.assertTrue(self.errors())

    def test_retired_context_discards_pass_watermark(self):
        self.samples[0]["counters"]["raw.queued_texture"] = 2
        self.samples[0]["counters"].update(
            {
                "queue_progress.texture_submitted": 2,
                "queue_progress.texture_oldest": 1,
                "queue_progress.texture_pending": 2,
            }
        )
        for sample in self.samples[1:]:
            sample["counters"]["raw.queued_texture"] = 2
            sample["counters"]["raw.discarded_texture"] = 2
            sample["counters"]["queue_progress.texture_submitted"] = 2
        self.assertEqual(self.errors(), [])

    def test_empty_checks_fail(self):
        self.manifest["phases"][0]["checks"] = []
        self.assertTrue(self.errors())

    def test_duplicate_request_fails(self):
        self.samples[1]["request_id"] = self.samples[0]["request_id"]
        self.assertTrue(self.errors())

    def test_pointer_check_requires_transition_evidence(self):
        self.manifest["phases"][0]["checks"].append(
            {"kind": "pointer", "name": "leave", "mode": "unchanged"}
        )
        self.assertTrue(self.errors())

    def test_pointer_transition_is_checked_by_phase_verdict(self):
        self.manifest["phases"][0]["checks"].append(
            {"kind": "pointer", "name": "leave", "mode": "unchanged"}
        )
        for sample in self.samples:
            sample["counters"].update(
                {"retest.pointer.0.x": 30.5, "retest.pointer.0.y": 10.25}
            )
        self.samples[2]["pointer_transitions"] = {
            "leave": {
                "kind": "unchanged",
                "before": "pre-minimize",
                "after": "held",
                "seat": 0,
            }
        }
        self.assertEqual(self.errors(), [])
        self.samples[1]["counters"]["retest.pointer.0.x"] = 31
        self.assertTrue(self.errors())

    def test_post_phase_queue_must_drain_by_settle(self):
        self.samples[2]["counters"]["raw.queued_texture"] = 5
        self.samples[3]["counters"]["raw.queued_texture"] = 5
        self.samples[2]["counters"]["queue_progress.texture_submitted"] = 5
        self.samples[3]["counters"].update(
            {
                "queue_progress.texture_submitted": 5,
                "queue_progress.texture_oldest": 1,
                "queue_progress.texture_pending": 1,
            }
        )
        self.assertTrue(self.errors())


if __name__ == "__main__":
    unittest.main()
