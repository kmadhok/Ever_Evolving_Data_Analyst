from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from .models import (
    AgentProposal,
    AutonomyRunRecord,
    ApplyProposalResponse,
    AutonomyRunResponse,
    DecisionPayload,
    GovernanceSummaryResponse,
    ProposalPayload,
    RollbackResponse,
    SourceSignals,
    SpecDiff,
)
from .policy import POLICY_VERSION, classify_risk_for_proposal_type, decision_for_payload, is_auto_apply_decision
from .repository import BigQueryRepository
from .spec_activation import activate_candidate_spec
from .spec_diff import diff_specs
from .spec_rollback import rollback_dashboard_spec

logger = logging.getLogger(__name__)

AUTO_ROLLBACK_MAX_RETRIES = 1
VERIFICATION_TIMEOUT_SECONDS = 30


def materialize_candidate_spec(repository: BigQueryRepository, payload: ProposalPayload):
    spec, _ = repository.get_dashboard_spec(payload.dashboard_id)
    candidate = spec.model_copy(deep=True)

    for move in payload.spec_diff.move_tiles:
        current_index = next((idx for idx, tile in enumerate(candidate.tiles) if tile.tile_id == move.tile_id), None)
        if current_index is None:
            continue
        tile = candidate.tiles.pop(current_index)
        insert_at = max(0, min(move.to_index, len(candidate.tiles)))
        candidate.tiles.insert(insert_at, tile)

    if payload.spec_diff.update_refresh_seconds is not None:
        candidate.refresh_seconds = payload.spec_diff.update_refresh_seconds.to_seconds

    for change in payload.spec_diff.tile_limit_changes:
        tile = next((tile for tile in candidate.tiles if tile.tile_id == change.tile_id), None)
        if tile is not None:
            tile.default_limit = change.to_limit

    for change in payload.spec_diff.role_visibility_changes:
        tile = next((tile for tile in candidate.tiles if tile.tile_id == change.tile_id), None)
        if tile is None:
            continue
        if change.visible and change.role not in tile.roles:
            tile.roles.append(change.role)
        if not change.visible and change.role in tile.roles:
            tile.roles = [role for role in tile.roles if role != change.role]

    return candidate


def build_governed_payload(repository: BigQueryRepository, proposal: AgentProposal) -> ProposalPayload:
    current_spec, _ = repository.get_dashboard_spec(proposal.dashboard_id)
    candidate = current_spec.model_copy(deep=True)

    if proposal.proposal_type == "layout_priority":
        tile_id = "pipeline_heartbeat"
        index = next((idx for idx, tile in enumerate(candidate.tiles) if tile.tile_id == tile_id), None)
        if index is not None:
            tile = candidate.tiles.pop(index)
            candidate.tiles.insert(0, tile)
    elif proposal.proposal_type == "ux_adaptation":
        lower_details = proposal.details.lower()
        target_tile_id = None
        for tile in current_spec.tiles:
            if tile.tile_id.lower() in lower_details:
                target_tile_id = tile.tile_id
                break
        if target_tile_id:
            index = next((idx for idx, tile in enumerate(candidate.tiles) if tile.tile_id == target_tile_id), None)
            if index is not None:
                tile = candidate.tiles.pop(index)
                candidate.tiles.insert(0, tile)
    elif proposal.proposal_type == "data_freshness":
        candidate.refresh_seconds = min(candidate.refresh_seconds, 30)

    payload = ProposalPayload(
        proposal_id=proposal.proposal_id,
        dashboard_id=proposal.dashboard_id,
        proposal_type=proposal.proposal_type,
        title=proposal.title,
        details=proposal.details,
        priority=proposal.priority,
        status="proposed",
        risk_level=classify_risk_for_proposal_type(proposal.proposal_type, proposal.priority),
        policy_version=POLICY_VERSION,
        source_signals=SourceSignals(metrics={"generated_from_card": True}),
        spec_diff=diff_specs(current_spec, candidate),
        idempotency_key=repository._proposal_idempotency_key(proposal),
        created_at=proposal.generated_at,
    )
    return payload


def generate_governed_proposals(repository: BigQueryRepository, dashboard_id: str) -> list[str]:
    proposals = repository.generate_agent_proposals(dashboard_id)
    inserted_ids: list[str] = []
    for proposal in proposals:
        payload = build_governed_payload(repository, proposal)
        if repository.insert_governed_proposal(payload):
            inserted_ids.append(proposal.proposal_id)
    return inserted_ids


