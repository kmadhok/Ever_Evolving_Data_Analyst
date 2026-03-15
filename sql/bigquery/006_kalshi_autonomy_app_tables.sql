-- BigQuery Standard SQL
-- App-layer autonomy tables for spec evolution and proposal workflow.
-- Project: brainrot-453319
--
-- Canonical proposal statuses:
--   proposed, decided, applied, rejected, failed, rolled_back
-- Canonical decision values:
--   approve_auto, approve_manual, reject, needs_review
-- Canonical risk levels:
--   low, medium, high
-- Canonical decided_by values:
--   autonomy_policy, human_operator, system

CREATE TABLE IF NOT EXISTS `brainrot-453319.kalshi_ops.dashboard_spec_versions` (
  dashboard_id STRING NOT NULL,
  version_id STRING NOT NULL,
  spec_json JSON NOT NULL,
  status STRING NOT NULL,
  created_at TIMESTAMP NOT NULL,
  created_by STRING,
  notes STRING,
  source_proposal_id STRING,
  previous_version_id STRING
)
PARTITION BY DATE(created_at)
CLUSTER BY dashboard_id, status;

ALTER TABLE `brainrot-453319.kalshi_ops.dashboard_spec_versions`
ADD COLUMN IF NOT EXISTS source_proposal_id STRING;

ALTER TABLE `brainrot-453319.kalshi_ops.dashboard_spec_versions`
ADD COLUMN IF NOT EXISTS previous_version_id STRING;

CREATE TABLE IF NOT EXISTS `brainrot-453319.kalshi_ops.agent_proposals` (
  proposal_id STRING NOT NULL,
  dashboard_id STRING NOT NULL,
  proposal_type STRING NOT NULL,
  proposal_json JSON NOT NULL,
  rationale STRING,
  status STRING NOT NULL,
  risk_level STRING,
  policy_version STRING,
  idempotency_key STRING,
  created_at TIMESTAMP NOT NULL
)
PARTITION BY DATE(created_at)
CLUSTER BY dashboard_id, status, proposal_type;

ALTER TABLE `brainrot-453319.kalshi_ops.agent_proposals`
ADD COLUMN IF NOT EXISTS risk_level STRING;

ALTER TABLE `brainrot-453319.kalshi_ops.agent_proposals`
ADD COLUMN IF NOT EXISTS policy_version STRING;

ALTER TABLE `brainrot-453319.kalshi_ops.agent_proposals`
ADD COLUMN IF NOT EXISTS idempotency_key STRING;

CREATE TABLE IF NOT EXISTS `brainrot-453319.kalshi_ops.agent_decisions` (
  proposal_id STRING NOT NULL,
  dashboard_id STRING NOT NULL,
  decision STRING NOT NULL,
  decided_by STRING,
  decision_reason STRING,
  policy_version STRING,
  candidate_version_id STRING,
  decided_at TIMESTAMP NOT NULL
)
PARTITION BY DATE(decided_at)
CLUSTER BY dashboard_id, decision;

ALTER TABLE `brainrot-453319.kalshi_ops.agent_decisions`
ADD COLUMN IF NOT EXISTS policy_version STRING;

ALTER TABLE `brainrot-453319.kalshi_ops.agent_decisions`
ADD COLUMN IF NOT EXISTS candidate_version_id STRING;

CREATE TABLE IF NOT EXISTS `brainrot-453319.kalshi_ops.autonomy_runs` (
  run_id STRING NOT NULL,
  dashboard_id STRING NOT NULL,
  mode STRING NOT NULL,
  status STRING NOT NULL,
  policy_version STRING NOT NULL,
  generated_count INT64 NOT NULL,
  decided_count INT64 NOT NULL,
  applied_count INT64 NOT NULL,
  rejected_count INT64 NOT NULL,
  failed_count INT64 NOT NULL,
  generated_proposal_ids JSON,
  decided_proposal_ids JSON,
  applied_version_ids JSON,
  validation_issues_json JSON,
  errors_json JSON,
  message STRING,
  started_at TIMESTAMP NOT NULL,
  completed_at TIMESTAMP NOT NULL
)
PARTITION BY DATE(completed_at)
CLUSTER BY dashboard_id, status, mode;

CREATE TABLE IF NOT EXISTS `brainrot-453319.kalshi_ops.live_validation_runs` (
  validation_id STRING NOT NULL,
  dashboard_id STRING NOT NULL,
  environment STRING NOT NULL,
  status STRING NOT NULL,
  issues_json JSON,
  checks_json JSON,
  message STRING,
  checked_at TIMESTAMP NOT NULL
)
PARTITION BY DATE(checked_at)
CLUSTER BY dashboard_id, status, environment;
