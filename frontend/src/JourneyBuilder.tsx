import React, {useEffect, useRef, useState} from 'react';
import {
  Alert, Box, Button, Cards, Checkbox, Container, ExpandableSection, FileUpload, FormField, Header,
  Input, KeyValuePairs, Multiselect, Select, SpaceBetween, StatusIndicator, Textarea, Wizard,
} from '@cloudscape-design/components';
import type {Entry} from './AICatalog';

export type Api = <T>(path: string, body?: unknown) => Promise<T>;
export type Definition = {
  template_id: string; name: string; model_id: string; prompt: string; mcp_servers: string[]; tools: string[]; skills: string[];
  output_format: 'text' | 'json'; dataset: Record<string, unknown>[]; minimum_score: number;
  component_versions: Record<string, string>;
};
type Template = {
  id: string; version: string; name: string; description: string; prompt: string;
  tools: string[]; skills: string[]; sample_input: string; sample_dataset: Record<string, unknown>[];
};
type Options = {foundation: {name: string; version: string; capabilities: string[]}; templates: Template[]; region: string; mode: string; minimum_evaluation_score?: number};
export type Revision = {agentId: string; version: number; definition: Definition};
export type EditorState = {definition: Definition; dataset: string; step: number};
const empty: Definition = {template_id: '', name: '', model_id: '', prompt: '', mcp_servers: [], tools: [], skills: [],
  output_format: 'text', dataset: [], minimum_score: 0.7, component_versions: {}};
const editable = (source: Definition): Definition =>
  Object.fromEntries(Object.keys(empty).map(key => [key, source[key as keyof Definition] ?? empty[key as keyof Definition]])) as Definition;

