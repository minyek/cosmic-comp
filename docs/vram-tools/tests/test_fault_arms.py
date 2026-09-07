import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class FaultArmOwnership(unittest.TestCase):
    def test_aborted_scope_removes_owned_token(self):
        from session import FaultArms

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(RuntimeError), FaultArms(root) as arms:
                arms.arm("arm-config", "ours")
                raise RuntimeError("aborted")
            self.assertFalse((root / "arm-config").exists())

    def test_unrelated_arm_survives_cleanup(self):
        from session import FaultArms

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "arm-scanout").write_text("unrelated")
            with FaultArms(root) as arms:
                arms.arm("arm-config", "ours")
            self.assertEqual((root / "arm-scanout").read_text(), "unrelated")

    def test_replaced_token_survives_cleanup(self):
        from session import FaultArms

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with FaultArms(root) as arms:
                arms.arm("arm-config", "ours")
                (root / "arm-config").unlink()
                (root / "arm-config").write_text("new-owner")
            self.assertEqual((root / "arm-config").read_text(), "new-owner")


if __name__ == "__main__":
    unittest.main()
