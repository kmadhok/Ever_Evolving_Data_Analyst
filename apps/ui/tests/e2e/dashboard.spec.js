import { expect, test } from '@playwright/test';

const specPayload = {
  source: 'bq',
  spec: {
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
  },
};

test.beforeEach(async ({ page }) => {
  await page.route('**/v1/**', async (route) => {
    const url = new URL(route.request().url());
    const { pathname } = url;
    if (pathname.endsWith('/dashboard/spec')) {
      await route.fulfill({ json: specPayload });
      return;
    }
    if (pathname.includes('/dashboard/tile/')) {
      await route.fulfill({ json: { rows: [{ minutes_since_latest_trade: 4 }] } });
      return;
    }
    if (pathname.endsWith('/agent/proposals')) {
      await route.fulfill({ json: { proposals: [] } });
      return;
    }
    if (pathname.endsWith('/governance/summary')) {
      await route.fulfill({
        json: {
          active_version_id: 'v-current',
          latest_active_version_id: 'v-current',
          previous_version_id: 'v-previous',
          proposal_backlog: 1,
          decided_pending_apply: 0,
          applied_count: 1,
          failed_count: 0,
          rejected_count: 0,
          rolled_back_count: 0,
          last_run_at: '2026-03-12T10:00:00Z',
          last_successful_live_validation_at: '2026-03-12T09:00:00Z',
        },
      });
      return;
    }
    if (pathname.endsWith('/governance/proposals')) {
      await route.fulfill({ json: { proposals: [] } });
      return;
    }
    if (pathname.endsWith('/governance/spec-versions')) {
      await route.fulfill({ json: { versions: [] } });
      return;
    }
    if (pathname.endsWith('/signals/feed')) {
      await route.fulfill({ json: { rows: [] } });
      return;
    }
    await route.fulfill({ json: { accepted: true } });
  });
});

test('loads the primary role routes', async ({ page }) => {
  for (const route of ['/de', '/analyst', '/ds', '/consumer', '/ops']) {
    await page.goto(route);
    await expect(page.locator('main')).toBeVisible();
  }
  await expect(page.getByText('Kalshi Autonomy Operator Console')).toBeVisible();
});
