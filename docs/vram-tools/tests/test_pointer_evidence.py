import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class PointerTransitions(unittest.TestCase):
    def evaluate(self, kind, after):
        from pointer_evidence import validate_transition

        samples = [
            {
                "label": "before",
                "counters": {
                    "retest.pointer.0.x": -120.5,
                    "retest.pointer.0.y": 240.25,
                },
            },
            {
                "label": "after",
                "counters": {
                    "retest.pointer.0.x": after[0],
                    "retest.pointer.0.y": after[1],
                },
            },
        ]
        record = {
            "kind": kind,
            "before": "before",
            "after": "after",
            "seat": 0,
            "local": {"x": 20.5, "y": 40.25},
            "hint": {"x": 80, "y": 80},
        }
        return validate_transition(record, samples)

    def test_matching_hint_uses_observed_surface_origin(self):
        self.assertEqual(self.evaluate("hint", (-61, 280)), [])

    def test_local_hint_mistaken_for_global_coordinate_fails(self):
        self.assertTrue(self.evaluate("hint", (80, 80)))

    def test_wrong_warp_after_focus_change_fails(self):
        self.assertTrue(self.evaluate("unchanged", (-61, 280)))

    def test_no_warp_preserves_fractional_negative_coordinates(self):
        self.assertEqual(self.evaluate("unchanged", (-120.5, 240.25)), [])

    def test_even_small_unexpected_warp_fails(self):
        self.assertTrue(self.evaluate("unchanged", (-120.25, 240.25)))


if __name__ == "__main__":
    unittest.main()
