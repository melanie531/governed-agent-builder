import {test, expect} from '@playwright/test';

for (const template of ['Research', 'Knowledge Q&A']) {
  for (const evaluation of [false, true]) {
    test(`${template}: MCP connection, ${evaluation ? 'synthetic evaluation' : 'no dataset'}, deploy and invoke`, async ({page}) => {
      await page.goto('/');
      await page.request.post('/api/demo/session', {data: {persona_id: 'alex'}, headers: {origin: 'http://127.0.0.1:5189'}});
      await page.reload();
      await page.getByRole('button', {name: 'Create agent', exact: true}).click();
      await expect(page.getByRole('radio')).toHaveCount(2);
      await page.getByRole('radio', {name: `Business templates Select ${template}`, exact: true}).check();
      await page.getByRole('button', {name: 'Next', exact: true}).click();
      await expect(page.getByText('MCP servers', {exact: true})).toBeVisible();
      // Tools are nested permissions, collapsed until the user chooses to inspect them.
      await expect(page.getByRole('checkbox')).toHaveCount(0);
      await page.getByRole('button', {name: 'Tool permissions (1 allowed)', exact: true}).click();
      const tool = page.getByRole('checkbox', {name: template === 'Research' ? 'Web search' : 'Knowledge search'});
      await expect(tool).toBeChecked();
      await tool.uncheck();
      await page.getByRole('button', {name: 'Next', exact: true}).click();
      await expect(page.getByText('Choose at least one allowed tool under each selected MCP server, or remove that server.')).toBeVisible();
      await tool.check();
      await page.getByRole('button', {name: 'Next', exact: true}).click();
      if (evaluation) await page.getByRole('button', {name: 'Use synthetic sample'}).click();
      else await expect(page.getByText('No dataset provided. Your agent will deploy and evaluation will be skipped.')).toBeVisible();
      await page.getByRole('button', {name: 'Next', exact: true}).click();
      const createRequests: string[] = [];
      page.on('request', req => {if (req.method() === 'POST' && req.url().endsWith('/journey/agents')) createRequests.push(req.url());});
      await page.getByRole('button', {name: 'Deploy to AgentCore', exact: true}).click();
      await expect(page.getByText('Deployed', {exact: true})).toBeVisible();
      await expect(page.getByText(evaluation ? 'Evaluation passed' : 'Skipped — no dataset', {exact: true})).toBeVisible();
      expect(createRequests).toHaveLength(1);
      await page.reload();
      await expect(page.getByText('Deployed', {exact: true})).toBeVisible();
      await page.getByRole('textbox', {name: 'Your question'}).fill('What is Aurora’s support target? Search the documents.');
      await page.getByRole('button', {name: 'Run agent', exact: true}).click();
      await expect(page.getByLabel('Agent output')).toContainText('Aurora launches in October. Support responds in four hours.');
      if (template === 'Knowledge Q&A' && !evaluation) {
        await page.getByRole('textbox', {name: 'Your question'}).fill('Repeat the support target from your previous answer.');
        await page.getByRole('button', {name: 'Run agent', exact: true}).click();
        await expect(page.getByRole('heading', {name: 'You', exact: true})).toHaveCount(2);
        await page.reload();
        await expect(page.getByRole('heading', {name: 'You', exact: true})).toHaveCount(2);
        await page.getByRole('tab', {name: 'API access', exact: true}).click();
        await expect(page.getByRole('textbox', {name: 'Python API example'})).toHaveValue(/invoke_agent_runtime/);
        await page.getByRole('button', {name: 'Delete agent', exact: true}).click();
        const dialog = page.getByRole('dialog');
        await expect(dialog.getByRole('button', {name: 'Permanently delete', exact: true})).toBeDisabled();
        await dialog.getByRole('textbox', {name: 'Agent name to confirm deletion'}).fill('wrong name');
        await expect(dialog.getByRole('button', {name: 'Permanently delete', exact: true})).toBeDisabled();
        await dialog.getByRole('button', {name: 'Cancel', exact: true}).click();
        await expect(page.getByText('Deployed', {exact: true})).toBeVisible();
        await page.getByRole('button', {name: 'Delete agent', exact: true}).click();
        const heading = await dialog.getByText(/^Type .+ to confirm$/).innerText();
        await dialog.getByRole('textbox', {name: 'Agent name to confirm deletion'}).fill(heading.slice(5, -11));
        await dialog.getByRole('button', {name: 'Permanently delete', exact: true}).click();
        await expect(page.getByText('Agent deleted', {exact: true})).toBeVisible();
        await page.reload();
        await expect(page.getByText('Agent deleted', {exact: true})).toBeVisible();
      }
      if (template === 'Research' && !evaluation) {
        await page.getByRole('button', {name: 'Revise agent', exact: true}).click();
        await page.getByRole('button', {name: 'Next', exact: true}).click();
        await page.getByRole('textbox', {name: 'Agent name', exact: true}).fill('Revised research agent');
        await page.getByRole('button', {name: 'Next', exact: true}).click();
        await page.getByRole('button', {name: 'Next', exact: true}).click();
        await page.getByRole('button', {name: 'Save draft', exact: true}).click();
        await expect(page.getByText('v2', {exact: true})).toBeVisible();
        await expect(page.getByRole('button', {name: 'Run agent', exact: true})).toBeDisabled();
        await page.getByRole('button', {name: 'Deploy to AgentCore', exact: true}).click();
        await expect(page.getByText('Deployed', {exact: true})).toBeVisible();
        await expect(page.getByText('Skipped — no dataset', {exact: true})).toBeVisible();
      }
    });
  }
}

