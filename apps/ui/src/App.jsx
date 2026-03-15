import { useEffect, useMemo, useRef, useState } from 'react';
import {
  API_BASE,
  applyProposal,
  decideGovernanceProposal,
  fetchGovernanceProposals,
  fetchGovernanceSummary,
  fetchProposals,
  fetchSignalFeed,
  fetchSpec,
  fetchSpecVersions,
  fetchTile,
  postUsageEvent,
  rollbackSpec,
  runAutonomyCycle,
} from './api';

const DASHBOARD_ID = 'kalshi_autonomous_v1';
const ROLE_CONFIG = {
  consumer: { label: 'Consumer' },
  de: { label: 'Data Engineer' },
  analyst: { label: 'Analyst' },
  ds: { label: 'Data Scientist' },
  ops: { label: 'Operator' },
};
const SIGNAL_FEED_COLUMNS = [
  'signal_type',
  'signal_window',
  'title',
  'entity_id',
  'market_family',
  'score',
  'severity',
  'signal_ts',
  'explanation_short',
];
const SIGNAL_SORT_OPTIONS = [
  { value: 'score', label: 'Score' },
  { value: 'signal_ts', label: 'Newest' },
  { value: 'signal_type', label: 'Type' },
  { value: 'market_family', label: 'Family' },
];

function normalizeRole(role) {
  return Object.hasOwn(ROLE_CONFIG, role) ? role : 'de';
}

function roleFromPath(pathname) {
  const firstSegment = pathname.split('/').filter(Boolean)[0] || 'de';
  return normalizeRole(firstSegment);
}

function formatCell(value) {
  if (value === null || value === undefined) return '-';
  if (typeof value === 'number') {
    if (Math.abs(value) >= 1000) return value.toLocaleString();
    return Number(value.toFixed(4)).toString();
  }
  return String(value);
}

function formatTimestamp(value) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString();
}

function ScorecardTile({ tile, rows }) {
  const row = rows?.[0] || {};
  return (
    <div className="score-grid">
      {tile.columns.map((column) => (
        <div className="score-item" key={column}>
          <div className="score-label">{column}</div>
          <div className="score-value">{formatCell(row[column])}</div>
        </div>
      ))}
    </div>
  );
}

