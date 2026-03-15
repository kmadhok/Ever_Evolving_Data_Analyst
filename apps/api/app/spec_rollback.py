from __future__ import annotations

from .models import RollbackResponse
from .repository import BigQueryRepository


def rollback_dashboard_spec(
    repository: BigQueryRepository,
    dashboard_id: str,
    *,
    rollback_reason: str,
    rolled_back_by: str,
    proposal_id: str | None = None,
) -> RollbackResponse:
    target = repository.get_previous_active_spec_record(dashboard_id)
    if target is None:
        return RollbackResponse(
            accepted=False,
            dashboard_id=dashboard_id,
            status="failed",
            message="No previous inactive version available for rollback.",
        )

    ok, restored_version_id = repository.restore_spec_version(
        dashboard_id,
        target.version_id,
        restored_by=rolled_back_by,
        reason=rollback_reason,
        source_proposal_id=proposal_id,
    )
    if not ok:
        return RollbackResponse(
            accepted=False,
            dashboard_id=dashboard_id,
            status="failed",
            message="Rollback activation failed.",
        )

    if proposal_id:
        try:
            repository.transition_proposal_status(proposal_id, "rolled_back")
        except ValueError:
            pass

    return RollbackResponse(
        accepted=True,
        dashboard_id=dashboard_id,
        restored_version_id=restored_version_id,
        status="rolled_back",
        message="Previous spec version restored.",
    )
