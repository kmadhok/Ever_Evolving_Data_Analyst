ENV_FILE ?= .env.airflow.local
PROJECT_ID ?= brainrot-453319
DASHBOARD_ID ?= kalshi_autonomous_v1

airflow-init:
	docker compose --env-file $(ENV_FILE) -f docker-compose.airflow.yml up airflow-init

airflow-up:
	docker compose --env-file $(ENV_FILE) -f docker-compose.airflow.yml up -d airflow-webserver airflow-scheduler

airflow-down:
	docker compose --env-file $(ENV_FILE) -f docker-compose.airflow.yml down

airflow-logs:
	docker compose --env-file $(ENV_FILE) -f docker-compose.airflow.yml logs -f airflow-scheduler airflow-webserver

airflow-ps:
	docker compose --env-file $(ENV_FILE) -f docker-compose.airflow.yml ps

api-setup:
	cd apps/api && python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt

api-dev:
	cd apps/api && . .venv/bin/activate && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

api-test-unit:
	cd apps/api && . .venv/bin/activate && python -m unittest discover -s tests -v

api-test-live:
	./scripts/live_bigquery_smoke_checks.sh $(DASHBOARD_ID)

ui-dev:
	cd apps/ui && npm install && npm run dev

ui-test:
	cd apps/ui && npm run test -- --run

ui-build:
	cd apps/ui && npm run build

ui-e2e:
	cd apps/ui && npm run test:e2e

bq-apply-kalshi-core:
	./apps/api/.venv/bin/python ./scripts/apply_bigquery_sql.py --project-id $(PROJECT_ID) --group kalshi-core

bq-apply-kalshi-signals:
	./apps/api/.venv/bin/python ./scripts/apply_bigquery_sql.py --project-id $(PROJECT_ID) --group kalshi-signals

bq-apply-odds-core:
	./apps/api/.venv/bin/python ./scripts/apply_bigquery_sql.py --project-id $(PROJECT_ID) --group odds-core

airflow-trigger-kalshi:
	./scripts/trigger_airflow_dag.sh kalshi_market_data_autonomous_de_v0

airflow-trigger-odds:
	./scripts/trigger_airflow_dag.sh odds_api_autonomous_de_v0

bq-apply-kalshi-ws:
	./apps/api/.venv/bin/python ./scripts/apply_bigquery_sql.py --project-id $(PROJECT_ID) --group kalshi-ws

airflow-trigger-kalshi-ws:
	./scripts/trigger_airflow_dag.sh kalshi_ws_realtime_v0

airflow-unpause-kalshi-ws:
	docker compose --env-file $(ENV_FILE) -f docker-compose.airflow.yml exec airflow-webserver airflow dags unpause kalshi_ws_realtime_v0

env-check:
	./scripts/env_check.sh
