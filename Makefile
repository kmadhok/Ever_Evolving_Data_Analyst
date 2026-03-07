ENV_FILE ?= .env.airflow.local

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

ui-dev:
	cd apps/ui && npm install && npm run dev
