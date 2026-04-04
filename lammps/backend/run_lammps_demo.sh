#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
python3 server/combined_server.py --host 127.0.0.1 --port 8765

