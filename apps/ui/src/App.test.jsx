import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import App from './App';
import * as api from './api';

vi.mock('./api', async () => {
  const actual = await vi.importActual('./api');
  return {
    ...actual,
    fetchSpec: vi.fn(),
    fetchTile: vi.fn(),
    postUsageEvent: vi.fn(),
    fetchProposals: vi.fn(),
    fetchSignalFeed: vi.fn(),
    fetchGovernanceSummary: vi.fn(),
    fetchGovernanceProposals: vi.fn(),
    fetchSpecVersions: vi.fn(),
    runAutonomyCycle: vi.fn(),
    decideGovernanceProposal: vi.fn(),
    applyProposal: vi.fn(),
    rollbackSpec: vi.fn(),
  };
});

const sampleSpec = {
  dashboard_id: 'kalshi_autonomous_v1',
  version: 1,
  title: 'Kalshi Autonomous Dashboard',
  description: 'Spec-driven dashboard',
  refresh_seconds: 60,
  tiles: [
    {
      tile_id: 'pipeline_heartbeat',
      title: 'Pipeline Heartbeat',
      description: 'Live health tile',
      view_name: 'vw_pipeline_heartbeat',
      viz_type: 'table',
      columns: ['minutes_since_latest_trade'],
      default_limit: 10,
      roles: ['de', 'analyst', 'ds'],
    },
  ],
};

beforeEach(() => {
  window.history.pushState({}, '', '/de');
  api.fetchSpec.mockResolvedValue({ source: 'bq', spec: sampleSpec });
  api.fetchTile.mockResolvedValue({ rows: [{ minutes_since_latest_trade: 4 }] });
  api.postUsageEvent.mockResolvedValue({ accepted: true });
  api.fetchProposals.mockResolvedValue({ proposals: [] });
  api.fetchSignalFeed.mockResolvedValue({ rows: [] });
  api.fetchGovernanceSummary.mockResolvedValue({
    active_version_id: 'v-current',
    latest_active_version_id: 'v-current',
    previous_version_id: 'v-previous',
    proposal_backlog: 1,
    decided_pending_apply: 0,
    applied_count: 2,
    failed_count: 0,
    rejected_count: 0,
    rolled_back_count: 0,
    last_run_at: '2026-03-12T10:00:00Z',
    last_successful_live_validation_at: '2026-03-12T09:00:00Z',
  });
  api.fetchGovernanceProposals.mockResolvedValue({ proposals: [] });
  api.fetchSpecVersions.mockResolvedValue({ versions: [] });
  api.runAutonomyCycle.mockResolvedValue({
    generated: 1,
    decided: 1,
    applied: 1,
    failed: 0,
    mode: 'manual_cli',
    policy_version: '2026-03-beta-1',
    validation_issues: [],
  });
  api.decideGovernanceProposal.mockResolvedValue({ accepted: true });
  api.applyProposal.mockResolvedValue({ accepted: true });
  api.rollbackSpec.mockResolvedValue({ accepted: true });
});

describe('App', () => {
  it('loads the DE route and renders tiles', async () => {
    render(<App />);
    await screen.findByText('Kalshi Autonomous Dashboard');
    expect(await screen.findByText('Pipeline Heartbeat')).toBeInTheDocument();
    expect(await screen.findByText('4')).toBeInTheDocument();
  });

  it('renders the operator console with environment details', async () => {
    window.history.pushState({}, '', '/ops');
    render(<App />);
    expect(await screen.findByText('Kalshi Autonomy Operator Console')).toBeInTheDocument();
    expect(await screen.findByText('Environment')).toBeInTheDocument();
    expect(await screen.findByText('http://localhost:8000')).toBeInTheDocument();
    expect(await screen.findByText('v-current')).toBeInTheDocument();
  });

  it('shows a tile-level error and still renders the page shell', async () => {
    api.fetchTile.mockRejectedValueOnce(new Error('tile boom'));
    render(<App />);
    await screen.findByText('Kalshi Autonomous Dashboard');
    await waitFor(() => expect(screen.getByText('tile boom')).toBeInTheDocument());
  });

  it('reloads analyst signals when filters change', async () => {
    window.history.pushState({}, '', '/analyst');
    render(<App />);
    await screen.findByText('Signal Feed');
    const severitySelect = await screen.findByLabelText('Severity');
    await userEvent.selectOptions(severitySelect, 'high');
    await waitFor(() => {
      expect(api.fetchSignalFeed).toHaveBeenCalled();
    });
  });
});
