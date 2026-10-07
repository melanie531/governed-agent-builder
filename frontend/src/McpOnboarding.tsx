import React, {useEffect, useMemo, useRef, useState} from 'react';
import {
  Alert, Box, Button, Checkbox, Container, ExpandableSection, FormField, Header,
  Input, KeyValuePairs, Multiselect, RadioGroup, Select, SpaceBetween, StatusIndicator, Table,
  Textarea, Wizard,
} from '@cloudscape-design/components';
import type {Api} from './JourneyBuilder';
import McpAuthentication from './McpAuthentication';
import McpIamAuthentication from './McpIamAuthentication';
import McpOAuthAuthentication, {oauthMethods} from './McpOAuthAuthentication';
import type {PythonMcp} from './McpPythonUpload';
import McpPackageUpload from './McpPackageUpload';
import McpDeployments from './McpDeployments';
import {AuthenticationConnections, RegistrationManagement} from './McpConnectionManagement';
import McpToolDefinitions, {parseToolDefinitions, type McpTool} from './McpToolDefinitions';

type Connection = {id: string; name: string; auth_type: string; allowed_origins: string[]; requires_schema?: boolean; callback_url?: string};
type Options = {enabled: boolean; reason?: string; workspaces: string[]; connections: Connection[]; credential_setup?: boolean; runtime_iam_setup?: boolean; oauth_setup?: boolean; oauth_grants?: string[]; python_bundle?: string; python_packages?: boolean};
type Tool = McpTool;
type Record = {
  id: string; job_id?: string; name: string; description: string; endpoint: string; connection_id: string;
  workspaces: string[]; phase: string; error?: string; tools?: Tool[]; selected_tools?: string[];
  discovery_digest?: string; gateway_target_id?: string; registry_record_arn?: string;
  retry_available?: boolean;
  legacy?: boolean; revision?: number; schema_source?: string;
};
type Payload = {name: string; description: string; endpoint: string; connection_id: string; workspaces: string[]; idempotency_key: string; tool_schema?: Tool[]};
type Intent = {payload: Payload; id?: string};
const stopped = (phase: string) => ['REVIEW', 'READY', 'FAILED', 'NEEDS_RECONCILIATION', 'DELETED'].includes(phase);
const phaseLabel = (phase: string) => phase[0] + phase.slice(1).toLowerCase().replaceAll('_', ' ');

