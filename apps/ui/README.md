# React UI (`apps/ui`)

Dynamic dashboard that renders tile layout from FastAPI `dashboard spec`.

## Run

```bash
cd /Users/kanumadhok/Downloads/code/Ever_Evolving_Software/apps/ui
npm install
VITE_API_BASE_URL=http://localhost:8000 npm run dev
```

## Tests

```bash
cd /Users/kanumadhok/Downloads/code/Ever_Evolving_Software/apps/ui
npm run test -- --run
npm run test:e2e
```

## Notes

- UI does not hardcode chart list; it reads tiles from `/v1/dashboard/spec`.
- Role routes are built-in: `/de`, `/analyst`, `/ds` (tabs update route + refetch role spec).
- Operator route is built-in: `/ops` for governed proposals, spec versions, manual decisions, apply, rollback, and autonomy-cycle runs.
- Operator console shows API base, spec source, last run timestamp, and last successful live validation timestamp.
- Each tile data fetch is resolved from `/v1/dashboard/tile/{tile_id}`.
- Usage events are logged via `/v1/usage/events`.
- Agent proposals are shown from `/v1/agent/proposals`.
