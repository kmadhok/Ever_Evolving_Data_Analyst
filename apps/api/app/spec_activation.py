from __future__ import annotations

from .models import ApplyProposalResponse, DashboardSpecValidationResponse, DashboardSpecWriteRequest, ProposalPayload
from .repository import BigQueryRepository
from .spec_validation import validate_dashboard_spec


def activate_candidate_spec(
    repository: BigQueryRepository,
    payload: ProposalPayload,
    candidate_spec,
    *,
    activated_by: str,
) -> ApplyProposalResponse:
    validation = validate_dashboard_spec(payload.dashboard_id, candidate_spec, repository)
    if not validation.valid:
        try:
            repository.transition_proposal_status(payload.proposal_id, "failed")
        except ValueError:
            pass
        return ApplyProposalResponse(
            accepted=False,
            proposal_id=payload.proposal_id,
            dashboard_id=payload.dashboard_id,
            status="failed",
            validation=validation,
            message="Candidate spec failed validation.",
        )

    active = repository.get_active_spec_record(payload.dashboard_id)
    ok, version_id, _ = repository.activate_dashboard_spec(
        DashboardSpecWriteRequest(
            dashboard_id=payload.dashboard_id,
            spec=candidate_spec,
            created_by=activated_by,
            notes=f'{{"source_proposal_id":"{payload.proposal_id}"}}',
            deactivate_existing=True,
        ),
        source_proposal_id=payload.proposal_id,
        previous_version_id=active.version_id if active else None,
    )
    if not ok:
        try:
            repository.transition_proposal_status(payload.proposal_id, "failed")
        except ValueError:
            pass
        return ApplyProposalResponse(
            accepted=False,
            proposal_id=payload.proposal_id,
            dashboard_id=payload.dashboard_id,
            status="failed",
            validation=validation,
            message="Failed to activate validated candidate spec.",
        )

    repository.transition_proposal_status(payload.proposal_id, "applied")
    return ApplyProposalResponse(
        accepted=True,
        proposal_id=payload.proposal_id,
        dashboard_id=payload.dashboard_id,
        version_id=version_id,
        status="applied",
        validation=validation,
        message="Candidate spec activated.",
    )
