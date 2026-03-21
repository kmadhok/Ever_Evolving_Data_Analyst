from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


VizType = Literal["scorecard", "table", "timeseries"]
SortDir = Literal["asc", "desc"]
RoleName = Literal["de", "analyst", "ds", "consumer"]


class TileSpec(BaseModel):
    tile_id: str
    title: str
    description: str = ""
    view_name: str
    dataset: Optional[str] = None
    viz_type: VizType = "table"
    columns: list[str] = Field(default_factory=list)
    order_by: Optional[str] = None
    order_dir: SortDir = "desc"
    default_limit: int = 100
    roles: list[RoleName] = Field(default_factory=lambda: ["de", "analyst", "ds", "consumer"])


class DashboardSpec(BaseModel):
    dashboard_id: str
    version: int = 1
    title: str
    description: str = ""
    refresh_seconds: int = 60
    tiles: list[TileSpec] = Field(default_factory=list)


class TileDataResponse(BaseModel):
    tile_id: str
    rows: list[dict[str, Any]]


class SignalFeedResponse(BaseModel):
    rows: list[dict[str, Any]]


class DashboardSpecResponse(BaseModel):
    source: Literal["default", "bq"]
    spec: DashboardSpec


class DashboardSpecWriteRequest(BaseModel):
    dashboard_id: str
    spec: DashboardSpec
    created_by: str = "agent"
    notes: str = ""
    deactivate_existing: bool = True


class DashboardSpecWriteResponse(BaseModel):
    accepted: bool
    dashboard_id: str
    version_id: str
    status: str


class UsageEventRequest(BaseModel):
    user_id: Optional[str] = None
    dashboard_id: str
    action: str
    panel_id: Optional[str] = None
    filters: dict[str, Any] = Field(default_factory=dict)
    session_id: Optional[str] = None


class AgentProposal(BaseModel):
    proposal_id: str
    dashboard_id: str
    proposal_type: str
    title: str
    details: str
    priority: Literal["low", "medium", "high"] = "medium"
    generated_at: datetime


class AgentProposalResponse(BaseModel):
    dashboard_id: str
    generated_at: datetime
    proposals: list[AgentProposal]
