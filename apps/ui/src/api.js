export const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

async function parseJson(response) {
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `HTTP ${response.status}`);
  }
  return response.json();
}

export async function fetchSpec(dashboardId, role) {
  const params = new URLSearchParams({ dashboard_id: dashboardId });
  if (role) params.set('role', role);
  const res = await fetch(`${API_BASE}/v1/dashboard/spec?${params.toString()}`);
  return parseJson(res);
}

export async function fetchTile(tileId, dashboardId, limit) {
  const params = new URLSearchParams({ dashboard_id: dashboardId });
  if (limit) params.set('limit', String(limit));
  const res = await fetch(`${API_BASE}/v1/dashboard/tile/${encodeURIComponent(tileId)}?${params.toString()}`);
  return parseJson(res);
}

export async function postUsageEvent(payload) {
  const res = await fetch(`${API_BASE}/v1/usage/events`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  return parseJson(res);
}

export async function fetchProposals(dashboardId) {
  const res = await fetch(
    `${API_BASE}/v1/agent/proposals?dashboard_id=${encodeURIComponent(dashboardId)}&persist=true`
  );
  return parseJson(res);
}

export async function fetchSignalFeed(filters = {}) {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => {
    if (value !== null && value !== undefined && value !== '') {
      params.set(key, String(value));
    }
  });
  const res = await fetch(`${API_BASE}/v1/signals/feed?${params.toString()}`);
  return parseJson(res);
}
