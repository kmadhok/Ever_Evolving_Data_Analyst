import { useEffect, useMemo, useRef, useState } from 'react';
import { fetchProposals, fetchSpec, fetchTile, postUsageEvent } from './api';

const DASHBOARD_ID = 'kalshi_autonomous_v1';
const ROLE_CONFIG = {
  de: { label: 'Data Engineer' },
  analyst: { label: 'Analyst' },
  ds: { label: 'Data Scientist' },
};

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

function Tile({ tile, rows, loading, onPanelView }) {
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
  const [error, setError] = useState('');
  const [proposals, setProposals] = useState([]);
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
    } catch (err) {
      setError(err.message);
    } finally {
      setLoadingByTile((prev) => ({ ...prev, [tile.tile_id]: false }));
    }
  };

  const loadAll = async (role) => {
    setError('');
    try {
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
    if (!spec) return undefined;
    const timer = setInterval(() => {
      loadAll(activeRole);
      logEvent('auto_refresh', null, { refresh_seconds: refreshSeconds });
    }, refreshSeconds * 1000);
    return () => clearInterval(timer);
  }, [spec, refreshSeconds, activeRole]);

  const sortedProposals = useMemo(
    () => [...proposals].sort((a, b) => (a.priority > b.priority ? -1 : 1)),
    [proposals]
  );

  const onRoleChange = (nextRole) => {
    const normalized = normalizeRole(nextRole);
    if (normalized === activeRole) return;
    window.history.pushState({}, '', `/${normalized}`);
    setActiveRole(normalized);
    logEvent('role_change', null, { to_role: normalized });
  };

  return (
    <main className="page">
      <header className="topbar">
        <div>
          <h1>{spec?.title || 'Kalshi Autonomous Dashboard'}</h1>
          <p>{spec?.description || 'Spec-driven dashboard with autonomous proposal lane.'}</p>
          <p className="role-subtitle">Role view: {ROLE_CONFIG[activeRole]?.label || activeRole}</p>
        </div>
        <div className="topbar-actions">
          <button
            onClick={() => {
              loadAll(activeRole);
              logEvent('manual_refresh');
            }}
          >
            Refresh
          </button>
          <span className="meta">spec source: {source}</span>
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

      <section className="tile-grid">
        {(spec?.tiles || []).map((tile) => (
          <Tile
            key={tile.tile_id}
            tile={tile}
            rows={rowsByTile[tile.tile_id] || []}
            loading={!!loadingByTile[tile.tile_id]}
            onPanelView={(panelId) => logEvent('panel_view', panelId)}
          />
        ))}
      </section>
    </main>
  );
}
