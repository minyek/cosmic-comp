#!/usr/bin/env python3
"""Verify the exact running instrumented executable and required desktop tools."""

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

from session import identity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("build")
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument(
        "--suite", choices=("normal", "fault", "hardware"), required=True
    )
    parser.add_argument("--client", type=Path, required=True)
    args = parser.parse_args()
    try:
        process = identity(args.pid, args.build)
        with Path(f"/proc/{args.pid}/exe").open("rb") as binary:
            if b"=== END VRAM/resource census ===" not in binary.read():
                raise ValueError(
                    "running binary has no complete retest census instrumentation"
                )
        required = ["journalctl", "nvidia-smi", "cosmic-randr", "ydotool"]
        if args.suite == "normal":
            required += ["wf-recorder", "ffprobe", "cosmic-screenshot"]
        missing = [name for name in required if shutil.which(name) is None]
        if missing:
            raise ValueError(f"missing required tools: {missing}")
        if not args.client.is_file() or not os.access(args.client, os.X_OK):
            raise ValueError("protocol helper executable missing")
        subprocess.run(
            ["journalctl", "--user", "-b", f"_PID={args.pid}", "-n", "0"],
            check=True,
            capture_output=True,
            timeout=10,
        )
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as connection:
            connection.connect(os.environ.get("YDOTOOL_SOCKET", "/tmp/.ydotool_socket"))
        print(json.dumps(process, sort_keys=True))
        print(
            "PREFLIGHT PASS; hardware and protocol execution remain suite requirements"
        )
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"PREFLIGHT FAILED: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
