import {test, expect} from '@playwright/test';

// Synthetic response fixtures; no live model, launch date, or access claims.
const base = {record_id: '', version: '1', kind: 'model', data_handling: 'Synthetic metadata',
  capabilities: ['input:text', 'output:text'], policy_reason: '', origin: 'Synthetic discovery',
  refreshed_at: null, fixture: false, approved: true, external: false, discovery_only: true,
  status: 'blocked', granted: false, usable: false, requestable: false, execution_ready: false,
  recency: 'recent', lifecycle: 'ACTIVE', description: 'Synthetic model description'};
const text = {...base, id: 'discovery:synthetic-text', model_id: 'synthetic.text',
  name: 'Synthetic text model', provider: 'Provider A', category: 'text'};
const image = {...base, id: 'discovery:synthetic-image', model_id: 'synthetic.image',
  name: 'Synthetic image model', provider: 'Provider B', category: 'multimodal',
  capabilities: ['input:text', 'input:image', 'output:text']};
const old = {...text, id: 'discovery:old', model_id: 'synthetic.old',
  name: 'Older discovery', recency: 'out_of_window'};
const published = {...text, id: 'published-model', model_id: 'synthetic.published',
  name: 'Published model', catalog: 'journey', recency: undefined, discovery_only: false,
  granted: true, usable: true, execution_ready: true, status: 'available'};

async function open(page, items) {
  await page.route('**/api/catalog', r => r.fulfill({json: {items, count: items.length,
    mode: 'live', agent_listing_implemented: false}}));
  await page.goto('/');
  await page.getByRole('button', {name: 'Enter as Sam Taylor'}).click();
  await expect(page.getByRole('heading', {name: 'My agents', exact: true})).toBeVisible();
  const toggle = page.getByRole('button', {name: 'Open side navigation', exact: true});
  if (await toggle.isVisible()) await toggle.click();
  await page.getByRole('link', {name: 'AI Catalog', exact: true}).click();
}

test('model providers and capability filters use API fields; discovery does not grant execution', async ({page}) => {
  await open(page, [text, image, old]);
  await expect(page.getByRole('table', {name: 'Provider A'}).getByRole('button', {name: text.name})).toBeVisible();
  await expect(page.getByRole('table', {name: 'Provider B'}).getByRole('button', {name: image.name})).toBeVisible();
  await expect(page.getByRole('button', {name: old.name})).toHaveCount(0);
  await expect(page.getByText('Access granted: 0 of 2 models', {exact: true})).toBeVisible();
  await expect(page.getByText('Not configured', {exact: true})).toHaveCount(2);
  await expect(page.getByRole('button', {name: 'All discovered', exact: true})).toHaveCount(0);
  await page.getByLabel('Filter models by capability').selectOption('input:image');
  await expect(page.getByRole('button', {name: text.name})).toHaveCount(0);
  await page.getByRole('button', {name: image.name}).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByText('Category: Multimodal', {exact: true})).toBeVisible();
  await expect(dialog.getByRole('button', {name: 'Add to draft'})).toHaveCount(0);
  await dialog.getByRole('button', {name: 'Technical identifiers'}).click();
  await expect(dialog.getByText('Model / profile ID: synthetic.image')).toBeVisible();
});

test('published Journey model stays visible and callable without discovery recency metadata', async ({page}) => {
  await open(page, [text, image, published, old]);
  await expect(page.getByText('Access granted: 1 of 3 models', {exact: true})).toBeVisible();
  const row = page.getByRole('row').filter({has: page.getByRole('button', {name: published.name})});
  await expect(row.getByText('Access granted', {exact: true})).toBeVisible();
  await expect(row.getByText('Ready', {exact: true})).toBeVisible();
  await page.getByRole('button', {name: published.name}).click();
  await expect(page.getByRole('dialog').getByRole('button', {name: 'Add to draft'})).toBeEnabled();
});