def decide_governed_proposals(
    repository: BigQueryRepository,
    dashboard_id: str,
    limit: int = 20,
) -> tuple[list[str], int]:
    decided_ids: list[str] = []
    rejected = 0
    for record in repository.list_governed_proposals(dashboard_id, limit=limit):
        if record.status != "proposed":
            continue
        decision = decision_for_payload(record.payload)
        decision_payload = DecisionPayload(
            proposal_id=record.proposal_id,
            dashboard_id=record.dashboard_id,
            decision=decision,
            decided_by="autonomy_policy",
            decision_reason=f"Policy {POLICY_VERSION} classified proposal as {decision}.",
            decided_at=datetime.now(timezone.utc),
            policy_version=POLICY_VERSION,
        )
        if repository.record_proposal_decision(decision_payload):
            decided_ids.append(record.proposal_id)
            if decision == "reject":
                repository.transition_proposal_status(record.proposal_id, "rejected")
                rejected += 1
    return decided_ids, rejected


def apply_auto_approved_proposals(repository: BigQueryRepository, dashboard_id: str, limit: int = 20) -> list[ApplyProposalResponse]:
    results: list[ApplyProposalResponse] = []
    for record in repository.list_governed_proposals(dashboard_id, limit=limit):
        if record.status != "decided" or record.decision is None:
            continue
        if not is_auto_apply_decision(record.decision.decision):
            continue
        candidate = materialize_candidate_spec(repository, record.payload)
        results.append(
            activate_candidate_spec(repository, record.payload, candidate, activated_by=record.decision.decided_by)
        )
    return results


def verify_and_rollback_on_failure(
    repository: BigQueryRepository,
    dashboard_id: str,
    applied_results: list[ApplyProposalResponse],
    *,
    environment: str = "auto",
) -> tuple[list[RollbackResponse], list[str]]:
    """Run live validation after apply. Auto-rollback any applied proposals if verification fails."""
    from .live_validation import run_live_validation

    rollbacks: list[RollbackResponse] = []
    verification_issues: list[str] = []

    successful_applies = [r for r in applied_results if r.accepted and r.version_id]
    if not successful_applies:
        return rollbacks, verification_issues

    validation = run_live_validation(
        repository,
        dashboard_id,
        environment=environment,
        write_run=True,
    )

    if validation.status == "passed":
        logger.info("Post-apply verification passed for dashboard %s", dashboard_id)
        return rollbacks, verification_issues

    logger.warning(
        "Post-apply verification FAILED for dashboard %s: %s",
        dashboard_id,
        validation.message,
    )
    verification_issues.extend(
        f"[{issue.severity}] {issue.code}: {issue.message}"
        for issue in validation.issues
        if issue.severity == "error"
    )

    for result in reversed(successful_applies):
        rollback_result = rollback_dashboard_spec(
            repository,
            dashboard_id,
            rollback_reason=f"Auto-rollback: post-apply verification failed. Issues: {'; '.join(verification_issues[:3])}",
            rolled_back_by="autonomy_auto_rollback",
            proposal_id=result.proposal_id,
        )
        rollbacks.append(rollback_result)
        if rollback_result.accepted:
            logger.info(
                "Auto-rollback succeeded for proposal %s, restored version %s",
                result.proposal_id,
                rollback_result.restored_version_id,
            )
        else:
            logger.error(
                "Auto-rollback FAILED for proposal %s: %s",
                result.proposal_id,
                rollback_result.message,
            )
            verification_issues.append(f"Rollback failed for {result.proposal_id}: {rollback_result.message}")

    return rollbacks, verification_issues


