from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .autonomy_service import governance_summary, materialize_candidate_spec, run_autonomy_cycle
from .models import (
    AgentProposalResponse,
    ApplyProposalRequest,
    ApplyProposalResponse,
    AutonomyRunResponse,
    DashboardSpec,
    DashboardSpecResponse,
    DashboardSpecVersionListResponse,
    DecisionPayload,
    DashboardSpecValidationRequest,
    DashboardSpecValidationResponse,
    GovernanceSummaryResponse,
    DashboardSpecWriteRequest,
    DashboardSpecWriteResponse,
    GovernanceDecisionRequest,
    GovernanceDecisionResponse,
    GovernedProposalListResponse,
    RollbackRequest,
    RollbackResponse,
    SignalFeedResponse,
    TileDataResponse,
    UsageEventRequest,
)
from .policy import POLICY_VERSION
from .repository import BigQueryRepository
from .spec_activation import activate_candidate_spec
from .spec_rollback import rollback_dashboard_spec
from .spec_validation import validate_dashboard_spec

settings = get_settings()

app = FastAPI(title="Kalshi Autonomous API", version="0.1.0")
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


@app.post("/v1/dashboard/spec", response_model=DashboardSpecWriteResponse)
def write_dashboard_spec(
    request: DashboardSpecWriteRequest,
    repository: BigQueryRepository = Depends(get_repo),
) -> DashboardSpecWriteResponse:
    ok, version_id, status = repository.save_dashboard_spec(request)
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


@app.get("/v1/agent/proposals", response_model=AgentProposalResponse)
def agent_proposals(
    dashboard_id: str = Query(default=settings.default_dashboard_id),
    persist: bool = Query(default=True),
    repository: BigQueryRepository = Depends(get_repo),
) -> AgentProposalResponse:
    proposals = repository.generate_agent_proposals(dashboard_id)
    if persist:
        repository.persist_proposals(proposals)
    return AgentProposalResponse(
        dashboard_id=dashboard_id,
        generated_at=datetime.now(timezone.utc),
        proposals=proposals,
    )


@app.get("/v1/governance/proposals", response_model=GovernedProposalListResponse)
def governance_proposals(
    dashboard_id: str = Query(default=settings.default_dashboard_id),
    limit: int = Query(default=20, ge=1, le=100),
    repository: BigQueryRepository = Depends(get_repo),
) -> GovernedProposalListResponse:
    proposals = repository.list_governed_proposals(dashboard_id=dashboard_id, limit=limit)
    return GovernedProposalListResponse(dashboard_id=dashboard_id, proposals=proposals)


@app.post("/v1/governance/proposals/{proposal_id}/decision", response_model=GovernanceDecisionResponse)
def governance_decision(
    proposal_id: str,
    request: GovernanceDecisionRequest,
    repository: BigQueryRepository = Depends(get_repo),
) -> GovernanceDecisionResponse:
    try:
        proposal = repository.get_governed_proposal(proposal_id)
        if proposal is None:
            raise HTTPException(status_code=404, detail=f"Unknown proposal_id: {proposal_id}")
        payload = DecisionPayload(
            proposal_id=proposal_id,
            dashboard_id=request.dashboard_id,
            decision=request.decision,
            decided_by=request.decided_by,
            decision_reason=request.decision_reason,
            decided_at=datetime.now(timezone.utc),
            policy_version=POLICY_VERSION,
            candidate_version_id=request.candidate_version_id,
        )
        ok = repository.record_proposal_decision(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not ok:
        raise HTTPException(status_code=500, detail="Failed to record proposal decision.")

    if request.decision == "reject":
        repository.transition_proposal_status(proposal_id, "rejected")

    updated = repository.get_governed_proposal(proposal_id)
    if updated is None:
        raise HTTPException(status_code=500, detail="Decision recorded but proposal could not be reloaded.")

    return GovernanceDecisionResponse(
        accepted=True,
        proposal_id=proposal_id,
        status=updated.status,
        decision=request.decision,
        policy_version=POLICY_VERSION,
    )


@app.post("/v1/dashboard/spec/validate", response_model=DashboardSpecValidationResponse)
def validate_spec(
    request: DashboardSpecValidationRequest,
    repository: BigQueryRepository = Depends(get_repo),
) -> DashboardSpecValidationResponse:
    return validate_dashboard_spec(request.dashboard_id, request.spec, repository)


@app.get("/v1/governance/spec-versions", response_model=DashboardSpecVersionListResponse)
def governance_spec_versions(
    dashboard_id: str = Query(default=settings.default_dashboard_id),
    limit: int = Query(default=20, ge=1, le=100),
    repository: BigQueryRepository = Depends(get_repo),
) -> DashboardSpecVersionListResponse:
    return DashboardSpecVersionListResponse(
        dashboard_id=dashboard_id,
        versions=repository.list_spec_versions(dashboard_id=dashboard_id, limit=limit),
    )


@app.get("/v1/governance/summary", response_model=GovernanceSummaryResponse)
def governance_status_summary(
    dashboard_id: str = Query(default=settings.default_dashboard_id),
    repository: BigQueryRepository = Depends(get_repo),
) -> GovernanceSummaryResponse:
    return governance_summary(repository, dashboard_id)


@app.post("/v1/governance/run", response_model=AutonomyRunResponse)
def governance_run(
    dashboard_id: str = Query(default=settings.default_dashboard_id),
    repository: BigQueryRepository = Depends(get_repo),
) -> AutonomyRunResponse:
    return run_autonomy_cycle(repository, dashboard_id)


@app.post("/v1/governance/apply", response_model=ApplyProposalResponse)
def governance_apply(
    request: ApplyProposalRequest,
    repository: BigQueryRepository = Depends(get_repo),
) -> ApplyProposalResponse:
    proposal = repository.get_governed_proposal(request.proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail=f"Unknown proposal_id: {request.proposal_id}")
    if proposal.dashboard_id != request.dashboard_id:
        raise HTTPException(status_code=400, detail="Proposal dashboard_id mismatch.")
    if proposal.status != "decided":
        raise HTTPException(status_code=400, detail="Only decided proposals can be applied.")
    candidate = materialize_candidate_spec(repository, proposal.payload)
    try:
        return activate_candidate_spec(
            repository,
            proposal.payload,
            candidate,
            activated_by=request.activated_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/v1/governance/rollback", response_model=RollbackResponse)
def governance_rollback(
    request: RollbackRequest,
    repository: BigQueryRepository = Depends(get_repo),
) -> RollbackResponse:
    response = rollback_dashboard_spec(
        repository,
        request.dashboard_id,
        rollback_reason=request.rollback_reason,
        rolled_back_by=request.rolled_back_by,
        proposal_id=request.proposal_id,
    )
    if not response.accepted:
        raise HTTPException(status_code=400, detail=response.message)
    return response
