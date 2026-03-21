from __future__ import annotations

from functools import lru_cache
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .models import (
    DashboardSpec,
    DashboardSpecResponse,
    SignalFeedResponse,
    TileDataResponse,
    UsageEventRequest,
)
from .repository import BigQueryRepository

settings = get_settings()

app = FastAPI(title="Kalshi Dashboard API", version="0.2.0")
VALID_ROLES = {"de", "analyst", "ds", "consumer"}

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
        raise HTTPException(
            status_code=400,
            detail=f"Invalid role '{role}'. Use one of: de, analyst, ds, consumer.",
        )
    role_tiles = [tile for tile in spec.tiles if role in tile.roles]
    return spec.model_copy(update={"tiles": role_tiles})


@lru_cache(maxsize=1)
def get_repo() -> BigQueryRepository:
    return BigQueryRepository(settings)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "env": settings.app_env}


@app.get("/v1/dashboard/spec", response_model=DashboardSpecResponse)
def dashboard_spec(
    dashboard_id: str = Query(default=settings.default_dashboard_id),
    role: Optional[str] = Query(default=None),
    repository: BigQueryRepository = Depends(get_repo),
) -> DashboardSpecResponse:
    spec, source = repository.get_dashboard_spec(dashboard_id)
    filtered = _filter_spec_for_role(spec, role)
    return DashboardSpecResponse(source=source, spec=filtered)


@app.get("/v1/dashboard/tile/{tile_id}", response_model=TileDataResponse)
def tile_data(
    tile_id: str,
    dashboard_id: str = Query(default=settings.default_dashboard_id),
    limit: Optional[int] = Query(default=None, ge=1, le=5000),
    repository: BigQueryRepository = Depends(get_repo),
) -> TileDataResponse:
    try:
        rows = repository.fetch_tile_rows(dashboard_id=dashboard_id, tile_id=tile_id, limit=limit)
        return TileDataResponse(tile_id=tile_id, rows=rows)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=f"Failed to fetch tile data: {exc}") from exc


@app.get("/v1/signals/feed", response_model=SignalFeedResponse)
def signal_feed(
    signal_type: Optional[str] = Query(default=None),
    severity: Optional[str] = Query(default=None),
    family: Optional[str] = Query(default=None),
    window: Optional[str] = Query(default=None),
    sort_by: str = Query(default="score"),
    sort_dir: str = Query(default="desc"),
    limit: int = Query(default=50, ge=1, le=500),
    repository: BigQueryRepository = Depends(get_repo),
) -> SignalFeedResponse:
    try:
        rows = repository.fetch_signal_feed(
            signal_type=signal_type,
            severity=severity,
            family=family,
            window=window,
            sort_by=sort_by,
            sort_dir=sort_dir,
            limit=limit,
        )
        return SignalFeedResponse(rows=rows)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=f"Failed to fetch signal feed: {exc}") from exc


@app.post("/v1/usage/events")
def usage_event(
    event: UsageEventRequest,
    repository: BigQueryRepository = Depends(get_repo),
) -> dict[str, bool]:
    ok = repository.record_usage_event(event)
    return {"accepted": ok}
