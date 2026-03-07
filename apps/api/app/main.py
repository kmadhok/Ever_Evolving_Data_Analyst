from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .models import (
    AgentProposalResponse,
    DashboardSpec,
    DashboardSpecResponse,
    DashboardSpecWriteRequest,
    DashboardSpecWriteResponse,
    TileDataResponse,
    UsageEventRequest,
)
from .repository import BigQueryRepository

settings = get_settings()
repo = BigQueryRepository(settings)

app = FastAPI(title="Kalshi Autonomous API", version="0.1.0")
VALID_ROLES = {"de", "analyst", "ds"}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _filter_spec_for_role(spec: DashboardSpec, role: Optional[str]) -> DashboardSpec:
    if role is None or role == "all":
        return spec
    if role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid role '{role}'. Use one of: de, analyst, ds.")
    role_tiles = [tile for tile in spec.tiles if role in tile.roles]
    return spec.model_copy(update={"tiles": role_tiles})


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "env": settings.app_env}


@app.get("/v1/dashboard/spec", response_model=DashboardSpecResponse)
def dashboard_spec(
    dashboard_id: str = Query(default=settings.default_dashboard_id),
    role: Optional[str] = Query(default=None),
) -> DashboardSpecResponse:
    spec, source = repo.get_dashboard_spec(dashboard_id)
    filtered = _filter_spec_for_role(spec, role)
    return DashboardSpecResponse(source=source, spec=filtered)


@app.post("/v1/dashboard/spec", response_model=DashboardSpecWriteResponse)
def write_dashboard_spec(request: DashboardSpecWriteRequest) -> DashboardSpecWriteResponse:
    ok, version_id, status = repo.save_dashboard_spec(request)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to write dashboard spec.")
    return DashboardSpecWriteResponse(
        accepted=True,
        dashboard_id=request.dashboard_id,
        version_id=version_id,
        status=status,
    )


@app.get("/v1/dashboard/tile/{tile_id}", response_model=TileDataResponse)
def tile_data(
    tile_id: str,
    dashboard_id: str = Query(default=settings.default_dashboard_id),
    limit: Optional[int] = Query(default=None, ge=1, le=5000),
) -> TileDataResponse:
    try:
        rows = repo.fetch_tile_rows(dashboard_id=dashboard_id, tile_id=tile_id, limit=limit)
        return TileDataResponse(tile_id=tile_id, rows=rows)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=f"Failed to fetch tile data: {exc}") from exc


@app.post("/v1/usage/events")
def usage_event(event: UsageEventRequest) -> dict[str, bool]:
    ok = repo.record_usage_event(event)
    return {"accepted": ok}


@app.get("/v1/agent/proposals", response_model=AgentProposalResponse)
def agent_proposals(
    dashboard_id: str = Query(default=settings.default_dashboard_id),
    persist: bool = Query(default=True),
) -> AgentProposalResponse:
    proposals = repo.generate_agent_proposals(dashboard_id)
    if persist:
        repo.persist_proposals(proposals)
    return AgentProposalResponse(
        dashboard_id=dashboard_id,
        generated_at=datetime.now(timezone.utc),
        proposals=proposals,
    )