test('add an MCP server from AI Catalog without losing the domain draft or dataset', async ({page}) => {
  await page.goto('/');
  await page.request.post('/api/demo/session', {data: {persona_id: 'alex'}, headers: {origin: 'http://127.0.0.1:5189'}});
  await page.reload();
  await page.getByRole('button', {name: 'Create agent', exact: true}).click();
  await page.getByRole('radio', {name: 'Business templates Select Research', exact: true}).check();
  await page.getByRole('button', {name: 'Next', exact: true}).click();
  const instructions = 'Preserve this domain prompt while I browse available MCP connections.';
  await page.getByRole('textbox', {name: 'Your instructions', exact: true}).fill(instructions);
  await page.getByRole('button', {name: 'Next', exact: true}).click();
  const dataset = JSON.stringify([{id: 'domain-case', input: 'A synthetic question', expected_response: 'A synthetic answer'}]);
  await page.getByRole('textbox', {name: 'Evaluation dataset JSON', exact: true}).fill(dataset);
  const toggle = page.getByRole('button', {name: 'Open side navigation', exact: true});
  if (await toggle.isVisible()) await toggle.click();
  await page.getByRole('link', {name: 'AI Catalog', exact: true}).click();
  await page.getByRole('tab', {name: 'MCP servers', exact: true}).click();
  await page.getByRole('button', {name: 'Aurora knowledge', exact: true}).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByRole('button', {name: 'Add to draft', exact: true})).toHaveCount(1);
  await expect(page.getByTestId('catalog-operation').getByRole('button', {name: 'Add to draft'})).toHaveCount(0);
  await dialog.getByRole('button', {name: 'Add to draft', exact: true}).click();
  await expect(page.getByRole('textbox', {name: 'Evaluation dataset JSON', exact: true})).toHaveValue(dataset);
  await page.getByRole('button', {name: 'Previous', exact: true}).click();
  await expect(page.getByRole('textbox', {name: 'Your instructions', exact: true})).toHaveValue(instructions);
  await expect(page.getByRole('heading', {name: 'Tavily', exact: true})).toBeVisible();
  await expect(page.getByRole('heading', {name: 'Aurora knowledge', exact: true})).toBeVisible();
  await expect(page.getByRole('checkbox')).toHaveCount(0);
});