export default function JourneyBuilder({api, onSaved, onCancel, revision, initialCapability, editorState, onEditorChange}: {
  api: Api; onSaved: (id: string) => void; onCancel: () => void; revision?: Revision;
  initialCapability?: Entry; editorState?: EditorState; onEditorChange?: (state: EditorState) => void;
}) {
  const [options, setOptions] = useState<Options | null>(null);
  const [catalog, setCatalog] = useState<Entry[]>([]);
  const [draft, setDraft] = useState<Definition>(editorState?.definition || (revision ? editable(revision.definition) : empty));
  const [dataset, setDataset] = useState(editorState?.dataset ?? (revision?.definition.dataset.length ? JSON.stringify(revision.definition.dataset, null, 2) : ''));
  const [step, setStep] = useState(editorState?.step || 0);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [files, setFiles] = useState<File[]>([]);
  const submission = useRef<{signature: string; key: string} | null>(null);
  const inFlight = useRef(false);
  const appliedCapability = useRef<Entry | null>(null);
  useEffect(() => { onEditorChange?.({definition: draft, dataset, step}); }, [draft, dataset, step, onEditorChange]);

  async function refresh() {
    setBusy(true); setError('');
    try {
      // The catalog page and this picker read exactly the same backend endpoint.
      const [config, snapshot] = await Promise.all([
        api<Options>('/journey/options'), api<{items: Entry[]}>('/catalog'),
      ]);
      setOptions(config); setCatalog(snapshot.items);
      setDraft(previous => ({...previous, minimum_score: Math.max(previous.minimum_score, config.minimum_evaluation_score ?? .7)}));
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  useEffect(() => { void refresh(); }, []);
  const template = options?.templates.find(item => item.id === draft.template_id);
  const available = (kind: string) => catalog.filter(item => item.kind === kind && item.supported !== false);
  const selectable = (id: string) => catalog.find(item => item.id === id && item.usable && item.execution_ready);
  const selectedIds = [draft.model_id, ...draft.mcp_servers, ...draft.tools, ...draft.skills].filter(Boolean);
  const stale = selectedIds.filter(id => !selectable(id) || selectable(id)?.version !== draft.component_versions[id]);
  const name = (id: string) => catalog.find(item => item.id === id)?.name || id;
  useEffect(() => {
    if (!initialCapability || !draft.template_id || !catalog.length || appliedCapability.current === initialCapability) return;
    appliedCapability.current = initialCapability;
    const item = selectable(initialCapability.id);
    if (!item) { setError('This catalog selection is unavailable. Request access or choose another connection.'); return; }
    if (item.kind === 'mcp_server') selectServers([...new Set([...draft.mcp_servers, item.id])]);
    if (item.kind === 'skill') select('skills', [...new Set([...draft.skills, item.id])]);
    if (item.kind === 'model') {
      const pins = {...draft.component_versions}; delete pins[draft.model_id];
      setDraft({...draft, model_id: item.id, component_versions: {...pins, [item.id]: item.version}});
    }
  }, [initialCapability, catalog, draft.template_id]);
  const chooseTemplate = (selected: Template) => {
    const tools = selected.tools.filter(id => {
      const tool = selectable(id);
      return tool?.parent_id && selectable(tool.parent_id);
    });
    const skills = selected.skills.filter(id => selectable(id));
    let model = selectable(draft.model_id)?.kind === 'model' ? draft.model_id : available('model').find(item => item.usable && item.execution_ready)?.id || '';
    if (initialCapability?.kind === 'model' && selectable(initialCapability.id)) model = initialCapability.id;
    if (initialCapability?.kind === 'tool' && selectable(initialCapability.id) && initialCapability.parent_id
        && selectable(initialCapability.parent_id) && !tools.includes(initialCapability.id)) tools.push(initialCapability.id);
    if (initialCapability?.kind === 'mcp_server' && selectable(initialCapability.id)) {
      for (const id of initialCapability.default_tool_ids || []) {
        if (selectable(id)?.parent_id === initialCapability.id && !tools.includes(id)) tools.push(id);
      }
    }
    if (initialCapability?.kind === 'skill' && selectable(initialCapability.id) && !skills.includes(initialCapability.id)) skills.push(initialCapability.id);
    const mcp_servers = [...new Set(tools.map(id => selectable(id)!.parent_id!))];
    const ids = [model, ...mcp_servers, ...tools, ...skills].filter(Boolean);
    setDraft({...draft, template_id: selected.id, name: draft.name || `My ${selected.name}`,
      model_id: model, prompt: selected.prompt, mcp_servers, tools, skills,
      component_versions: Object.fromEntries(ids.map(id => [id, selectable(id)!.version]))});
    setError('');
  };
  function select(kind: 'tools' | 'skills', ids: string[]) {
    const pins = {...draft.component_versions};
    for (const id of draft[kind]) if (!ids.includes(id)) delete pins[id];
    for (const id of ids) if (!pins[id]) pins[id] = selectable(id)!.version;
    setDraft({...draft, [kind]: ids, component_versions: pins});
  }
  function selectServers(ids: string[]) {
    const tools = draft.tools.filter(id => ids.includes(catalog.find(item => item.id === id)?.parent_id || ''));
    for (const id of ids.filter(id => !draft.mcp_servers.includes(id))) {
      for (const tool of catalog.find(item => item.id === id)?.default_tool_ids || []) {
        if (selectable(tool)?.parent_id === id && !tools.includes(tool)) tools.push(tool);
      }
    }
    const pins = {...draft.component_versions};
    for (const id of [...draft.mcp_servers, ...draft.tools]) {
      if (![...ids, ...tools].includes(id)) delete pins[id];
    }
    for (const id of [...ids, ...tools]) if (!pins[id]) pins[id] = selectable(id)!.version;
    setDraft({...draft, mcp_servers: ids, tools, component_versions: pins});
  }
  function parseDataset(): Record<string, unknown>[] {
    if (!dataset.trim()) return [];
    let value: unknown;
    try { value = JSON.parse(dataset); } catch { throw new Error('Evaluation dataset is not valid JSON. Correct it or clear the field to skip evaluation.'); }
    if (!Array.isArray(value) || value.length > 20 || value.some(item => !item || typeof item !== 'object' || Array.isArray(item))) {
      throw new Error('Use a JSON array with at most 20 evaluation cases.');
    }
    if (new TextEncoder().encode(dataset).length > 32768) throw new Error('Dataset must be at most 32 KiB.');
    const ids = value.map(item => item.id);
    if (value.some(item => typeof item.id !== 'string' || !item.id || typeof item.input !== 'string' || !item.input.trim())) {
      throw new Error('Each evaluation case needs an id and a nonempty input.');
    }
    if (new Set(ids).size !== ids.length) throw new Error('Evaluation case IDs must be unique.');
    return value;
  }
  function validate(until: number) {
    if (!template) throw new Error('Select a template from the platform library.');
    if (until >= 1 && (!draft.name.trim() || draft.prompt.trim().length < 10 || !draft.model_id)) {
      throw new Error('Provide a name, instructions of at least 10 characters, and a model.');
    }
    if (until >= 1 && stale.length) throw new Error('Some capabilities changed or are unavailable. Remove and reselect them from the current catalog.');
    if (until >= 1 && draft.mcp_servers.some(server => !draft.tools.some(id => catalog.find(item => item.id === id)?.parent_id === server))) {
      throw new Error('Choose at least one allowed tool under each selected MCP server, or remove that server.');
    }
    if (until >= 2) parseDataset();
  }
  async function submit(deploy: boolean) {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true); setError('');
    try {
      validate(2);
      const body = {definition: {...draft, dataset: parseDataset()}, deploy,
        ...(revision ? {base_version: revision.version} : {})};
      const signature = JSON.stringify(body);
      if (submission.current?.signature !== signature) submission.current = {signature, key: crypto.randomUUID()};
      const result = await api<{agent_id: string}>(revision ? `/journey/agents/${revision.agentId}/versions` : '/journey/agents',
        {...body, idempotency_key: submission.current.key});
      onSaved(result.agent_id);
    } catch (e) { setError((e as Error).message); }
    finally { inFlight.current = false; setBusy(false); }
  }
  function download() {
    if (!template) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(template.sample_dataset, null, 2)], {type: 'application/json'}));
    const link = document.createElement('a'); link.href = url; link.download = `${template.id}-evaluation.json`; link.click();
    URL.revokeObjectURL(url);
  }
  let caseCount = 0;
  try { caseCount = parseDataset().length; } catch { /* The validation error is shown on navigation/submission. */ }
  const capabilityOptions = (kind: string) => available(kind).map(item => ({value: item.id, label: item.name,
    description: item.description, disabled: !item.usable || !item.execution_ready}));
  const chosen = (ids: string[]) => ids.map(id => ({value: id, label: name(id)}));
  return <SpaceBetween size="l">
    {error && <Alert type="error" header="Review your agent">{error}</Alert>}
    {options && <Container><SpaceBetween size="s">
      <Box>Built on {options.foundation.name} · v{options.foundation.version}</Box>
      <Box variant="small">Models, MCP servers and skills come from your workspace AI Catalog.</Box>
      <Button iconName="refresh" disabled={busy} onClick={() => void refresh()}>Refresh catalog</Button>
    </SpaceBetween></Container>}
    {!!stale.length && <Alert type="warning">These selections changed or are no longer available: {stale.map(name).join(', ')}. Remove and reselect them before deployment.</Alert>}
    <Wizard activeStepIndex={step} isLoadingNextStep={busy || !options}
      i18nStrings={{stepNumberLabel: n => `Step ${n}`, collapsedStepsLabel: (n, total) => `Step ${n} of ${total}`,
        skipToButtonLabel: item => `Skip to ${item.title}`, navigationAriaLabel: 'Create agent steps',
        cancelButton: 'Cancel', previousButton: 'Previous', nextButton: 'Next', submitButton: 'Deploy to AgentCore', optional: 'optional'}}
      onNavigate={({detail}) => { try { if (detail.requestedStepIndex > step) validate(detail.requestedStepIndex - 1); setError(''); setStep(detail.requestedStepIndex); } catch (e) { setError((e as Error).message); } }}
      onCancel={onCancel} onSubmit={() => void submit(true)}
      steps={[
        {title: 'Choose a template', description: 'Start from a platform-published template and make it your own.',
          content: <Cards items={options?.templates || []} trackBy="id" selectionType="single"
            selectedItems={template ? [template] : []} onSelectionChange={({detail}) => { if (detail.selectedItems[0]) chooseTemplate(detail.selectedItems[0]); }}
            ariaLabels={{selectionGroupLabel: 'Business templates', itemSelectionLabel: (_, item) => `Select ${item.name}`}}
            cardsPerRow={[{cards: 1}, {minWidth: 650, cards: 2}]}
            cardDefinition={{header: item => <Header variant="h3">{item.name}</Header>, sections: [
              {id: 'description', content: item => item.description},
              {id: 'example', header: 'Try asking', content: item => item.sample_input},
            ]}} />},
        {title: 'Configure your agent', description: 'Choose catalog capabilities and describe the work.',
          content: <Container header={<Header variant="h2">Your domain harness</Header>}><SpaceBetween size="l">
            <FormField label="Agent name"><Input value={draft.name} onChange={({detail}) => setDraft({...draft, name: detail.value})}/></FormField>
            <FormField label="Model"><Select selectedOption={draft.model_id ? {value: draft.model_id, label: name(draft.model_id)} : null}
              placeholder="Choose an approved model" options={capabilityOptions('model')} onChange={({detail}) => {
                const id = detail.selectedOption.value!; const pins = {...draft.component_versions}; delete pins[draft.model_id];
                setDraft({...draft, model_id: id, component_versions: {...pins, [id]: selectable(id)!.version}});
              }}/></FormField>
            <FormField label="MCP servers" description="Connect approved sources and services. Your agent can use their allowed tools when it runs.">
              <Multiselect options={capabilityOptions('mcp_server')} selectedOptions={chosen(draft.mcp_servers)}
                placeholder="Choose MCP servers from AI Catalog" onChange={({detail}) => selectServers(detail.selectedOptions.map(item => item.value!))}/>
            </FormField>
            {draft.mcp_servers.map(id => {
              const server = catalog.find(item => item.id === id);
              const children = available('tool').filter(item => item.parent_id === id);
              const count = children.filter(item => draft.tools.includes(item.id)).length;
              return <Container key={id} header={<Header variant="h3">{name(id)}</Header>}>
                <SpaceBetween size="s">
                  <Box>{server?.description}</Box>
                  <StatusIndicator type={selectable(id) ? 'success' : 'warning'}>
                    {selectable(id) ? 'Available through AgentCore Gateway' : 'Connection unavailable'}
                  </StatusIndicator>
                  <Box variant="small">{server?.data_handling}</Box>
                  <ExpandableSection headerText={`Tool permissions (${count} allowed)`}>
                    <SpaceBetween size="s">
                      <Box variant="small">Only checked tools are available to this agent. Newly published tools require a new selection.</Box>
                      {children.map(tool => <Checkbox key={tool.id} checked={draft.tools.includes(tool.id)}
                        disabled={!selectable(tool.id) && !draft.tools.includes(tool.id)}
                        description={tool.description} onChange={({detail}) => select('tools',
                          detail.checked ? [...draft.tools, tool.id] : draft.tools.filter(id => id !== tool.id))}>
                        {tool.name}{!selectable(tool.id) ? ' — unavailable' : ''}
                      </Checkbox>)}
                      {!children.length && <Box>No approved tools are available for this MCP server.</Box>}
                    </SpaceBetween>
                  </ExpandableSection>
                </SpaceBetween>
              </Container>;
            })}
            <FormField label="Skills" description="Versioned instructions published by your platform.">
              <Multiselect options={capabilityOptions('skill')} selectedOptions={chosen(draft.skills)}
                placeholder="Choose skills from AI Catalog" onChange={({detail}) => select('skills', detail.selectedOptions.map(item => item.value!))}/>
            </FormField>
            <FormField label="Your instructions"><Textarea rows={7} value={draft.prompt} onChange={({detail}) => setDraft({...draft, prompt: detail.value})}/></FormField>
            <FormField label="Output format"><Select selectedOption={{value: draft.output_format, label: draft.output_format === 'text' ? 'Readable text' : 'JSON'}}
              options={[{value: 'text', label: 'Readable text'}, {value: 'json', label: 'JSON'}]}
              onChange={({detail}) => setDraft({...draft, output_format: detail.selectedOption.value as 'text' | 'json'})}/></FormField>
          </SpaceBetween></Container>},
        {title: 'Evaluation', description: 'Optional. Upload cases to evaluate the deployed agent.',
          content: <Container header={<Header variant="h2">Evaluation dataset (optional)</Header>}><SpaceBetween size="l">
            <Alert type="info">{caseCount ? `${caseCount} cases will run with AgentCore on-demand evaluation after deployment.` : 'No dataset provided. Your agent will deploy and evaluation will be skipped.'}</Alert>
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="download" onClick={download}>Download JSON sample</Button>
              <Button onClick={() => { setDataset(JSON.stringify(template?.sample_dataset || [], null, 2)); setError(''); }}>Use synthetic sample</Button>
              <Button onClick={() => { setDataset(''); setFiles([]); setError(''); }}>Clear dataset</Button>
            </SpaceBetween>
            <FormField label="Upload JSON" constraintText="Up to 20 cases, maximum 32 KiB.">
              <FileUpload value={files} accept=".json,application/json" i18nStrings={{uploadButtonText: () => 'Choose JSON file', dropzoneText: () => 'Drop JSON file',
                removeFileAriaLabel: n => `Remove file ${n + 1}`, limitShowFewer: 'Show fewer', limitShowMore: 'Show more', errorIconAriaLabel: 'Error'}}
                onChange={({detail}) => { const file = detail.value[0]; setFiles(detail.value);
                  if (file) { if (file.size > 32768) { setError('Dataset must be at most 32 KiB.'); return; }
                    void file.text().then(text => { setDataset(text); setError(''); }).catch(() => setError('Could not read the selected file.')); }
                }}/>
            </FormField>
            <FormField label="Evaluation dataset JSON" description="Each case has an id and input. Add expected_response to provide a reference answer.">
              <Textarea rows={12} value={dataset} placeholder="Leave empty to skip evaluation" onChange={({detail}) => setDataset(detail.value)}/>
            </FormField>
            {caseCount > 0 && <FormField label="Minimum score" constraintText="0 to 1, applied to each case.">
              <Input type="number" value={String(draft.minimum_score)} onChange={({detail}) => setDraft({...draft, minimum_score: Number(detail.value)})}/>
            </FormField>}
          </SpaceBetween></Container>},
        {title: 'Review and deploy', description: 'Save a version, deploy it, and optionally evaluate it.',
          content: <SpaceBetween size="l"><Container header={<Header variant="h2">Ready to build</Header>}>
            <KeyValuePairs columns={2} items={[
              {label: 'Agent', value: draft.name}, {label: 'Template', value: template?.name},
              {label: 'Model', value: name(draft.model_id)}, {label: 'Region', value: options?.region},
              {label: 'MCP servers', value: draft.mcp_servers.map(id =>
                `${name(id)} (${draft.tools.filter(tool => catalog.find(item => item.id === tool)?.parent_id === id).length} allowed tools)`).join(', ') || 'None'},
              {label: 'Skills', value: draft.skills.map(name).join(', ') || 'None'},
              {label: 'Evaluation', value: caseCount ? `Run ${caseCount} cases after deployment` : 'Skip — no dataset'},
              {label: 'Version', value: revision ? `v${revision.version + 1}` : 'v1'},
            ]}/><ExpandableSection headerText="Review instructions"><Box>{draft.prompt}</Box></ExpandableSection>
          </Container><Button disabled={busy} onClick={() => void submit(false)}>Save draft</Button></SpaceBetween>},
      ]}/>
  </SpaceBetween>;
}
