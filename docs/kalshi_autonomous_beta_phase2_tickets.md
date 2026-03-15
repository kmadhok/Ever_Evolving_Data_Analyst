# Kalshi Autonomous Beta Phase 2 Tickets

## Purpose

This document breaks Phase 2 of the autonomous beta build plan into executable tickets.

Phase 2 outcome:

- Candidate specs can be validated safely.
- Candidate specs can be activated in a controlled way.
- Failed or invalid changes can be rolled back to the previous active spec.

## Scope

Phase 2 covers:

- spec validation
- spec diffing
- activation workflow
- rollback workflow
- apply-time safety checks in the API layer

Phase 2 does not cover:

- scheduled proposal generation
- automated decisioning
- Airflow orchestration of apply runs
- operator UI
- alert delivery

## Sequencing

Recommended order:

1. `P2-01` validation contract and allowlist rules
2. `P2-02` spec validation module
3. `P2-03` spec diff engine
4. `P2-04` activation service
5. `P2-05` rollback service
6. `P2-06` API endpoints and integration wiring
7. `P2-07` tests and docs

## Ticket List

### P2-01: Define Validation Contract And Auto-Apply Allowlist

Goal:

- Lock down what a valid auto-applicable dashboard spec looks like.

Files:

- `docs/kalshi_autonomous_beta_build_plan.md`
- `docs/kalshi_autonomous_app_architecture.md`
- optionally `apps/api/app/policy.py`

Tasks:

- Define blocking validation rules for candidate specs.
- Define warning-level validation results for operator visibility.
- Define the initial approved dataset/view allowlist for auto-apply.
- Define minimum tile coverage per role.
- Define refresh interval rules for candidate specs.

Acceptance criteria:

- Blocking versus non-blocking validation rules are documented.
- Auto-apply allowlist exists in one canonical location.
- Phase 2 implementation can read a concrete validation contract instead of inferring it.

Estimate:

- 0.5 engineer day

Dependencies:

- Phase 1 complete

### P2-02: Add Spec Validation Module

Goal:

- Validate candidate specs before any activation path can use them.

Files:

- new `apps/api/app/spec_validation.py`
- `apps/api/app/models.py`
- `apps/api/app/repository.py`

Tasks:

- Add validation result models:
  - validation issue
  - validation severity
  - validation summary
- Validate:
  - dashboard id consistency
  - unique tile ids
  - valid roles
  - identifier safety for dataset/view/column/order fields
  - refresh bounds
  - tile row limits within safe cap
  - at least one tile remains visible for each role
- Add repository-backed existence checks for referenced views.
- Add repository-backed existence checks for referenced columns where feasible.

Implementation notes:

- Keep validation results machine-readable.
- Separate pure schema validation from BigQuery-backed existence checks.

Acceptance criteria:

- Invalid candidate specs return structured validation errors.
- Missing views fail validation.
- Zero-tile role outcomes fail validation.
- Duplicate tile ids fail validation.

Estimate:

- 2 engineer days

Dependencies:

- `P2-01`

### P2-03: Add Spec Diff Engine

Goal:

- Compute allowed and forbidden mutations between active and candidate specs.

Files:

- new `apps/api/app/spec_diff.py`
- `apps/api/app/models.py`
- optionally `apps/api/app/policy.py`

Tasks:

- Compare active spec to candidate spec.
- Detect:
  - tile moves
  - role visibility changes
  - tile default-limit changes
  - refresh interval changes
  - newly added views
- Classify diff contents into:
  - allowed auto-apply mutations
  - manual-review mutations
  - forbidden mutations

Acceptance criteria:

- Diff output is machine-readable and stable.
- Pure reordering produces a move-only diff.
- Unknown view additions are flagged.
- Forbidden mutations are surfaced explicitly.

Estimate:

- 1.5 engineer days

Dependencies:

- `P2-01`

### P2-04: Add Safe Activation Service

Goal:

- Activate a validated candidate spec while preserving prior active state.

Files:

- new `apps/api/app/spec_activation.py`
- `apps/api/app/repository.py`
- `apps/api/app/models.py`

Tasks:

- Create service method to:
  - fetch current active spec
  - validate candidate spec
  - persist candidate version
  - deactivate previous active version
  - activate new version
- Record the resulting `version_id`.
- Make activation idempotent when retried with the same proposal context.
- Return structured activation result status.

Implementation notes:

- Preserve a clear path to rollback by keeping prior active version reference.
- Avoid breaking current manual `POST /v1/dashboard/spec` behavior.

