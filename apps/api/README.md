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
- `POST /v1/dashboard/spec/validate`
- `GET /v1/dashboard/tile/{tile_id}`
- `POST /v1/usage/events`
- `GET /v1/agent/proposals?persist=true`
- `GET /v1/governance/proposals`
- `POST /v1/governance/proposals/{proposal_id}/decision`
- `GET /v1/governance/spec-versions`
- `GET /v1/governance/summary`
- `POST /v1/governance/run`
- `POST /v1/governance/apply`
- `POST /v1/governance/rollback`

## CLI

```bash
cd /Users/kanumadhok/Downloads/code/Ever_Evolving_Software/apps/api
./.venv/bin/python -m app.cli env-check
./.venv/bin/python -m app.cli validate-live --dashboard-id kalshi_autonomous_v1
./.venv/bin/python -m app.cli governance-run --dashboard-id kalshi_autonomous_v1
./.venv/bin/python -m app.cli governance-summary --dashboard-id kalshi_autonomous_v1
```

## Tests

```bash
cd /Users/kanumadhok/Downloads/code/Ever_Evolving_Software/apps/api
./.venv/bin/python -m unittest discover -s tests -v
```

## Notes

- Dashboard UI reads `spec` and renders tiles dynamically.
- Dashboard UI routes (`/de`, `/analyst`, `/ds`) call role-filtered spec.
- Live validation writes metadata into `kalshi_ops.live_validation_runs`.
- CLI and API governance runs write metadata into `kalshi_ops.autonomy_runs`.
- Agent proposals are generated from usage + pipeline health and stored in `kalshi_ops.agent_proposals`.
- Governed proposal history and decisions are exposed separately from the presentation-oriented proposal cards endpoint.
- The operator loop now supports dry-run validation, governed proposal decisions, manual apply, rollback, and scheduled autonomy-cycle execution.
- If `kalshi_ops.dashboard_spec_versions` is empty/unavailable, API falls back to built-in default spec.
