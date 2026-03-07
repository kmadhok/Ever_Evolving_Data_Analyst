-- BigQuery Standard SQL
-- App-layer autonomy tables for spec evolution and proposal workflow.
-- Project: brainrot-453319

CREATE TABLE IF NOT EXISTS `brainrot-453319.kalshi_ops.dashboard_spec_versions` (
  dashboard_id STRING NOT NULL,
  version_id STRING NOT NULL,
  spec_json JSON NOT NULL,
  status STRING NOT NULL,
  created_at TIMESTAMP NOT NULL,
  created_by STRING,
  notes STRING
)
PARTITION BY DATE(created_at)
CLUSTER BY dashboard_id, status;

CREATE TABLE IF NOT EXISTS `brainrot-453319.kalshi_ops.agent_proposals` (
  proposal_id STRING NOT NULL,
  dashboard_id STRING NOT NULL,
  proposal_type STRING NOT NULL,
  proposal_json JSON NOT NULL,
  rationale STRING,
  status STRING NOT NULL,
  created_at TIMESTAMP NOT NULL
)
PARTITION BY DATE(created_at)
CLUSTER BY dashboard_id, status, proposal_type;

CREATE TABLE IF NOT EXISTS `brainrot-453319.kalshi_ops.agent_decisions` (
  proposal_id STRING NOT NULL,
  dashboard_id STRING NOT NULL,
  decision STRING NOT NULL,
  decided_by STRING,
  decision_reason STRING,
  decided_at TIMESTAMP NOT NULL
)
PARTITION BY DATE(decided_at)
CLUSTER BY dashboard_id, decision;
