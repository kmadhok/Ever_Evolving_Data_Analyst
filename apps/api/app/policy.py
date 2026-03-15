from __future__ import annotations

from .models import DecisionName, ProposalPayload, ProposalPriority, RiskLevel

POLICY_VERSION = "2026-03-beta-1"
AUTO_APPLY_MIN_REFRESH_SECONDS = 30
AUTO_APPLY_MAX_REFRESH_SECONDS = 300
APPROVED_AUTO_APPLY_DATASETS = {"kalshi_dash", "kalshi_signal"}
LOW_RISK_PROPOSAL_TYPES = {"layout_priority", "ux_adaptation"}
MEDIUM_RISK_PROPOSAL_TYPES = {"data_freshness"}

PRIORITY_TO_RISK: dict[ProposalPriority, RiskLevel] = {
    "low": "low",
    "medium": "medium",
    "high": "high",
}


def risk_level_for_priority(priority: ProposalPriority) -> RiskLevel:
    return PRIORITY_TO_RISK.get(priority, "medium")


def dataset_is_auto_apply_approved(dataset: str) -> bool:
    return dataset in APPROVED_AUTO_APPLY_DATASETS


def classify_risk_for_proposal_type(proposal_type: str, default: RiskLevel = "medium") -> RiskLevel:
    if proposal_type in LOW_RISK_PROPOSAL_TYPES:
        return "low"
    if proposal_type in MEDIUM_RISK_PROPOSAL_TYPES:
        return "medium"
    return default


def decision_for_payload(payload: ProposalPayload) -> DecisionName:
    if payload.risk_level == "low" and payload.proposal_type in LOW_RISK_PROPOSAL_TYPES:
        return "approve_auto"
    if payload.risk_level == "high":
        return "reject"
    return "needs_review"


def is_auto_apply_decision(decision: DecisionName) -> bool:
    return decision == "approve_auto"
