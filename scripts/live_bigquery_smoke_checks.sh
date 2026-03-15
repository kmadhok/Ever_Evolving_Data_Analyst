#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${REPO_ROOT}/apps/api/.venv/bin/python"
DASHBOARD_ID="${1:-kalshi_autonomous_v1}"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="python3"
fi

cd "${REPO_ROOT}/apps/api"
"${PYTHON_BIN}" -m app.cli validate-live --dashboard-id "${DASHBOARD_ID}"