function Onboarding({api, identityKey}: {api: Api; identityKey: string}) {
  const storageKey = 'mcp-onboarding:' + identityKey;
  const [options, setOptions] = useState<Options>(), [items, setItems] = useState<Record[]>([]);
  const [record, setRecord] = useState<Record>(), [selectedId, setSelectedId] = useState('');
  const [form, setForm] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [name, setName] = useState(''), [description, setDescription] = useState(''), [endpoint, setEndpoint] = useState('');
  const [connectionId, setConnectionId] = useState(''), [workspaces, setWorkspaces] = useState<string[]>([]);
  const [authMethod, setAuthMethod] = useState('new'), [authSaved, setAuthSaved] = useState(false);
  const [selectedTools, setSelectedTools] = useState<string[]>([]);
  const [toolSchema, setToolSchema] = useState('');
  const [source, setSource] = useState('endpoint');
  const [uploadBusy, setUploadBusy] = useState(false);
  const [authBusy, setAuthBusy] = useState(false), [step, setStep] = useState(0);
  const [uploaded, setUploaded] = useState<PythonMcp>();
  const [intent, setIntent] = useState<Intent>(), [storageError, setStorageError] = useState('');
  const [notRecorded, setNotRecorded] = useState(false);
  const lock = useRef(false);
  const connection = options?.connections.find(c => c.id === connectionId);
  const definitions = useMemo(() => parseToolDefinitions(toolSchema), [toolSchema]);
  let endpointOrigin = '';
  try {
    const url = new URL(endpoint.trim());
    if (url.protocol === 'https:' && !url.username && !url.password && !url.hash) endpointOrigin = url.origin;
  } catch { /* The server step shows guidance until a valid endpoint is supplied. */ }
  const duplicate = items.find(item => item.id !== intent?.id
    && (item.endpoint === endpoint.trim() || item.name.toLowerCase() === name.trim().toLowerCase()));
  const serverReady = name.trim().length >= 2 && !!endpointOrigin && (source === 'endpoint' || !!uploaded) && !duplicate;
  const authenticationReady = authMethod === 'existing' && !!connection?.allowed_origins.includes(endpointOrigin);
  const toolsReady = authenticationReady && !!workspaces.length && (!connection?.requires_schema || !!definitions.tools);
  const defaultOauthMethod = oauthMethods.find(m => !options?.oauth_grants || options.oauth_grants.includes(m.grant))?.value || 'oauth-3lo';
  function show(value: Record) {
    if (!value?.id || !value.phase) throw new Error('Incomplete onboarding status. Check status before continuing.');
    setRecord(value);
    setItems(previous => [...(value.phase === 'DELETED' ? [] : [value]), ...previous.filter(i => i.id !== value.id)]);
  }
  async function load() {
    const [o, rows, legacy] = await Promise.all([
      api<Options>('/admin/mcp/onboarding-options'), api<{items: Record[]}>('/admin/mcp/onboarding'),
      api<{items: Record[]}>('/admin/mcp/servers'),
    ]);
    setOptions(o); setItems([...rows.items, ...legacy.items.map(i => ({...i, legacy: true}))]);
  }
  useEffect(() => {
    void load().catch(e => setError(e.message));
    try {
      const raw = sessionStorage.getItem(storageKey);
      if (raw) {
        const saved = JSON.parse(raw) as Intent;
        if (!saved.payload?.idempotency_key || !saved.payload.endpoint) throw new Error();
        setIntent(saved);
        if (saved.id) setSelectedId(saved.id);
      }
    } catch {setStorageError('The saved onboarding request is unavailable. Restore session storage before starting another request.');}
  }, []);
  useEffect(() => {
    if (!selectedId || busy) return;
    let active = true, timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const isLegacy = items.some(i => i.id === selectedId && i.legacy);
        const value = {...await api<Record>('/admin/mcp/' + (isLegacy ? 'servers/' : 'onboarding/') + selectedId), legacy: isLegacy};
        if (!active) return;
        show(value);
        if (!stopped(value.phase)) timer = setTimeout(poll, 3000);
      } catch (e) {if (active) setError((e as Error).message);}
    }
    void poll();
    return () => {active = false; clearTimeout(timer);};
  }, [selectedId, busy, record?.phase]);
  async function act(operation: () => Promise<void>) {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError('');
    try {await operation();}
    catch (e) {setError((e as Error).message);}
    finally {lock.current = false; setBusy(false);}
  }
  function start() {
    try {sessionStorage.removeItem(storageKey);}
    catch {setStorageError('Session storage is required to retain the onboarding request.'); return;}
    setIntent(undefined); setRecord(undefined); setSelectedId(''); setSelectedTools([]);
    setNotRecorded(false);
    setName(''); setEndpoint(''); setDescription('');
    setToolSchema('');
    setSource('endpoint');
    setUploaded(undefined);
    setConnectionId('');
    setAuthMethod(options?.credential_setup ? 'new' : 'existing'); setAuthSaved(false);
    setWorkspaces(options?.workspaces.includes('research') ? ['research'] : []);
    setForm(true); setStep(0); setError('');
  }
  async function connect() {
    let origin: string;
    try {
      const url = new URL(endpoint.trim()); origin = url.origin;
      if (url.protocol !== 'https:' || url.username || url.password || url.hash) throw new Error();
    } catch {setError('Enter an HTTPS MCP endpoint without embedded credentials.'); return;}
    if (!connection?.allowed_origins.includes(origin)) {setError('Choose a credential connection approved for this endpoint host.'); return;}
    if (name.trim().length < 2 || !workspaces.length) {setError('Enter a connection name and select at least one workspace.'); return;}
    await act(async () => {
      const payload: Payload = {name: name.trim(), description, endpoint: endpoint.trim(), connection_id: connectionId,
        workspaces, idempotency_key: crypto.randomUUID()};
      if (connection.requires_schema) {
        if (!definitions.tools) throw new Error(definitions.error);
        payload.tool_schema = definitions.tools;
      }
      const pending = {payload};
      sessionStorage.setItem(storageKey, JSON.stringify(pending)); setIntent(pending);
      try {
        const result = await api<{id: string}>('/admin/mcp/onboarding', payload);
        const saved = {payload, id: result.id};
        sessionStorage.setItem(storageKey, JSON.stringify(saved)); setIntent(saved);
        setStep(3); setSelectedId(result.id);
      } catch (e) {
        // Definitive input rejection has no native side effect. An uncertain
        // response retains the intent and requires GET reconciliation.
        const status = (e as Error & {status?: number}).status;
        if (status && [409, 422, 429].includes(status)) {
          sessionStorage.removeItem(storageKey); setIntent(undefined);
        }
        throw e;
      }
    });
  }
  async function check() {
    await act(async () => {
      let value: Record;
      try {
        value = selectedId ? {...await api<Record>('/admin/mcp/' + (record?.legacy ? 'servers/' : 'onboarding/') + selectedId), legacy: record?.legacy}
          : await api<Record>('/admin/mcp/onboarding-requests/' + intent!.payload.idempotency_key);
      } catch (e) {
        if (!selectedId && (e as Error & {status?: number}).status === 404) {
          setNotRecorded(true); return;
        }
        throw e;
      }
      setNotRecorded(false);
      show(value); setSelectedId(value.id); if (form) setStep(3);
      if (intent) {
        const saved = {...intent, id: value.id};
        sessionStorage.setItem(storageKey, JSON.stringify(saved)); setIntent(saved);
      }
    });
  }
  async function publish() {
    await act(async () => {
      const key = storageKey + ':publish:' + record!.id + (record!.revision && record!.revision > 1 ? ':r' + record!.revision : '');
      const payload = {discovery_digest: record!.discovery_digest, tools: [...selectedTools].sort(), idempotency_key: crypto.randomUUID()};
      const raw = sessionStorage.getItem(key);
      const saved = raw ? JSON.parse(raw) as typeof payload : payload;
      if (raw && (saved.discovery_digest !== payload.discovery_digest || JSON.stringify(saved.tools) !== JSON.stringify(payload.tools))) {
        throw new Error('A publication request is already retained. Check status before changing the selection.');
      }
      sessionStorage.setItem(key, JSON.stringify(saved));
      await api('/admin/mcp/onboarding/' + record!.id + '/publish', saved);
      show(await api<Record>('/admin/mcp/onboarding/' + record!.id));
    });
  }
  const unresolved = !!intent && (!record || !stopped(record.phase));
  async function authenticationSaved(id: string) {
    const updated = await api<Options>('/admin/mcp/onboarding-options');
    setOptions(updated); setConnectionId(id); setAuthMethod('existing'); setAuthSaved(true);
    if (uploaded?.connection_mode === 'PACKAGE') {
      setEndpoint(updated.connections.find(c => c.id === id)?.auth_type === 'GATEWAY_IAM_ROLE'
        ? uploaded.endpoint : uploaded.bearer_endpoint || uploaded.endpoint);
    }
  }
  function authenticationChanged(method: string, id = connectionId) {
    setAuthMethod(method); setAuthSaved(false);
    if (uploaded?.connection_mode === 'PACKAGE') {
      const iam = method === 'iam' || method === 'existing'
        && options?.connections.find(c => c.id === id)?.auth_type === 'GATEWAY_IAM_ROLE';
      setEndpoint(iam ? uploaded.endpoint : uploaded.bearer_endpoint || uploaded.endpoint);
    }
  }
  const bearerUnavailable = uploaded?.connection_mode === 'PACKAGE' && !uploaded.bearer_endpoint;
  const locked = busy || uploadBusy || authBusy;
  const recordPanel = record && <Container header={<Header variant="h2" actions={<Button disabled={busy} onClick={() => void check()}>Check status</Button>}>{record.name}</Header>}><SpaceBetween size="m">
    <KeyValuePairs columns={2} items={[
      {label: 'Endpoint', value: record.endpoint}, {label: 'Status', value: phaseLabel(record.phase)},
      {label: 'Gateway target', value: record.gateway_target_id || 'Pending'},
      {label: 'Registry record', value: record.registry_record_arn || 'Pending'},
      {label: 'Workspaces', value: record.workspaces.join(', ')},
    ]}/>
    {!form && record.phase !== 'DELETED' && <RegistrationManagement key={record.id} api={api} identityKey={identityKey} record={record} options={options}
      onChanged={async () => {await load(); show(await api<Record>('/admin/mcp/onboarding/' + record.id)); setSelectedTools([]);}}/>}
    {record.phase === 'DELETED' && <Alert type="success">Connection deleted. Its remote MCP server and saved authentication were preserved.</Alert>}
    {record.error && <Alert type="warning">{record.error}</Alert>}
    {!record.legacy && record.phase === 'NEEDS_RECONCILIATION' && <Button disabled={busy} onClick={() => void act(async () => {
      await api('/admin/mcp/onboarding/' + record.id + '/reconcile', {});
      show(await api<Record>('/admin/mcp/onboarding/' + record.id));
    })}>Reconcile connection</Button>}
    {record.phase === 'NEEDS_RECONCILIATION' && record.retry_available && <SpaceBetween size="s">
      <Box>If creation failed, retry the original request. The platform first checks for an existing result and reuses the original request identifier if it must send it again.</Box>
      <Button disabled={busy} onClick={() => void act(async () => {
        await api('/admin/mcp/onboarding/' + record.id + '/retry', {job_id: record.job_id});
        show(await api<Record>('/admin/mcp/onboarding/' + record.id));
      })}>Retry original request</Button>
    </SpaceBetween>}
    {record.phase === 'REVIEW' && <>
      <Header variant="h3">{record.schema_source === 'supplied' ? 'Review supplied tools' : 'Review discovered tools'}</Header>
      <Box>Select up to 20 tools to make available in the selected workspaces.</Box>
      {record.tools?.map(t => <SpaceBetween size="xs" key={t.name}>
        <Checkbox checked={selectedTools.includes(t.name)} disabled={selectedTools.length >= 20 && !selectedTools.includes(t.name)} onChange={({detail}) => setSelectedTools(previous => detail.checked ? [...previous, t.name] : previous.filter(n => n !== t.name))}>{t.name}</Checkbox>
        <Box>{t.description}</Box><ExpandableSection headerText="Input schema"><pre style={{whiteSpace: 'pre-wrap'}}>{JSON.stringify(t.inputSchema, null, 2)}</pre></ExpandableSection>
      </SpaceBetween>)}
      {!form && <Button variant="primary" disabled={busy || !selectedTools.length} onClick={() => void publish()}>Approve and publish</Button>}
    </>}
    {!stopped(record.phase) && <StatusIndicator type="loading">Preparing the connection. You can check its saved status without submitting it again.</StatusIndicator>}
    {record.phase === 'READY' && <Alert type="success" header="Connection published">{!record.legacy && 'The Registry record is approved. '}Switch to Business User and select this connection when creating an agent.</Alert>}
  </SpaceBetween></Container>;
  return <SpaceBetween size="l">
    <Container header={<Header variant="h2" description="Connect a server, set up authentication, review its tools and publish them for your workspaces."
      actions={!form && <Button variant="primary" disabled={!options?.enabled || locked || unresolved || !!storageError} onClick={start}>Add MCP connection</Button>}>MCP connections</Header>}>
      <Box>{form ? 'Complete these steps in order. Authentication is set up here as part of the connection.'
        : 'Start with an existing MCP endpoint, or upload a complete Python package to host a new server. Authentication and tool setup are part of the same flow.'}</Box>
    </Container>
    {options && !options.enabled && <Alert type="info" header="Onboarding is not configured">{options.reason}</Alert>}
    {(error || storageError) && <Alert type="error">{error || storageError}</Alert>}
    {intent && !record && <Alert type="info" header="Saved onboarding request">
      The request is retained. Check its status before submitting another connection.
      <Box margin={{top: 's'}}><Button disabled={busy} onClick={() => void check()}>Check status</Button></Box>
      {notRecorded && <Box margin={{top: 's'}}><SpaceBetween size="s">
        <Box>No request was recorded. You can explicitly retry the same retained request.</Box>
        <Button disabled={busy} onClick={() => void act(async () => {
          const result = await api<{id: string}>('/admin/mcp/onboarding', intent.payload);
          const saved = {...intent, id: result.id};
          sessionStorage.setItem(storageKey, JSON.stringify(saved)); setIntent(saved);
          setNotRecorded(false); if (form) setStep(3); setSelectedId(result.id);
        })}>Retry retained request</Button>
      </SpaceBetween></Box>}
    </Alert>}
    {form && duplicate && step < 3 && <Alert type="warning" header="This connection is already registered">
      The name or endpoint belongs to {duplicate.name}. Change the name if this is a different server, or open the existing connection.
      <Box margin={{top: 's'}}><Button onClick={() => {
        setForm(false); setSelectedId(duplicate.id); setRecord(undefined); setSelectedTools([]);
      }}>View existing connection</Button></Box>
    </Alert>}
    {form && <Wizard activeStepIndex={step}
      i18nStrings={{stepNumberLabel: n => `Step ${n}`, collapsedStepsLabel: (n, total) => `Step ${n} of ${total}`,
        navigationAriaLabel: 'Add MCP connection steps'}}
      onNavigate={({detail}) => {
        if (locked || intent || detail.requestedStepIndex === 3) return;
        if (detail.requestedStepIndex > 0 && !serverReady) {setError('Complete the server step before continuing.'); return;}
        if (detail.requestedStepIndex > 1 && !authenticationReady) {setError('Save or select authentication for this endpoint before continuing.'); return;}
        setError(''); setStep(detail.requestedStepIndex);
      }}
      customPrimaryActions={<div style={{display: 'flex', flexWrap: 'wrap', gap: '8px', justifyContent: 'flex-end'}}>
        <Button disabled={locked || (!!intent && !record)} onClick={() => setForm(false)}>{record ? 'Close setup' : 'Cancel'}</Button>
        {step > 0 && !intent && <Button disabled={locked} onClick={() => {setError(''); setStep(step - 1);}}>Previous</Button>}
        {step < 2 && <Button variant="primary" disabled={locked || !!intent || !!storageError || (step === 0 ? !serverReady : !serverReady || !authenticationReady)}
          onClick={() => {setError(''); setStep(step + 1);}}>Next</Button>}
        {step === 2 && <Button variant="primary" loading={busy} disabled={locked || !serverReady || !toolsReady || !!intent || !!storageError}
          onClick={() => void connect()}>{connection?.requires_schema ? 'Connect and review' : 'Connect and discover'}</Button>}
        {step === 3 && record?.phase === 'REVIEW' && <Button variant="primary" disabled={busy || !selectedTools.length}
          onClick={() => void publish()}>Approve and publish</Button>}
        {step === 3 && record?.phase === 'READY' && <Button variant="primary" onClick={() => setForm(false)}>Done</Button>}
      </div>}
      steps={[
        {title: 'Server', description: 'Connect an existing server or deploy a complete package.', content: <Container><SpaceBetween size="m">
      <FormField label="MCP source"><RadioGroup ariaLabel="MCP source"
        items={[
          {value: 'endpoint', label: 'Connect hosted endpoint', disabled: uploadBusy, description: 'Use an existing HTTPS MCP server.'},
          {value: 'package', label: 'Upload MCP package (.zip)', disabled: uploadBusy || !options?.python_packages,
            description: 'Deploy a complete Python server with its own supporting files and dependencies.'},
          {value: 'saved-package', label: 'Use a deployed package', disabled: uploadBusy || !options?.python_packages,
            description: 'Continue with a ready server already hosted by Studio.'},
        ]}
        value={source}
        onChange={({detail}) => {
          setSource(detail.value); setEndpoint(''); setToolSchema(''); setUploaded(undefined);
          setAuthSaved(false); setConnectionId(''); setAuthMethod('new');
        }}/></FormField>
      <FormField label="Connection name"><Input ariaLabel="Connection name" value={name} onChange={({detail}) => setName(detail.value)}/></FormField>
      <FormField label="Description"><Textarea ariaLabel="Connection description" value={description} onChange={({detail}) => setDescription(detail.value)}/></FormField>
      {source !== 'endpoint' && options?.python_packages && <McpPackageUpload key={source} api={api} identityKey={identityKey} name={name}
        existingOnly={source === 'saved-package'} initial={uploaded}
        onReset={() => {setEndpoint(''); setToolSchema(''); setAuthSaved(false); setConnectionId(''); setUploaded(undefined);}}
        onBusyChange={setUploadBusy} onReady={runtime => {
          setUploaded(runtime);
          setName(runtime.name); setEndpoint(runtime.endpoint); setToolSchema(JSON.stringify({tools: runtime.tools}, null, 2));
          setAuthMethod(runtime.connection_mode === 'PACKAGE' ? '' : runtime.connection_mode === 'IAM' ? 'iam' : defaultOauthMethod);
          setAuthSaved(false);
        }}/>}
      {(source === 'endpoint' || !!endpoint) && <FormField label="MCP endpoint URL"
        description={source === 'endpoint' ? 'The full HTTPS Streamable HTTP URL supplied by your server. This path does not deploy a server.'
          : 'Filled from your ready package. Continue to set up authentication for this server.'}
        errorText={endpoint && !endpointOrigin ? 'Enter an HTTPS URL without embedded credentials or a fragment.' : undefined}>
        <Input ariaLabel="MCP endpoint URL" value={endpoint} disabled={source !== 'endpoint'} placeholder="https://your-server.example.com/mcp"
          onChange={({detail}) => {setEndpoint(detail.value); setConnectionId(''); setAuthSaved(false); setToolSchema('');}}/>
      </FormField>}
      </SpaceBetween></Container>},
      {title: 'Authentication', description: 'Configure access here, or choose saved authentication for this server.', content: <Container><SpaceBetween size="m">
      <KeyValuePairs items={[{label: 'Server endpoint', value: <span style={{wordBreak: 'break-all'}}>{endpoint}</span>}]}/>
      <FormField label="Authentication method">
        <Select ariaLabel="Authentication method" disabled={authBusy} placeholder="Choose authentication" options={[
          {value: 'new', label: 'API key / PAT', disabled: !options?.credential_setup || bearerUnavailable},
          {value: 'iam', label: 'AWS IAM / AgentCore Runtime', disabled: !options?.runtime_iam_setup},
          ...oauthMethods.map(m => ({value: m.value, label: m.label,
            disabled: bearerUnavailable || !options?.oauth_setup || (!!options.oauth_grants && !options.oauth_grants.includes(m.grant))})),
          {value: 'existing', label: 'Existing connection'},
        ]} selectedOption={authMethod ? {value: authMethod, label: oauthMethods.find(m => m.value === authMethod)?.label
          || (authMethod === 'new' ? 'API key / PAT' : authMethod === 'iam' ? 'AWS IAM / AgentCore Runtime' : 'Existing connection')} : null}
          onChange={({detail}) => authenticationChanged(detail.selectedOption.value!)}/>
      </FormField>
      {bearerUnavailable && <Box>This package uses IAM access. Packages that accept provider tokens must include a bearer validation tool in their package manifest.</Box>}
      {authMethod === 'new' ? <McpAuthentication api={api} identityKey={identityKey} name={name} endpoint={endpoint} onSaved={authenticationSaved} onBusyChange={setAuthBusy}/>
        : authMethod === 'iam' ? <McpIamAuthentication api={api} identityKey={identityKey} name={name} endpoint={endpoint} onSaved={authenticationSaved} onBusyChange={setAuthBusy}/>
        : authMethod.startsWith('oauth-') ? <McpOAuthAuthentication key={authMethod} api={api} identityKey={identityKey} name={name}
          endpoint={endpoint} grant={authMethod === 'oauth-2lo' ? 'CLIENT_CREDENTIALS' : 'AUTHORIZATION_CODE'}
          onSaved={authenticationSaved} allowedGrants={options?.oauth_grants} onBusyChange={setAuthBusy}/>
        : authMethod === 'existing' ? <FormField label="Authentication connection" description="Choose saved authentication approved for this endpoint. It will be attached to this connection."
          errorText={connection && !authenticationReady ? 'Choose authentication approved for this endpoint host.' : undefined}>
        <Select ariaLabel="Authentication connection" options={options?.connections.map(c => ({value: c.id, label: c.name, description: c.auth_type,
          disabled: !c.allowed_origins.includes(endpointOrigin) && !uploaded}))}
          selectedOption={connection ? {value: connection.id, label: connection.name} : null}
          onChange={({detail}) => {setConnectionId(detail.selectedOption.value!); authenticationChanged('existing', detail.selectedOption.value!);}}/>
      </FormField> : null}
      {authSaved && <Alert type="success">Authentication saved</Alert>}
      {authMethod === 'existing' && connection?.requires_schema && connection.callback_url && <Alert type="info" header="Check the OAuth callback">
        If your provider already allows this exact URL, continue without changing it. Otherwise add it to the provider’s allowed callbacks.
        <Box><span style={{wordBreak: 'break-all'}}>{connection.callback_url}</span></Box>
      </Alert>}
      {authenticationReady && <Box>Authentication is selected for this connection. Choose Next to set up its tools.</Box>}
      </SpaceBetween></Container>},
      {title: 'Tools and workspaces', description: 'Load the server’s tools and choose who can use this connection.', content: <Container><SpaceBetween size="m">
      <KeyValuePairs columns={2} items={[{label: 'Connection', value: name}, {label: 'Authentication', value: connection?.name || 'Not selected'}]}/>
      {connection?.requires_schema
        ? <McpToolDefinitions value={toolSchema} onChange={setToolSchema} validation={definitions} packageName={uploaded?.name}/>
        : <Alert type="info" header="Tools will be discovered automatically">
          Studio will connect using the selected authentication and load the server’s tools for your review.
        </Alert>}
      <FormField label="Visible workspaces"><Multiselect ariaLabel="Onboarding workspaces"
        options={options?.workspaces.map(w => ({value: w, label: w}))} selectedOptions={workspaces.map(w => ({value: w, label: w}))}
        onChange={({detail}) => setWorkspaces(detail.selectedOptions.map(o => o.value!))}/></FormField>
      {!workspaces.length && <Box color="text-status-info">Select at least one workspace to continue.</Box>}
      </SpaceBetween></Container>},
      {title: 'Review and publish', description: 'Select the tools to publish for the chosen workspaces.', content: recordPanel
        || <StatusIndicator type="loading">Preparing the connection…</StatusIndicator>},
      ]}/>}
    {!form && <Table header={<Header variant="h2">Registered MCP connections</Header>} items={items} wrapLines selectionType="single" trackBy="id" selectedItems={items.filter(i => i.id === selectedId)}
      onSelectionChange={({detail}) => {if (!busy && !unresolved) {setSelectedId(detail.selectedItems[0]?.id || ''); setRecord(undefined); setSelectedTools([]); setForm(false);}}}
      columnDefinitions={[
        {id: 'name', header: 'Connection', width: 220, cell: i => i.name},
        {id: 'endpoint', header: 'Endpoint', width: 450, cell: i => <span style={{wordBreak: 'break-all'}}>{i.endpoint}</span>},
        {id: 'status', header: 'Status', width: 180, cell: i => <StatusIndicator type={i.phase === 'READY' ? 'success' : i.phase === 'REVIEW' ? 'info' : stopped(i.phase) ? 'warning' : 'loading'}>{phaseLabel(i.phase)}</StatusIndicator>},
      ]} empty={<Box>No existing MCP endpoints have been onboarded.</Box>}/>}
    {!form && recordPanel}
    {!form && options?.python_packages && <ExpandableSection headerText="Manage uploaded MCP servers">
      <McpDeployments api={api} identityKey={identityKey} refreshKey={(uploaded?.id || '') + ':' + (record?.phase || '')}/>
    </ExpandableSection>}
    {!form && <ExpandableSection headerText="Manage saved authentication">
      <AuthenticationConnections api={api} identityKey={identityKey} onChanged={load} runtimeIamSetup={options?.runtime_iam_setup} oauthSetup={options?.oauth_setup} oauthGrants={options?.oauth_grants}/>
    </ExpandableSection>}
  </SpaceBetween>;
}

export default function McpConnections(props: {api: Api; identityKey: string}) {
  return <Onboarding {...props}/>;
}
