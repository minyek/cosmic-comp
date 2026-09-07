import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('census', TOOLS / 'census.py')
census = importlib.util.module_from_spec(spec)
spec.loader.exec_module(census)


class HistoricalFalsePasses(unittest.TestCase):
    def verdict(self, expectation, labels, minimized):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'expectations.csv').write_text(expectation)
            marks = ['seq,time,label,mib']
            for seq, (label, count) in enumerate(zip(labels, minimized), 1):
                marks.append(f'{seq},00:00:00,{label},1')
                (root / f'journal-{seq:02d}.txt').write_text(
                    '=== VRAM/resource census (SIGUSR1) ===\n'
                    f'outputs=1 minimized_windows={count}\n'
                    f'workload counters: pointer_motions={seq * 20}\n')
            (root / 'marks.csv').write_text('\n'.join(marks))
            with contextlib.redirect_stdout(io.StringIO()):
                return census.cmd_verdict(root)

    def test_unknown_expectation_cannot_pass(self):
        self.assertNotEqual(self.verdict('pointer,typo,1,x', ['baseline', 'post-pointer'], [0, 0]), 0)

    def test_missing_phase_boundary_cannot_borrow_activity(self):
        self.assertNotEqual(self.verdict('pointer,pointer_input,1,x', ['baseline', 'other'], [0, 0]), 0)

    def test_retained_minimized_window_cannot_pass(self):
        self.assertNotEqual(self.verdict('minimize,minimize_tracked,1,x', ['baseline', 'minimize-held', 'post-minimize'], [0, 1, 1]), 0)


if __name__ == '__main__':
    unittest.main()
