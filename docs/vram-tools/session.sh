#!/bin/bash
set -euo pipefail
DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec python3 "$DIR/session.py" serve "${CTL:?set CTL to a fresh capture directory}" --build "${1:?pass the expected compositor binary}" --pid "${COMPOSITOR_PID:?set COMPOSITOR_PID explicitly}"
