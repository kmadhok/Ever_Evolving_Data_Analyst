#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCHEDULER_SCRIPT="$ROOT_DIR/scripts/scheduler.py"
VENV_PYTHON="$ROOT_DIR/apps/api/.venv/bin/python"

if [[ -x "$VENV_PYTHON" ]]; then
  exec "$VENV_PYTHON" "$SCHEDULER_SCRIPT"
fi

if command -v python3 >/dev/null 2>&1; then
  if python3 -c "import apscheduler" >/dev/null 2>&1; then
    exec python3 "$SCHEDULER_SCRIPT"
  fi
fi

echo "No usable Python runtime found for scheduler." >&2
echo "Expected a working venv at $VENV_PYTHON, or a system python3 with APScheduler installed." >&2
echo "Recreate the repo venv on this machine and install apps/api/requirements.txt." >&2
exit 1
