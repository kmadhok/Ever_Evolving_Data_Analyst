#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="$ROOT_DIR/apps/api/.venv/bin/python"
SCHEDULER_SCRIPT="$ROOT_DIR/scripts/scheduler.py"
GOOGLE_CREDENTIALS_PATH="${GOOGLE_APPLICATION_CREDENTIALS:-$ROOT_DIR/credentials_brainrot.json}"
KALSHI_KEY_PATH="${KALSHI_PRIVATE_KEY_PATH:-$ROOT_DIR/kalshi_private_key.pem}"

if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "Missing executable venv python: $VENV_PYTHON" >&2
  echo "Run 'make api-setup' first." >&2
  exit 1
fi

if [[ ! -f "$GOOGLE_CREDENTIALS_PATH" ]]; then
  echo "Missing Google credentials file: $GOOGLE_CREDENTIALS_PATH" >&2
  exit 1
fi

if [[ ! -f "$KALSHI_KEY_PATH" ]]; then
  echo "Missing Kalshi private key file: $KALSHI_KEY_PATH" >&2
  exit 1
fi

"$VENV_PYTHON" - <<PY
import importlib
import importlib.util
from pathlib import Path

required_modules = [
    "apscheduler",
    "websocket",
    "requests",
    "cryptography",
    "google.cloud.bigquery",
]

for module_name in required_modules:
    importlib.import_module(module_name)

scheduler_path = Path(r"$SCHEDULER_SCRIPT")
spec = importlib.util.spec_from_file_location("repo_scheduler", scheduler_path)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)
print("scheduler smoke check passed")
PY
