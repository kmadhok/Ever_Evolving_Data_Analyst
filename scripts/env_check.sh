#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${REPO_ROOT}/apps/api/.venv/bin/python"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="python3"
fi

echo "Running API environment check"
(
  cd "${REPO_ROOT}/apps/api"
  "${PYTHON_BIN}" -m app.cli env-check
)

echo "Checking Node.js"
node --version

echo "Checking npm"
npm --version