function TableTile({ tile, rows }) {
  const columns = tile.columns || (rows[0] ? Object.keys(rows[0]) : []);
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column}>{column}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, idx) => (
            <tr key={`${tile.tile_id}-${idx}`}>
              {columns.map((column) => (
                <td key={column}>{formatCell(row[column])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TimeSeriesTile({ tile, rows }) {
  const columns = tile.columns || [];
  const metricCol = columns.find((c) => c !== 'kpi_ts' && c !== 'ts_minute' && c !== 'series_ticker' && c !== 'taker_side');
  const numeric = rows
    .map((r) => Number(r[metricCol]))
    .filter((n) => Number.isFinite(n));
  const maxValue = numeric.length ? Math.max(...numeric) : 1;

  return (
    <div>
      {metricCol && (
        <div className="spark-wrap">
          {rows.slice(0, 40).map((row, idx) => {
            const value = Number(row[metricCol]);
            const width = Number.isFinite(value) ? Math.max(2, Math.round((value / maxValue) * 100)) : 2;
            return (
              <div className="spark-row" key={`${tile.tile_id}-spark-${idx}`}>
                <div className="spark-label">{formatCell(row.kpi_ts || row.ts_minute || idx)}</div>
                <div className="spark-bar" style={{ width: `${width}%` }} />
                <div className="spark-value">{formatCell(value)}</div>
              </div>
            );
          })}
        </div>
      )}
      <TableTile tile={tile} rows={rows.slice(0, 25)} />
    </div>
  );
}

function SignalFeedSection({
  rows,
  loading,
  filters,
  familyOptions,
  onFilterChange,
  onRefresh,
}) {
  return (
    <section className="tile signal-feed">
      <header className="tile-header">
        <div>
          <h3>Signal Feed</h3>
          <p className="tile-description">Unified market-intelligence feed with analyst filters.</p>
        </div>
        <button onClick={onRefresh}>Refresh Signals</button>
      </header>
      <div className="signal-controls">
        <label>
          <span>Type</span>
          <select value={filters.signal_type} onChange={(e) => onFilterChange('signal_type', e.target.value)}>
            <option value="">All</option>
            <option value="probability_shift_24h">Probability</option>
            <option value="volume_spike_6h">Volume</option>
            <option value="volatility_spike_1h">Volatility</option>
            <option value="liquidity_deterioration_latest">Liquidity</option>
            <option value="open_interest_change_12h">Open interest</option>
            <option value="event_reaction_3h">Event reaction</option>
            <option value="cross_market_inconsistency">Cross-market</option>
          </select>
        </label>
        <label>
          <span>Severity</span>
          <select value={filters.severity} onChange={(e) => onFilterChange('severity', e.target.value)}>
            <option value="">All</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </select>
        </label>
        <label>
          <span>Family</span>
          <select value={filters.family} onChange={(e) => onFilterChange('family', e.target.value)}>
            <option value="">All</option>
            {familyOptions.map((family) => (
              <option key={family} value={family}>
                {family}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Window</span>
          <select value={filters.window} onChange={(e) => onFilterChange('window', e.target.value)}>
            <option value="">All</option>
            <option value="latest">Latest</option>
            <option value="24h">24h</option>
            <option value="12h">12h</option>
            <option value="6h">6h</option>
            <option value="3h">3h</option>
            <option value="1h">1h</option>
          </select>
        </label>
        <label>
          <span>Sort</span>
          <select value={filters.sort_by} onChange={(e) => onFilterChange('sort_by', e.target.value)}>
            {SIGNAL_SORT_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Direction</span>
          <select value={filters.sort_dir} onChange={(e) => onFilterChange('sort_dir', e.target.value)}>
            <option value="desc">Desc</option>
            <option value="asc">Asc</option>
          </select>
        </label>
      </div>
      {loading ? <div className="tile-loading">Loading...</div> : null}
      {!loading && rows.length === 0 ? <div className="tile-loading">No signals returned.</div> : null}
      {!loading && rows.length > 0 ? (
        <TableTile tile={{ tile_id: 'signal_feed', columns: SIGNAL_FEED_COLUMNS }} rows={rows} />
      ) : null}
    </section>
  );
}

function AnalystHelp() {
  return (
    <section className="tile analyst-help">
      <header className="tile-header">
        <h3>How To Read These Markets</h3>
      </header>
      <div className="help-copy">
        <p>Each row is one side in one event. Higher contract price means that side is more likely to happen.</p>
        <p>`favorite` means highest current price in that event. `underdog` means lower-priced side in a two-side market.</p>
        <p>`YES` trades mean people are buying that side. `NO` trades mean people are betting against that side.</p>
      </div>
    </section>
  );
}

function ConsumerHelp() {
  return (
    <section className="tile analyst-help">
      <header className="tile-header">
        <h3>How To Read This</h3>
      </header>
      <div className="help-copy">
        <p>Each row is one side in one event. `yes_now` is the current price for that side. `no_now` is the price against that side.</p>
        <p>`favorite` means that side is more likely right now. `underdog` means less likely right now.</p>
        <p>`result` only appears when we can tell who won from settled market prices. If it is blank, the event is still unresolved here.</p>
      </div>
    </section>
  );
}

function StatusBadge({ status }) {
  const badgeClass =
    status === 'applied' || status === 'active'
      ? 'badge-success'
      : status === 'failed' || status === 'rolled_back'
        ? 'badge-danger'
        : status === 'proposed' || status === 'needs_review'
          ? 'badge-warning'
          : 'badge-neutral';
  return <span className={`status-badge ${badgeClass}`}>{status}</span>;
}

function RiskBadge({ level }) {
  const cls = level === 'high' ? 'badge-danger' : level === 'medium' ? 'badge-warning' : 'badge-success';
  return <span className={`status-badge ${cls}`}>{level} risk</span>;
}

function FreshnessBadge({ lastRunAt, lastValidationAt }) {
  const now = Date.now();
  const runAge = lastRunAt ? now - new Date(lastRunAt).getTime() : Infinity;
  const valAge = lastValidationAt ? now - new Date(lastValidationAt).getTime() : Infinity;
  const staleThresholdMs = 15 * 60 * 1000;

  if (runAge < staleThresholdMs && valAge < staleThresholdMs) {
    return <span className="freshness-badge freshness-fresh">Live</span>;
  }
  if (runAge < 60 * 60 * 1000) {
    return <span className="freshness-badge freshness-warn">Stale ({Math.round(runAge / 60000)}m ago)</span>;
  }
  return <span className="freshness-badge freshness-stale">Stale ({lastRunAt ? formatTimestamp(lastRunAt) : 'never'})</span>;
}

function SpecSourceBadge({ source }) {
  if (source === 'bq') {
    return <span className="spec-source-badge spec-source-managed">Managed Spec</span>;
  }
  return <span className="spec-source-badge spec-source-fallback">Fallback Spec</span>;
}

function ConfirmButton({ label, confirmLabel, onConfirm, disabled, variant }) {
  const [confirming, setConfirming] = useState(false);
  const btnClass = variant === 'danger' ? 'btn-danger' : '';
  if (confirming) {
    return (
      <span className="confirm-group">
        <button className={btnClass} onClick={() => { onConfirm(); setConfirming(false); }} disabled={disabled}>
          {confirmLabel || 'Confirm'}
        </button>
        <button className="btn-ghost" onClick={() => setConfirming(false)}>Cancel</button>
      </span>
    );
  }
  return (
    <button className={btnClass} onClick={() => setConfirming(true)} disabled={disabled}>
      {label}
    </button>
  );
}

function OpsPanel({
  summary,
  versions,
  proposals,
  lastRun,
  actionLoading,
  source,
  apiBase,
  onRun,
  onDecision,
  onApply,
  onRollback,
}) {
  const [filterStatus, setFilterStatus] = useState('all');
  const pendingReview = summary?.pending_review_count ?? 0;
  const proposalBacklog = summary?.proposal_backlog ?? 0;

  const filteredProposals = useMemo(() => {
    if (filterStatus === 'all') return proposals;
    return proposals.filter((p) => p.status === filterStatus);
  }, [proposals, filterStatus]);

  return (
    <section className="ops-layout">
      {/* Freshness + Source indicators */}
      <div className="ops-badges-row">
        <FreshnessBadge
          lastRunAt={summary?.last_run_at}
          lastValidationAt={summary?.last_successful_live_validation_at}
        />
        <SpecSourceBadge source={source || 'bq'} />
        {pendingReview > 0 ? (
          <span className="freshness-badge freshness-warn">{pendingReview} pending review</span>
        ) : null}
        {proposalBacklog > 0 ? (
          <span className="freshness-badge freshness-neutral">{proposalBacklog} in backlog</span>
        ) : null}
      </div>

      <section className="tile ops-summary">
        <header className="tile-header">
          <h3>Autonomy Summary</h3>
          <button onClick={onRun} disabled={actionLoading}>
            {actionLoading ? 'Running...' : 'Run Cycle'}
          </button>
        </header>
        <div className="score-grid">
          <div className="score-item">
            <div className="score-label">Active Version</div>
            <div className="score-value score-mono">{(summary?.latest_active_version_id || summary?.active_version_id || '-').slice(0, 12)}</div>
          </div>
          <div className="score-item">
            <div className="score-label">Previous Version</div>
            <div className="score-value score-mono">{(summary?.previous_version_id || '-').slice(0, 12)}</div>
          </div>
          <div className="score-item">
            <div className="score-label">Proposal Backlog</div>
            <div className={`score-value ${proposalBacklog > 5 ? 'score-warn' : ''}`}>{formatCell(proposalBacklog)}</div>
          </div>
          <div className="score-item">
            <div className="score-label">Decided Pending Apply</div>
            <div className="score-value">{formatCell(summary?.decided_pending_apply ?? 0)}</div>
          </div>
          <div className="score-item">
            <div className="score-label">Applied</div>
            <div className="score-value">{formatCell(summary?.applied_count ?? 0)}</div>
          </div>
          <div className="score-item">
            <div className={`score-label ${(summary?.failed_count ?? 0) > 0 ? 'score-danger-label' : ''}`}>Failed</div>
            <div className={`score-value ${(summary?.failed_count ?? 0) > 0 ? 'score-danger' : ''}`}>{formatCell(summary?.failed_count ?? 0)}</div>
          </div>
          <div className="score-item">
            <div className="score-label">Rejected</div>
            <div className="score-value">{formatCell(summary?.rejected_count ?? 0)}</div>
          </div>
          <div className="score-item">
            <div className={`score-label ${(summary?.rolled_back_count ?? 0) > 0 ? 'score-danger-label' : ''}`}>Rolled Back</div>
            <div className={`score-value ${(summary?.rolled_back_count ?? 0) > 0 ? 'score-danger' : ''}`}>{formatCell(summary?.rolled_back_count ?? 0)}</div>
          </div>
          <div className="score-item">
            <div className="score-label">Last Run</div>
            <div className="score-value">{formatTimestamp(summary?.last_run_at)}</div>
          </div>
          <div className="score-item">
            <div className="score-label">Last Apply</div>
            <div className="score-value">{formatTimestamp(summary?.last_applied_at)}</div>
          </div>
          <div className="score-item">
            <div className="score-label">Last Rollback</div>
            <div className="score-value">{formatTimestamp(summary?.last_rollback_at)}</div>
          </div>
          <div className="score-item">
            <div className="score-label">Last Validation</div>
            <div className="score-value">{formatTimestamp(summary?.last_successful_live_validation_at)}</div>
          </div>
        </div>
        {lastRun ? (
          <div className={`ops-run-result ${lastRun.status?.includes('rollback') ? 'ops-run-rollback' : lastRun.failed > 0 ? 'ops-run-error' : 'ops-run-ok'}`}>
            <div className="ops-run-header">
              <strong>Latest Cycle Result</strong>
              <StatusBadge status={lastRun.status} />
            </div>
            <span>
              Generated {lastRun.generated} | Decided {lastRun.decided} | Applied {lastRun.applied} | Failed {lastRun.failed}
            </span>
            <span className="ops-run-meta">
              {lastRun.mode} | {lastRun.policy_version} | {lastRun.validation_issues?.length || 0} validation issues
            </span>
            {lastRun.errors?.length > 0 ? (
              <div className="ops-run-errors">
                {lastRun.errors.slice(0, 3).map((err, i) => (
                  <div key={`err-${i}`} className="ops-run-error-line">{err}</div>
                ))}
              </div>
            ) : null}
            {lastRun.messages?.length > 0 ? (
              <div className="ops-run-messages">
                {lastRun.messages.slice(0, 5).map((msg, i) => (
                  <div key={`msg-${i}`} className="ops-run-msg-line">{msg}</div>
                ))}
              </div>
            ) : null}
          </div>
        ) : null}
      </section>

      <section className="tile ops-summary">
        <header className="tile-header">
          <h3>Environment</h3>
        </header>
        <div className="score-grid">
          <div className="score-item">
            <div className="score-label">API Base</div>
            <div className="score-value score-mono">{apiBase}</div>
          </div>
          <div className="score-item">
            <div className="score-label">Spec Source</div>
            <div className="score-value"><SpecSourceBadge source={source || 'bq'} /></div>
          </div>
          <div className="score-item">
            <div className="score-label">Pending Review</div>
            <div className={`score-value ${pendingReview > 0 ? 'score-warn' : ''}`}>{formatCell(pendingReview)}</div>
          </div>
        </div>
      </section>

      <section className="tile">
        <header className="tile-header">
          <div>
            <h3>Governed Proposals</h3>
            <p className="tile-description">{filteredProposals.length} of {proposals.length} proposals shown</p>
          </div>
          <div className="ops-filter-row">
            {['all', 'proposed', 'decided', 'applied', 'rejected', 'rolled_back', 'failed'].map((s) => (
              <button
                key={s}
                className={`filter-chip ${filterStatus === s ? 'filter-chip-active' : ''}`}
                onClick={() => setFilterStatus(s)}
              >
                {s === 'all' ? 'All' : s.replace('_', ' ')}
              </button>
            ))}
          </div>
        </header>
        <div className="proposal-list ops-proposal-list">
          {filteredProposals.length === 0 ? <div className="tile-loading">No proposals match this filter.</div> : null}
          {filteredProposals.map((proposal) => (
            <article className={`proposal proposal-${proposal.payload?.risk_level || proposal.risk_level}`} key={proposal.proposal_id}>
              <div className="proposal-header-row">
                <h4>{proposal.payload?.title || proposal.proposal_type}</h4>
                <div className="proposal-badges">
                  <StatusBadge status={proposal.status} />
                  <RiskBadge level={proposal.payload?.risk_level || proposal.risk_level} />
                </div>
              </div>
              <p>{proposal.payload?.details || proposal.rationale}</p>
              <div className="proposal-meta">
                <span>{proposal.proposal_type}</span>
                <span>{proposal.policy_version}</span>
                <span>{formatTimestamp(proposal.created_at)}</span>
                <span className="proposal-id-mono">{proposal.proposal_id.slice(0, 8)}</span>
              </div>
              {proposal.decision ? (
                <div className="proposal-decision-box">
                  <strong>Decision:</strong> {proposal.decision.decision} by {proposal.decision.decided_by}
                  {proposal.decision.decision_reason ? (
                    <p className="proposal-note">{proposal.decision.decision_reason}</p>
                  ) : null}
                </div>
              ) : null}
              <div className="proposal-actions">
                {proposal.status === 'proposed' ? (
                  <>
                    <ConfirmButton
                      label="Approve"
                      confirmLabel="Confirm Approve"
                      onConfirm={() => onDecision(proposal.proposal_id, 'approve_manual')}
                      disabled={actionLoading}
                    />
                    <ConfirmButton
                      label="Reject"
                      confirmLabel="Confirm Reject"
                      onConfirm={() => onDecision(proposal.proposal_id, 'reject')}
                      disabled={actionLoading}
                      variant="danger"
                    />
                  </>
                ) : null}
                {proposal.status === 'decided' ? (
                  <ConfirmButton
                    label="Apply"
                    confirmLabel="Confirm Apply"
                    onConfirm={() => onApply(proposal.proposal_id)}
                    disabled={actionLoading}
                  />
                ) : null}
                {proposal.status === 'applied' ? (
                  <ConfirmButton
                    label="Rollback"
                    confirmLabel="Confirm Rollback"
                    onConfirm={() => onRollback(proposal.proposal_id)}
                    disabled={actionLoading}
                    variant="danger"
                  />
                ) : null}
              </div>
            </article>
          ))}
        </div>
      </section>

      <section className="tile">
        <header className="tile-header">
          <h3>Spec Version History</h3>
        </header>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Version</th>
                <th>Status</th>
                <th>Created</th>
                <th>By</th>
                <th>Source Proposal</th>
                <th>Previous</th>
                <th>Notes</th>
              </tr>
            </thead>
            <tbody>
              {versions.map((v, idx) => (
                <tr key={v.version_id || idx} className={v.status === 'active' ? 'row-active' : ''}>
                  <td className="cell-mono">{(v.version_id || '-').slice(0, 12)}</td>
                  <td><StatusBadge status={v.status} /></td>
                  <td>{formatTimestamp(v.created_at)}</td>
                  <td>{v.created_by || '-'}</td>
                  <td className="cell-mono">{v.source_proposal_id ? v.source_proposal_id.slice(0, 8) : '-'}</td>
                  <td className="cell-mono">{v.previous_version_id ? v.previous_version_id.slice(0, 12) : '-'}</td>
                  <td className="cell-notes">{v.notes || '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </section>
  );
}

function Tile({ tile, rows, loading, error, onPanelView }) {
  useEffect(() => {
    if (!loading && rows.length > 0) onPanelView(tile.tile_id);
  }, [loading, onPanelView, rows.length, tile.tile_id]);

  return (
    <section className="tile">
      <header className="tile-header">
        <h3>{tile.title}</h3>
        <span className="viz-pill">{tile.viz_type}</span>
      </header>
      <p className="tile-description">{tile.description}</p>
      {loading ? <div className="tile-loading">Loading...</div> : null}
      {!loading && error ? <div className="tile-error">{error}</div> : null}
      {!loading && rows.length === 0 ? <div className="tile-loading">No rows returned.</div> : null}
      {!loading && rows.length > 0 && tile.viz_type === 'scorecard' ? <ScorecardTile tile={tile} rows={rows} /> : null}
      {!loading && rows.length > 0 && tile.viz_type === 'table' ? <TableTile tile={tile} rows={rows} /> : null}
      {!loading && rows.length > 0 && tile.viz_type === 'timeseries' ? <TimeSeriesTile tile={tile} rows={rows} /> : null}
    </section>
  );
}

export default function App() {
  const [activeRole, setActiveRole] = useState(() => roleFromPath(window.location.pathname));
  const [spec, setSpec] = useState(null);
  const [source, setSource] = useState('default');
  const [rowsByTile, setRowsByTile] = useState({});
  const [loadingByTile, setLoadingByTile] = useState({});
  const [tileErrors, setTileErrors] = useState({});
  const [error, setError] = useState('');
  const [proposals, setProposals] = useState([]);
  const [governanceProposals, setGovernanceProposals] = useState([]);
  const [governanceSummary, setGovernanceSummary] = useState(null);
  const [specVersions, setSpecVersions] = useState([]);
  const [lastAutonomyRun, setLastAutonomyRun] = useState(null);
  const [opsActionLoading, setOpsActionLoading] = useState(false);
  const [signalFeed, setSignalFeed] = useState([]);
  const [signalFeedLoading, setSignalFeedLoading] = useState(false);
  const [signalFilters, setSignalFilters] = useState({
    signal_type: '',
    severity: '',
    family: '',
    window: '',
    sort_by: 'score',
    sort_dir: 'desc',
    limit: 40,
  });
  const sessionIdRef = useRef(`session-${Date.now()}`);

  const refreshSeconds = spec?.refresh_seconds || 60;

  const logEvent = async (action, panelId = null, filters = {}) => {
    try {
      await postUsageEvent({
        user_id: 'local-user',
        dashboard_id: DASHBOARD_ID,
        action,
        panel_id: panelId,
        filters: { role: activeRole, ...filters },
        session_id: sessionIdRef.current,
      });
    } catch {
      // Event logging should not break UX.
    }
  };

  const loadTile = async (tile) => {
    setLoadingByTile((prev) => ({ ...prev, [tile.tile_id]: true }));
    try {
      const data = await fetchTile(tile.tile_id, DASHBOARD_ID, tile.default_limit);
      setRowsByTile((prev) => ({ ...prev, [tile.tile_id]: data.rows || [] }));
      setTileErrors((prev) => ({ ...prev, [tile.tile_id]: '' }));
    } catch (err) {
      setTileErrors((prev) => ({ ...prev, [tile.tile_id]: err.message }));
    } finally {
      setLoadingByTile((prev) => ({ ...prev, [tile.tile_id]: false }));
    }
  };

  const loadSignals = async (filters) => {
    if (activeRole !== 'analyst') {
      setSignalFeed([]);
      return;
    }
    setSignalFeedLoading(true);
    try {
      const data = await fetchSignalFeed(filters);
      setSignalFeed(data.rows || []);
    } catch (err) {
      setError(err.message);
    } finally {
      setSignalFeedLoading(false);
    }
  };

  const loadGovernance = async () => {
    try {
      const [summaryRes, proposalRes, versionsRes] = await Promise.all([
        fetchGovernanceSummary(DASHBOARD_ID),
        fetchGovernanceProposals(DASHBOARD_ID, 20),
        fetchSpecVersions(DASHBOARD_ID, 20),
      ]);
      setGovernanceSummary(summaryRes);
      setGovernanceProposals(proposalRes.proposals || []);
      setSpecVersions(versionsRes.versions || []);
    } catch (err) {
      setError(err.message);
    }
  };

  const loadAll = async (role) => {
    setError('');
    try {
      if (role === 'ops') {
        setSpec(null);
        setRowsByTile({});
        setProposals([]);
        setSignalFeed([]);
        await loadGovernance();
        return;
      }
      const specRes = await fetchSpec(DASHBOARD_ID, role);
      setSpec(specRes.spec);
      setSource(specRes.source);

      await Promise.all((specRes.spec.tiles || []).map((tile) => loadTile(tile)));

      const proposalRes = await fetchProposals(DASHBOARD_ID);
      setProposals(proposalRes.proposals || []);
    } catch (err) {
      setError(err.message);
    }
  };

  useEffect(() => {
    if (window.location.pathname === '/' || window.location.pathname === '') {
      window.history.replaceState({}, '', `/${activeRole}`);
    }

    const onPopState = () => {
      setActiveRole(roleFromPath(window.location.pathname));
    };
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, []);

  useEffect(() => {
    loadAll(activeRole);
    logEvent('page_view');
  }, [activeRole]);

  useEffect(() => {
    if (activeRole !== 'analyst') {
      setSignalFeed([]);
      return;
    }
    loadSignals(signalFilters);
  }, [activeRole, signalFilters]);

  useEffect(() => {
    if (!spec || activeRole === 'ops') return undefined;
    const timer = setInterval(() => {
      loadAll(activeRole);
      if (activeRole === 'analyst') {
        loadSignals(signalFilters);
      }
      logEvent('auto_refresh', null, { refresh_seconds: refreshSeconds });
    }, refreshSeconds * 1000);
    return () => clearInterval(timer);
  }, [spec, refreshSeconds, activeRole, signalFilters]);

  useEffect(() => {
    if (activeRole !== 'ops') return undefined;
    const timer = setInterval(() => {
      loadGovernance();
      logEvent('ops_auto_refresh');
    }, 30000);
    return () => clearInterval(timer);
  }, [activeRole]);

  const sortedProposals = useMemo(
    () => [...proposals].sort((a, b) => (a.priority > b.priority ? -1 : 1)),
    [proposals]
  );
  const familyOptions = useMemo(
    () =>
      Array.from(
        new Set(signalFeed.map((row) => row.market_family).filter((value) => typeof value === 'string' && value))
      ).sort(),
    [signalFeed]
  );

  const onRoleChange = (nextRole) => {
    const normalized = normalizeRole(nextRole);
    if (normalized === activeRole) return;
    window.history.pushState({}, '', `/${normalized}`);
    setActiveRole(normalized);
    logEvent('role_change', null, { to_role: normalized });
  };

  const onSignalFilterChange = (key, value) => {
    setSignalFilters((prev) => ({ ...prev, [key]: value }));
    logEvent('signal_filter_change', 'signal_feed', { [key]: value });
  };

  const onRunAutonomy = async () => {
    setOpsActionLoading(true);
    try {
      const result = await runAutonomyCycle(DASHBOARD_ID);
      setLastAutonomyRun(result);
      await loadGovernance();
      logEvent('ops_run_cycle');
    } catch (err) {
      setError(err.message);
    } finally {
      setOpsActionLoading(false);
    }
  };

  const onGovernanceDecision = async (proposalId, decision) => {
    setOpsActionLoading(true);
    try {
      await decideGovernanceProposal(proposalId, {
        dashboard_id: DASHBOARD_ID,
        decision,
        decided_by: 'human_operator',
        decision_reason: decision === 'reject' ? 'Manual rejection from operator panel.' : 'Manual approval from operator panel.',
      });
      await loadGovernance();
      logEvent('ops_decision', proposalId, { decision });
    } catch (err) {
      setError(err.message);
    } finally {
      setOpsActionLoading(false);
    }
  };

  const onApplyProposal = async (proposalId) => {
    setOpsActionLoading(true);
    try {
      await applyProposal({
        dashboard_id: DASHBOARD_ID,
        proposal_id: proposalId,
        activated_by: 'human_operator',
      });
      await loadGovernance();
      logEvent('ops_apply', proposalId);
    } catch (err) {
      setError(err.message);
    } finally {
      setOpsActionLoading(false);
    }
  };

  const onRollbackProposal = async (proposalId) => {
    setOpsActionLoading(true);
    try {
      await rollbackSpec({
        dashboard_id: DASHBOARD_ID,
        proposal_id: proposalId,
        rollback_reason: 'Manual rollback from operator panel.',
        rolled_back_by: 'human_operator',
      });
      await loadGovernance();
      logEvent('ops_rollback', proposalId);
    } catch (err) {
      setError(err.message);
    } finally {
      setOpsActionLoading(false);
    }
  };

  return (
    <main className="page">
      <header className="topbar">
        <div>
          <h1>{activeRole === 'ops' ? 'Kalshi Autonomy Operator Console' : spec?.title || 'Kalshi Autonomous Dashboard'}</h1>
          <p>
            {activeRole === 'ops'
              ? 'Governed proposal queue, active spec versions, and autonomy controls.'
              : spec?.description || 'Spec-driven dashboard with autonomous proposal lane.'}
          </p>
          <p className="role-subtitle">Role view: {ROLE_CONFIG[activeRole]?.label || activeRole}</p>
        </div>
        <div className="topbar-actions">
          {activeRole !== 'ops' ? (
            <SpecSourceBadge source={source} />
          ) : null}
          <button
            onClick={() => {
              loadAll(activeRole);
              logEvent('manual_refresh');
            }}
          >
            Refresh
          </button>
        </div>
      </header>

      <nav className="role-tabs">
        {Object.entries(ROLE_CONFIG).map(([roleKey, roleMeta]) => (
          <button
            key={roleKey}
            className={`role-tab ${activeRole === roleKey ? 'role-tab-active' : ''}`}
            onClick={() => onRoleChange(roleKey)}
          >
            {roleMeta.label}
          </button>
        ))}
      </nav>

      {error ? <div className="error">{error}</div> : null}

      {activeRole !== 'consumer' && activeRole !== 'ops' ? (
        <section className="proposal-strip">
          <h2>Agent Proposals</h2>
          <div className="proposal-list">
            {sortedProposals.map((proposal) => (
              <article className={`proposal proposal-${proposal.priority}`} key={proposal.proposal_id}>
                <h4>{proposal.title}</h4>
                <p>{proposal.details}</p>
                <small>{proposal.proposal_type}</small>
              </article>
            ))}
          </div>
        </section>
      ) : null}

      {activeRole === 'analyst' ? (
        <>
          <AnalystHelp />
          <SignalFeedSection
            rows={signalFeed}
            loading={signalFeedLoading}
            filters={signalFilters}
            familyOptions={familyOptions}
            onFilterChange={onSignalFilterChange}
            onRefresh={() => {
              loadSignals(signalFilters);
              logEvent('manual_refresh', 'signal_feed');
            }}
          />
        </>
      ) : null}

      {activeRole === 'consumer' ? <ConsumerHelp /> : null}

      {activeRole === 'ops' ? (
        <OpsPanel
          summary={governanceSummary}
          versions={specVersions}
          proposals={governanceProposals}
          lastRun={lastAutonomyRun}
          actionLoading={opsActionLoading}
          source={source}
          apiBase={API_BASE}
          onRun={onRunAutonomy}
          onDecision={onGovernanceDecision}
          onApply={onApplyProposal}
          onRollback={onRollbackProposal}
        />
      ) : null}

      {activeRole !== 'ops' ? (
        <section className="tile-grid">
          {(spec?.tiles || []).map((tile) => (
            <Tile
              key={tile.tile_id}
              tile={tile}
              rows={rowsByTile[tile.tile_id] || []}
              loading={!!loadingByTile[tile.tile_id]}
              error={tileErrors[tile.tile_id]}
              onPanelView={(panelId) => logEvent('panel_view', panelId)}
            />
          ))}
        </section>
      ) : null}
    </main>
  );
}
