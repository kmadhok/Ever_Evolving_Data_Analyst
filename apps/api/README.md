# FastAPI Service (`apps/api`)

Spec-driven API for the autonomous Kalshi dashboard.

## Run

```bash
cd /Users/kanumadhok/Downloads/code/Ever_Evolving_Software/apps/api
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## Key Endpoints

- `GET /health`
- `GET /v1/dashboard/spec?role=de|analyst|ds`
- `POST /v1/dashboard/spec`
- `GET /v1/dashboard/tile/{tile_id}`
- `POST /v1/usage/events`
- `GET /v1/agent/proposals?persist=true`

## Notes

- Dashboard UI reads `spec` and renders tiles dynamically.
- Dashboard UI routes (`/de`, `/analyst`, `/ds`) call role-filtered spec.
- Agent proposals are generated from usage + pipeline health and stored in `kalshi_ops.agent_proposals`.
- If `kalshi_ops.dashboard_spec_versions` is empty/unavailable, API falls back to built-in default spec.
