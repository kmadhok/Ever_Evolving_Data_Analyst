# Kalshi Autonomous Beta Phase 1 Tickets

## Purpose

This document breaks Phase 1 of the autonomous beta build plan into executable tickets.

Phase 1 outcome:

- Governance state is explicit.
- Proposal and decision payloads are durable and machine-readable.
- Policy rules are documented and enforced by backend code.

## Scope

Phase 1 covers:

- governance schema
- proposal/decision models
- repository write/read behavior
- policy metadata
- status transition rules
- documentation updates

Phase 1 does not cover:

- spec validation
- apply or rollback execution
- Airflow scheduling changes
- UI operator panel

## Sequencing

Recommended order:

1. `P1-01` governance schema contract
2. `P1-02` backend models
3. `P1-03` repository persistence and transition helpers
4. `P1-04` policy module and risk rules
5. `P1-05` API surface for decisions and status reads
6. `P1-06` tests
7. `P1-07` docs and runbook updates

## Ticket List

### P1-01: Extend Governance Table Contract

Goal:

- Formalize the persistence contract for proposals and decisions.

Files:

- `sql/bigquery/006_kalshi_autonomy_app_tables.sql`
- `docs/kalshi_autonomous_app_architecture.md`

Tasks:

- Update `agent_proposals` documentation and intended payload contract to require:
  - `risk_level`
  - `policy_version`
  - `source_signals`
  - `spec_diff`
  - `idempotency_key`
- Update `agent_decisions` contract to require:
  - `policy_version`
  - `candidate_version_id`
  - `decision`
  - `decision_reason`
  - `decided_by`
- Decide whether missing governance fields remain embedded in JSON only or become explicit columns.
- If keeping JSON-centric storage, document that the JSON payload is authoritative and table columns are indexing aids.
- Document allowed values for:
  - proposal status
  - decision
  - risk level
  - decided_by

Implementation notes:

- Avoid a large schema migration unless necessary.
- If new columns are added, they should be additive and nullable for compatibility.

Acceptance criteria:

- SQL file reflects the agreed governance contract.
- Documentation explicitly defines proposal and decision field semantics.
- Allowed enums are written down in one canonical place.

Estimate:

- 1 engineer day

Dependencies:

- none

### P1-02: Add Governance Models

Goal:

- Replace loose proposal metadata handling with typed backend models.

Files:

- `apps/api/app/models.py`

Tasks:

- Add typed enums or `Literal` values for:
  - proposal status
  - decision status
  - risk level
  - decided_by
- Add models for:
  - `ProposalPayload`
  - `DecisionPayload`
  - `SpecDiff`
  - `SourceSignals`
- Add request/response models for:
  - creating a governed proposal
  - recording a decision
  - reading proposal history
- Ensure existing `AgentProposal` remains backward-compatible where needed.

Implementation notes:

- Do not mix human-readable proposal cards with the new persistence contract.
- Keep presentation-oriented fields separate from policy-oriented fields.

Acceptance criteria:

- New models validate the governance payload contract.
- Invalid enum values fail model validation.
- Existing proposal endpoint can still serialize user-visible proposal cards.

Estimate:

- 1 engineer day

Dependencies:

- `P1-01`

### P1-03: Repository Governance Persistence

Goal:

- Centralize proposal and decision persistence logic with explicit status transitions.

Files:

- `apps/api/app/repository.py`

Tasks:

- Add repository methods for:
  - insert governed proposal
  - read latest proposals by dashboard
  - lookup proposal by `proposal_id`
  - record decision
  - transition proposal status
  - reject invalid transition
- Add transition rules:
  - `proposed -> decided`
  - `decided -> applied`
  - `decided -> rejected`
  - `applied -> rolled_back`
  - `proposed|decided|applied -> failed`
- Reject invalid transitions such as:
  - `applied -> proposed`
  - `rejected -> applied`
  - `rolled_back -> applied`
- Persist `policy_version` with every proposal and decision write.
- Store `idempotency_key` in proposal JSON and expose lookup helper for later duplicate suppression work.

Implementation notes:

- Keep existing `generate_agent_proposals()` working until later phases replace the flow.
- Add new repository methods rather than overloading the current lightweight proposal card persistence.

Acceptance criteria:

- Repository enforces valid status transitions.
- Repository rejects missing required governance fields.
- Proposal and decision lookups return typed payloads or clearly structured dicts.

Estimate:

- 1.5 engineer days

Dependencies:

- `P1-01`
- `P1-02`

### P1-04: Policy Module And Risk Classification

Goal:

- Define the first real backend policy contract.

Files:

- new `apps/api/app/policy.py`
- optionally `apps/api/app/config.py`

Tasks:

- Create a policy module that defines:
  - `POLICY_VERSION`
  - allowed auto-apply mutation classes for later phases
  - risk-level classification helpers
  - hard limits:
    - max 1 auto-apply per dashboard per hour
    - max 3 auto-applies per dashboard per day
    - refresh floor 30 seconds
    - refresh ceiling 300 seconds
