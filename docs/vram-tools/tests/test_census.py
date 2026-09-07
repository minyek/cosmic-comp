import contextlib
import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
spec = importlib.util.spec_from_file_location("census", TOOLS / "census.py")
census = importlib.util.module_from_spec(spec)
spec.loader.exec_module(census)


class HistoricalFalsePasses(unittest.TestCase):
    def test_dead_cache_entries_are_summed(self):
        parsed = census.parse_block(
            [
                "renderer cache detail A alive=1 dead=2",
                "renderer cache detail B alive=1 dead=0",
            ]
        )
        self.assertEqual(parsed["dead"], 2)

    def test_signed_pointer_coordinates_are_preserved(self):
        parsed = census.parse_block(
            ["retest pointer: seat=0 pointer_x=-80.5 pointer_y=12.25"]
        )
        self.assertEqual(parsed["retest.pointer.0.x"], -80.5)

    def test_tracing_prefix_does_not_hide_cleanup_depth(self):
        parsed = census.parse_block(
            ["2026-01-01 WARN cosmic_comp: GL cleanup queue depth: texture=3"]
        )
        self.assertEqual(parsed["queue.texture"], 3)

    def verdict(self, expectation, labels, minimized):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "expectations.csv").write_text(expectation)
            marks = ["seq,time,label,mib"]
            for seq, (label, count) in enumerate(zip(labels, minimized), 1):
                marks.append(f"{seq},00:00:00,{label},1")
                (root / f"journal-{seq:02d}.txt").write_text(
                    "=== VRAM/resource census (SIGUSR1) ===\n"
                    f"outputs=1 minimized_windows={count}\n"
                    f"workload counters: pointer_motions={seq * 20}\n"
                )
            (root / "marks.csv").write_text("\n".join(marks))
            with contextlib.redirect_stdout(io.StringIO()):
                return census.cmd_verdict(root)

    def test_unknown_expectation_cannot_pass(self):
        self.assertNotEqual(
            self.verdict("pointer,typo,1,x", ["baseline", "post-pointer"], [0, 0]), 0
        )

    def test_missing_phase_boundary_cannot_borrow_activity(self):
        self.assertNotEqual(
            self.verdict("pointer,pointer_input,1,x", ["baseline", "other"], [0, 0]), 0
        )

    def test_retained_minimized_window_cannot_pass(self):
        self.assertNotEqual(
            self.verdict(
                "minimize,minimize_tracked,1,x",
                ["baseline", "minimize-held", "post-minimize"],
                [0, 1, 1],
            ),
            0,
        )


if __name__ == "__main__":
    unittest.main()