Acceptance criteria:

- Valid candidate spec becomes the only active version.
- Previous active version becomes inactive.
- Invalid candidate spec does not mutate active state.
- Activation returns the resulting `version_id`.

Estimate:

- 2 engineer days

Dependencies:

- `P2-02`
- `P2-03`

### P2-05: Add Rollback Service

Goal:

- Restore the last known good active spec after a failed or reverted change.

Files:

- new `apps/api/app/spec_rollback.py`
- `apps/api/app/repository.py`
- `apps/api/app/models.py`

Tasks:

- Add rollback service to:
  - identify current active version
  - identify previous inactive version
  - reactivate previous version
  - mark failed or reverted proposal status appropriately
- Record rollback reason and source proposal id.
- Add clear failure behavior if no rollback target exists.

Acceptance criteria:

- Rollback restores the previous active version.
- Exactly one version is active after rollback.
- Rollback result is machine-readable.
- Missing rollback target returns a clear error.

Estimate:

- 1.5 engineer days

Dependencies:

- `P2-04`

### P2-06: Add API Wiring For Validation, Activation, And Rollback

Goal:

- Expose safe service entry points for candidate validation and lifecycle control.

Files:

- `apps/api/app/main.py`
- `apps/api/app/models.py`
- `apps/api/app/repository.py`
- new service modules from earlier tickets

Tasks:

- Add endpoint to validate a candidate spec without activating it.
- Add endpoint or internal API path to activate a validated candidate spec.
- Add endpoint or internal API path to roll back the active spec.
- Return structured validation or lifecycle results.
- Keep current dashboard-spec read behavior stable.

Implementation notes:

- If public endpoints are too permissive, keep them internal to the operator path and document intended use.

Acceptance criteria:

- Candidate spec can be dry-run validated through API.
- Valid candidate spec can be activated through API or internal service entry point.
- Rollback can be triggered through API or internal service entry point.
- All failure paths return actionable errors.

Estimate:

- 1.5 engineer days

Dependencies:

- `P2-02`
- `P2-04`
- `P2-05`

### P2-07: Add Phase 2 Tests And Docs

Goal:

- Lock in validation, activation, and rollback behavior before automation uses it.

Files:

- new `apps/api/tests/test_spec_validation.py`
- new `apps/api/tests/test_spec_diff.py`
- new `apps/api/tests/test_spec_activation.py`
- new `apps/api/tests/test_spec_rollback.py`
- update docs under `docs`

Tasks:

- Add tests for validation failures.
- Add tests for diff classification.
- Add tests for successful activation.
- Add tests for activation rejection.
- Add tests for rollback behavior.
- Update docs with service and endpoint references.

Acceptance criteria:

- Invalid specs are covered by tests.
- Successful and failed activation paths are covered by tests.
- Rollback behavior is covered by tests.
- Docs match implementation.

Estimate:

- 2 engineer days

Dependencies:

- `P2-02`
- `P2-03`
- `P2-04`
- `P2-05`
- `P2-06`

## Delivery Milestones

### Milestone A: Validation Ready

Includes:

- `P2-01`
- `P2-02`
- `P2-03`

Exit check:

- Candidate specs can be validated and diffed safely.

### Milestone B: Lifecycle Ready

Includes:

- `P2-04`
- `P2-05`
- `P2-06`

Exit check:

- Candidate specs can be activated and rolled back programmatically.

### Milestone C: Hardening Ready

Includes:

- `P2-07`

Exit check:

- Phase 2 behavior is covered by tests and documented.

## Suggested Sprint Cut

- Sprint slice 1:
  - `P2-01`
  - `P2-02`
  - `P2-03`
- Sprint slice 2:
  - `P2-04`
  - `P2-05`
  - `P2-06`
- Sprint slice 3:
  - `P2-07`

## Definition Of Done For Phase 2

Phase 2 is done when all of the following are true:

1. Candidate specs can be validated before activation.
2. Validation distinguishes blocking and non-blocking issues.
3. Spec diffs classify allowed and forbidden mutations.
4. Valid candidate specs can be activated safely.
5. Previous active specs can be restored through rollback.
6. Tests cover validation, activation, and rollback behavior.
7. Docs reflect the implemented validation and lifecycle flows.

## Recommended Next Phase Immediately After Completion

Start Phase 3 with:

1. scheduled proposal generation
2. duplicate suppression
3. automated decisioning
4. apply-orchestration wiring