def run_autonomy_cycle(repository: BigQueryRepository, dashboard_id: str, *, mode: str = "scheduled") -> AutonomyRunResponse:
    started_at = datetime.now(timezone.utc)
    generated_ids = generate_governed_proposals(repository, dashboard_id)
    decided_ids, rejected = decide_governed_proposals(repository, dashboard_id)
    applied_results = apply_auto_approved_proposals(repository, dashboard_id)
    applied_version_ids = [result.version_id for result in applied_results if result.accepted and result.version_id]
    validation_issues = [
        issue
        for result in applied_results
        for issue in (result.validation.issues if result.validation is not None else [])
    ]
    errors = [result.message for result in applied_results if not result.accepted and result.message]

    # Phase 4: Post-apply verification with auto-rollback
    rollbacks, verification_errors = verify_and_rollback_on_failure(
        repository,
        dashboard_id,
        applied_results,
        environment=mode,
    )
    rolled_back_count = sum(1 for r in rollbacks if r.accepted)
    if verification_errors:
        errors.extend(verification_errors)
    if rolled_back_count > 0:
        logger.warning(
            "Autonomy cycle rolled back %d applied proposals for dashboard %s",
            rolled_back_count,
            dashboard_id,
        )

    completed_at = datetime.now(timezone.utc)

    # Determine final status
    if rolled_back_count > 0:
        status = "completed_with_rollback"
    elif errors:
        status = "completed_with_errors"
    else:
        status = "completed"

    net_applied = sum(1 for result in applied_results if result.accepted) - rolled_back_count
    messages = [result.message for result in applied_results]
    for rb in rollbacks:
        messages.append(f"Rollback: {rb.message}")

    run_record = AutonomyRunRecord(
        run_id=str(uuid.uuid4()),
        dashboard_id=dashboard_id,
        mode=mode,
        status=status,
        policy_version=POLICY_VERSION,
        generated=len(generated_ids),
        decided=len(decided_ids),
        applied=net_applied,
        rejected=rejected,
        failed=sum(1 for result in applied_results if not result.accepted) + rolled_back_count,
        generated_proposal_ids=generated_ids,
        decided_proposal_ids=decided_ids,
        applied_version_ids=[version_id for version_id in applied_version_ids if version_id is not None],
        validation_issues=validation_issues,
        errors=errors,
        started_at=started_at,
        completed_at=completed_at,
        message=f"Autonomy cycle {status.replace('_', ' ')}.",
    )
    repository.record_autonomy_run(run_record)
    return AutonomyRunResponse(
        run_id=run_record.run_id,
        dashboard_id=dashboard_id,
        mode=mode,
        status=status,
        policy_version=POLICY_VERSION,
        generated=len(generated_ids),
        decided=len(decided_ids),
        applied=net_applied,
        rejected=rejected,
        failed=sum(1 for result in applied_results if not result.accepted) + rolled_back_count,
        generated_proposal_ids=generated_ids,
        decided_proposal_ids=decided_ids,
        applied_version_ids=[version_id for version_id in applied_version_ids if version_id is not None],
        validation_issues=validation_issues,
        errors=errors,
        started_at=started_at,
        completed_at=completed_at,
        messages=messages,
    )


def governance_summary(repository: BigQueryRepository, dashboard_id: str) -> GovernanceSummaryResponse:
    versions = repository.list_spec_versions(dashboard_id, limit=20)
    active = next((version for version in versions if version.status == "active"), None)
    previous = next((version for version in versions if version.status == "inactive"), None)
    proposals = repository.list_governed_proposals(dashboard_id, limit=50)
    latest_run = repository.latest_autonomy_run(dashboard_id)
    latest_validation = repository.latest_live_validation_run(dashboard_id)
    applied_versions = [version for version in versions if version.source_proposal_id]
    rollback_versions = [version for version in versions if "rollback_reason" in version.notes]
    return GovernanceSummaryResponse(
        dashboard_id=dashboard_id,
        active_version_id=active.version_id if active else None,
        previous_version_id=previous.version_id if previous else None,
        proposal_backlog=sum(1 for proposal in proposals if proposal.status == "proposed"),
        decided_pending_apply=sum(1 for proposal in proposals if proposal.status == "decided"),
        pending_review_count=sum(
            1
            for proposal in proposals
            if proposal.status == "decided" and proposal.decision is not None and proposal.decision.decision == "needs_review"
        ),
        decided_count=sum(1 for proposal in proposals if proposal.status == "decided"),
        applied_count=sum(1 for proposal in proposals if proposal.status == "applied"),
        rejected_count=sum(1 for proposal in proposals if proposal.status == "rejected"),
        failed_count=sum(1 for proposal in proposals if proposal.status == "failed"),
        rolled_back_count=sum(1 for proposal in proposals if proposal.status == "rolled_back"),
        latest_active_version_id=active.version_id if active else None,
        last_run_at=latest_run.completed_at if latest_run else None,
        last_apply_at=applied_versions[0].created_at if applied_versions else None,
        last_applied_at=applied_versions[0].created_at if applied_versions else None,
        last_rollback_at=rollback_versions[0].created_at if rollback_versions else None,
        last_successful_live_validation_at=(
            latest_validation.checked_at if latest_validation is not None and latest_validation.status == "passed" else None
        ),
    )