- Add helper functions for:
  - checking allowed proposal types
  - checking decision eligibility
  - determining when a proposal must be `needs_review`
- Keep policy deterministic and data-free in Phase 1.

Implementation notes:

- This module should not call BigQuery.
- It should be pure business logic and easy to unit test.

Acceptance criteria:

- One canonical `POLICY_VERSION` exists in code.
- Repository and future services can import policy helpers.
- Risk and decision eligibility logic is unit-testable without external services.

Estimate:

- 1 engineer day

Dependencies:

- `P1-02`

### P1-05: API Endpoints For Governance Decisions

Goal:

- Expose a minimal operator/API surface for governed proposal history and decisions.

Files:

- `apps/api/app/main.py`
- `apps/api/app/models.py`
- `apps/api/app/repository.py`

Tasks:

- Add `GET` endpoint for proposal history by dashboard.
- Add `POST` endpoint to record a decision for a proposal.
- Add response payloads that include:
  - proposal id
  - dashboard id
  - status
  - risk level
  - policy version
  - decision summary
- Keep existing `/v1/agent/proposals` endpoint working for current UI cards.
- Make it clear which endpoint is operational/governance oriented versus presentation oriented.

Implementation notes:

- Do not add auth in this phase unless already required by deployment context.
- Add clean error responses for invalid proposal ids and invalid transitions.

Acceptance criteria:

- Operator can fetch recent governed proposals for one dashboard.
- Operator can record a valid decision through API.
- Invalid decisions return 4xx with actionable error text.

Estimate:

- 1.5 engineer days

Dependencies:

- `P1-02`
- `P1-03`
- `P1-04`

### P1-06: Governance Tests

Goal:

- Prevent Phase 1 regressions before more automation is added.

Files:

- new `apps/api/tests/test_models.py`
- new `apps/api/tests/test_repository_governance.py`
- new `apps/api/tests/test_policy.py`
- new `apps/api/tests/test_main_governance.py`

Tasks:

- Add model validation tests for enum and required field enforcement.
- Add repository tests for valid and invalid transitions.
- Add policy tests for risk rules and `needs_review` classification.
- Add API tests for:
  - list proposal history
  - record valid decision
  - reject invalid decision
- Use mocking or fakes around BigQuery interactions.

Implementation notes:

- Keep tests fast and deterministic.
- Prefer repository seam mocking over end-to-end BigQuery dependency for this phase.

Acceptance criteria:

- All new governance tests pass locally.
- Invalid transition cases are covered.
- Policy version persistence is asserted in tests.

Estimate:

- 2 engineer days

Dependencies:

- `P1-02`
- `P1-03`
- `P1-04`
- `P1-05`

### P1-07: Documentation And Runbook Update

Goal:

- Make the governance contract discoverable for future phases.

Files:

- `docs/kalshi_autonomous_app_architecture.md`
- `docs/kalshi_autonomous_beta_build_plan.md`
- `README.md`

Tasks:

- Update architecture doc with governance state machine.
- Link the new Phase 1 governance endpoints if added.
- Update the beta build plan to note Phase 1 implementation references.
- Add a short operator note describing:
  - what a governed proposal is
  - how decisions are recorded
  - what statuses mean

Acceptance criteria:

- Docs reflect the implemented governance contract.
- Another engineer can identify the correct tables, endpoints, and status lifecycle without reading source code first.

Estimate:

- 0.5 engineer day

Dependencies:

- `P1-05`

## Delivery Milestones

### Milestone A: Governance Contract Complete

Includes:

- `P1-01`
- `P1-02`
- `P1-04`

Exit check:

- Typed policy and payload definitions exist.

### Milestone B: Persistence And API Complete

Includes:

- `P1-03`
- `P1-05`

Exit check:

- Proposal and decision records are persisted and queryable.

### Milestone C: Test And Documentation Complete

Includes:

- `P1-06`
- `P1-07`

Exit check:

- Governance behavior is documented and covered by automated tests.

## Suggested Sprint Cut

If one engineer is driving the work, use:

- Sprint slice 1:
  - `P1-01`
  - `P1-02`
  - `P1-04`
- Sprint slice 2:
  - `P1-03`
  - `P1-05`
- Sprint slice 3:
  - `P1-06`
  - `P1-07`

## Definition Of Done For Phase 1

Phase 1 is done when all of the following are true:

1. Proposal and decision payload contracts are typed and documented.
2. Proposal statuses and transitions are enforced in repository or service logic.
3. Policy version is persisted with every proposal and every decision.
4. Operators can read governed proposal history through API.
5. Operators can record decisions through API.
6. Automated tests cover the governance contract and invalid transition cases.
7. Docs reflect the implemented state machine and fields.

## Recommended Next Phase Immediately After Completion

Start Phase 2 with:

1. `spec_validation.py`
2. spec diff calculator
3. safe activation service
4. rollback service
