import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))


class Transport(unittest.TestCase):
    def test_collection_failure_does_not_write_sample(self):
        import session

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch("session.os.kill"),
                patch(
                    "session.journal",
                    return_value="=== END VRAM/resource census ===\n=== VRAM/resource census (SIGUSR1) ===\n",
                ),
                self.assertRaises(ValueError),
            ):
                session.collect(
                    root,
                    {"pid": 123},
                    {
                        "request_id": "id",
                        "phase": "pointer",
                        "label": "held",
                        "verb": "census",
                    },
                    root / "cursor",
                    0.1,
                )
            self.assertFalse((root / "samples.jsonl").exists())

    def run_request(self, alter):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "requests").mkdir()
            (root / "acks").mkdir()
            (root / "session.json").write_text(json.dumps({"run_id": "run"}))
            result = {}

            def server():
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    requests = list((root / "requests").glob("*.json"))
                    if requests:
                        request = json.loads(requests[0].read_text())
                        reply = dict(request, status="ok")
                        alter(reply)
                        (root / "acks" / requests[0].name).write_text(json.dumps(reply))
                        result["request"] = request
                        return
                    time.sleep(0.01)

            thread = threading.Thread(target=server)
            thread.start()
            process = subprocess.run(
                [
                    sys.executable,
                    str(TOOLS / "session.py"),
                    "request",
                    directory,
                    "census",
                    "pointer",
                    "held",
                    "--timeout",
                    "1",
                ],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            thread.join()
            return process

    def test_exact_ack_succeeds(self):
        self.assertEqual(self.run_request(lambda reply: None).returncode, 0)

    def test_wrong_label_ack_fails(self):
        self.assertNotEqual(
            self.run_request(lambda reply: reply.update(label="unrelated")).returncode,
            0,
        )

    def test_failed_collection_propagates(self):
        self.assertNotEqual(
            self.run_request(lambda reply: reply.update(status="error")).returncode, 0
        )

    def test_session_requires_build_identity(self):
        process = subprocess.run(
            ["bash", str(TOOLS / "session.sh")],
            capture_output=True,
            timeout=3,
            check=False,
        )
        self.assertNotEqual(process.returncode, 0)


if __name__ == "__main__":
    unittest.main()
