import {test, expect} from '@playwright/test';

test('existing managed servers remain visible without a vendor-specific authoring route', async ({page}) => {
  const calls: string[] = [], errors: string[] = [];
  const server = {id: 'existing-1', name: 'Existing warehouse', phase: 'READY',
    endpoint: 'https://warehouse.example.com/mcp', workspaces: ['research'], gateway_target_id: 'existing-target'};
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/studio-config.json', r => r.fulfill({json: {hosted: true, journey_enabled: true}}));
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    calls.push(path);
    if (path === '/api/me') return route.fulfill({json: {persona: {id: 'admin', role: 'admin', workspace: 'platform'}, csrf: 'test-csrf'}});
    if (path === '/api/admin/platform/overview') return route.fulfill({json: {agents: [], hours: 24, pending_tool_requests: 0, pending_access_requests: 0}});
    if (path === '/api/admin/catalog') return route.fulfill({json: {foundations: [], components: [], grants: [], personas: [], history: [], policy: {}}});
    if (path === '/api/agents') return route.fulfill({json: []});
    if (path === '/api/admin/mcp/onboarding-options') return route.fulfill({json: {enabled: true, credential_setup: true, workspaces: ['research'], connections: []}});
    if (path === '/api/admin/mcp/onboarding') return route.fulfill({json: {items: []}});
    if (path === '/api/admin/mcp/servers') return route.fulfill({json: {items: [server]}});
    if (path === '/api/admin/mcp/servers/existing-1') return route.fulfill({json: server});
    return route.fulfill({status: 404, json: {detail: 'Unexpected route'}});
  });
  await page.goto('/');
  await page.getByRole('link', {name: 'MCP servers', exact: true}).click();
  await page.getByRole('row').filter({hasText: 'Existing warehouse'}).getByRole('radio').check();
  await expect(page.getByText('existing-target', {exact: true})).toBeVisible();
  await expect(page.getByRole('tab')).toHaveCount(0);
  await page.getByRole('button', {name: 'Create MCP connection', exact: true}).click();
  await expect(page.getByLabel('API key or PAT', {exact: true})).toBeVisible();
  await expect(page.getByText('Snowflake profile', {exact: true})).toHaveCount(0);
  expect(calls).not.toContain('/api/admin/mcp/options');
  expect(errors).toEqual([]);
});
