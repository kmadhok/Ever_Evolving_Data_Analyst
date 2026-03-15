from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


VizType = Literal["scorecard", "table", "timeseries"]
SortDir = Literal["asc", "desc"]
RoleName = Literal["de", "analyst", "ds", "consumer"]
ProposalPriority = Literal["low", "medium", "high"]
ProposalStatus = Literal["proposed", "decided", "applied", "rejected", "failed", "rolled_back"]
DecisionName = Literal["approve_auto", "approve_manual", "reject", "needs_review"]
RiskLevel = Literal["low", "medium", "high"]
DecidedBy = Literal["autonomy_policy", "human_operator", "system"]


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
    priority: ProposalPriority = "medium"
    generated_at: datetime


class AgentProposalResponse(BaseModel):
    dashboard_id: str
    generated_at: datetime
    proposals: list[AgentProposal]


class SourceSignals(BaseModel):
    model_config = ConfigDict(extra="allow")

    metrics: dict[str, Any] = Field(default_factory=dict)


class TileMove(BaseModel):
    tile_id: str
    from_index: Optional[int] = None
    to_index: int


class RoleVisibilityChange(BaseModel):
    tile_id: str
    role: RoleName
    visible: bool


class TileLimitChange(BaseModel):
    tile_id: str
    from_limit: Optional[int] = Field(default=None, alias="from")
    to_limit: int = Field(alias="to")


class RefreshSecondsChange(BaseModel):
    from_seconds: Optional[int] = Field(default=None, alias="from")
    to_seconds: int = Field(alias="to")


class SpecDiff(BaseModel):
    move_tiles: list[TileMove] = Field(default_factory=list)
    role_visibility_changes: list[RoleVisibilityChange] = Field(default_factory=list)
    tile_limit_changes: list[TileLimitChange] = Field(default_factory=list)
    update_refresh_seconds: Optional[RefreshSecondsChange] = None
    notes: list[str] = Field(default_factory=list)


class ProposalPayload(BaseModel):
    proposal_id: str
    dashboard_id: str
    proposal_type: str
    title: str
    details: str
    priority: ProposalPriority = "medium"
    status: ProposalStatus = "proposed"
    risk_level: RiskLevel = "medium"
    policy_version: str
    source_signals: SourceSignals = Field(default_factory=SourceSignals)
    spec_diff: SpecDiff = Field(default_factory=SpecDiff)
    idempotency_key: str
    created_at: datetime
    candidate_version_id: Optional[str] = None


class DecisionPayload(BaseModel):
    proposal_id: str
    dashboard_id: str
    decision: DecisionName
    decided_by: DecidedBy
    decision_reason: str
    decided_at: datetime
    policy_version: str
    candidate_version_id: Optional[str] = None


class GovernedProposalRecord(BaseModel):
    proposal_id: str
    dashboard_id: str
    proposal_type: str
    status: ProposalStatus
    rationale: str = ""
    created_at: datetime
    risk_level: RiskLevel
    policy_version: str
    idempotency_key: str
    payload: ProposalPayload
    decision: Optional[DecisionPayload] = None


class GovernedProposalListResponse(BaseModel):
    dashboard_id: str
    proposals: list[GovernedProposalRecord]


class GovernanceDecisionRequest(BaseModel):
    dashboard_id: str
    decision: DecisionName
    decided_by: DecidedBy = "human_operator"
    decision_reason: str
    candidate_version_id: Optional[str] = None


class GovernanceDecisionResponse(BaseModel):
    accepted: bool
    proposal_id: str
    status: ProposalStatus
    decision: DecisionName
    policy_version: str


ValidationSeverity = Literal["error", "warning"]


class ValidationIssue(BaseModel):
    severity: ValidationSeverity
    code: str
    message: str
    field: Optional[str] = None


class DashboardSpecValidationRequest(BaseModel):
    dashboard_id: str
    spec: DashboardSpec


class DashboardSpecValidationResponse(BaseModel):
    dashboard_id: str
    valid: bool
    issues: list[ValidationIssue] = Field(default_factory=list)


class DashboardSpecVersionRecord(BaseModel):
    dashboard_id: str
    version_id: str
    status: str
    created_at: datetime
    created_by: str = ""
    notes: str = ""
    source_proposal_id: Optional[str] = None
    previous_version_id: Optional[str] = None
    spec: Optional[DashboardSpec] = None


class DashboardSpecVersionListResponse(BaseModel):
    dashboard_id: str
    versions: list[DashboardSpecVersionRecord]


class ApplyProposalRequest(BaseModel):
    dashboard_id: str
    proposal_id: str
    activated_by: str = "autonomy_policy"


class ApplyProposalResponse(BaseModel):
    accepted: bool
    proposal_id: str
    dashboard_id: str
    version_id: Optional[str] = None
    status: ProposalStatus
    validation: Optional[DashboardSpecValidationResponse] = None
    message: str = ""


class RollbackRequest(BaseModel):
    dashboard_id: str
    proposal_id: Optional[str] = None
    rollback_reason: str
    rolled_back_by: str = "human_operator"


class RollbackResponse(BaseModel):
    accepted: bool
    dashboard_id: str
    restored_version_id: Optional[str] = None
    status: str
    message: str = ""


class AutonomyRunRecord(BaseModel):
    run_id: str
    dashboard_id: str
    mode: str = "scheduled"
    status: str = "completed"
    policy_version: str
    generated: int = 0
    decided: int = 0
    applied: int = 0
    rejected: int = 0
    failed: int = 0
    generated_proposal_ids: list[str] = Field(default_factory=list)
    decided_proposal_ids: list[str] = Field(default_factory=list)
    applied_version_ids: list[str] = Field(default_factory=list)
    validation_issues: list[ValidationIssue] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    started_at: datetime
    completed_at: datetime
    message: str = ""


class AutonomyRunResponse(BaseModel):
    run_id: Optional[str] = None
    dashboard_id: str
    mode: str = "scheduled"
    status: str = "completed"
    policy_version: str
    generated: int = 0
    decided: int = 0
    applied: int = 0
    rejected: int = 0
    failed: int = 0
    generated_proposal_ids: list[str] = Field(default_factory=list)
    decided_proposal_ids: list[str] = Field(default_factory=list)
    applied_version_ids: list[str] = Field(default_factory=list)
    validation_issues: list[ValidationIssue] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    messages: list[str] = Field(default_factory=list)


class LiveValidationRunRecord(BaseModel):
    validation_id: str
    dashboard_id: str
    environment: str
    status: str
    checked_at: datetime
    issues: list[ValidationIssue] = Field(default_factory=list)
    checks: dict[str, Any] = Field(default_factory=dict)
    message: str = ""


class GovernanceSummaryResponse(BaseModel):
    dashboard_id: str
    active_version_id: Optional[str] = None
    previous_version_id: Optional[str] = None
    proposal_backlog: int = 0
    decided_pending_apply: int = 0
    pending_review_count: int = 0
    decided_count: int = 0
    applied_count: int = 0
    rejected_count: int = 0
    failed_count: int = 0
    rolled_back_count: int = 0
    latest_active_version_id: Optional[str] = None
    last_run_at: Optional[datetime] = None
    last_apply_at: Optional[datetime] = None
    last_applied_at: Optional[datetime] = None
    last_rollback_at: Optional[datetime] = None
    last_successful_live_validation_at: Optional[datetime] = None
