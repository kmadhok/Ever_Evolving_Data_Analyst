#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-.env.airflow.local}"
DAG_ID="${1:?usage: scripts/trigger_airflow_dag.sh <dag_id>}"

cd "${REPO_ROOT}"
docker compose --env-file "${ENV_FILE}" -f docker-compose.airflow.yml exec airflow-webserver airflow dags trigger "${DAG_ID}"
