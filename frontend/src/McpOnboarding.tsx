import React, {useEffect, useRef, useState} from 'react';
import {
  Alert, Box, Button, Checkbox, Container, ExpandableSection, FormField, Header,
  Input, KeyValuePairs, Multiselect, Select, SpaceBetween, StatusIndicator, Table,
  Textarea,
} from '@cloudscape-design/components';
import type {Api} from './JourneyBuilder';
import McpAuthentication from './McpAuthentication';
import {AuthenticationConnections, RegistrationManagement} from './McpConnectionManagement';

type Connection = {id: string; name: string; auth_type: string; allowed_origins: string[]};
type Options = {enabled: boolean; reason?: string; workspaces: string[]; connections: Connection[]; credential_setup?: boolean};
type Tool = {name: string; description: string; inputSchema: {[key: string]: unknown}};
type Record = {
  id: string; job_id?: string; name: string; description: string; endpoint: string; connection_id: string;
  workspaces: string[]; phase: string; error?: string; tools?: Tool[]; selected_tools?: string[];
  discovery_digest?: string; gateway_target_id?: string; registry_record_arn?: string;
  retry_available?: boolean;
  legacy?: boolean; revision?: number;
};
type Payload = {name: string; description: string; endpoint: string; connection_id: string; workspaces: string[]; idempotency_key: string};
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
  const [intent, setIntent] = useState<Intent>(), [storageError, setStorageError] = useState('');
  const [notRecorded, setNotRecorded] = useState(false);
  const lock = useRef(false);
  const connection = options?.connections.find(c => c.id === connectionId);
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
    setConnectionId(options?.connections[0]?.id || '');
    setAuthMethod(options?.credential_setup ? 'new' : 'existing'); setAuthSaved(false);
    setWorkspaces(options?.workspaces.includes('research') ? ['research'] : []);
    setForm(true); setError('');
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
      const pending = {payload};
      sessionStorage.setItem(storageKey, JSON.stringify(pending)); setIntent(pending);
      try {
        const result = await api<{id: string}>('/admin/mcp/onboarding', payload);
        const saved = {payload, id: result.id};
        sessionStorage.setItem(storageKey, JSON.stringify(saved)); setIntent(saved);
        setForm(false); setSelectedId(result.id);
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
      show(value); setSelectedId(value.id); setForm(false);
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
  return <SpaceBetween size="l">
    <Container header={<Header variant="h2" description="Connect an MCP server, review its discovered tools, and publish it to the AI Registry through AgentCore Gateway."
      actions={<Button variant="primary" disabled={!options?.enabled || busy || unresolved || !!storageError} onClick={start}>Create MCP connection</Button>}>MCP connections</Header>}>
      <Box>Use a Streamable HTTP server from any provider. Set up authentication here, then define your agent’s task in its instructions and skills.</Box>
    </Container>
    {options && !options.enabled && <Alert type="info" header="Onboarding is not configured">{options.reason}</Alert>}
    {(error || storageError) && <Alert type="error">{error || storageError}</Alert>}
    <AuthenticationConnections api={api} identityKey={identityKey} onChanged={load}/>
    {intent && !record && <Alert type="info" header="Saved onboarding request">
      The request is retained. Check its status before submitting another connection.
      <Box margin={{top: 's'}}><Button disabled={busy} onClick={() => void check()}>Check status</Button></Box>
      {notRecorded && <Box margin={{top: 's'}}><SpaceBetween size="s">
        <Box>No request was recorded. You can explicitly retry the same retained request.</Box>
        <Button disabled={busy} onClick={() => void act(async () => {
          const result = await api<{id: string}>('/admin/mcp/onboarding', intent.payload);
          const saved = {...intent, id: result.id};
          sessionStorage.setItem(storageKey, JSON.stringify(saved)); setIntent(saved);
          setNotRecorded(false); setForm(false); setSelectedId(result.id);
        })}>Retry retained request</Button>
      </SpaceBetween></Box>}
    </Alert>}
    {form && <Container header={<Header variant="h2">Connect an MCP endpoint</Header>}><SpaceBetween size="m">
      <FormField label="Connection name"><Input ariaLabel="Connection name" value={name} onChange={({detail}) => setName(detail.value)}/></FormField>
      <FormField label="Description"><Textarea ariaLabel="Connection description" value={description} onChange={({detail}) => setDescription(detail.value)}/></FormField>
      <FormField label="MCP endpoint URL" description="The HTTPS Streamable HTTP endpoint supplied by your MCP server.">
        <Input ariaLabel="MCP endpoint URL" value={endpoint} placeholder="https://your-server.example.com/mcp" onChange={({detail}) => setEndpoint(detail.value)}/>
      </FormField>
      <FormField label="Authentication method">
        <Select ariaLabel="Authentication method" options={[
          {value: 'new', label: 'API key / PAT', disabled: !options?.credential_setup},
          {value: 'existing', label: 'Existing connection'},
        ]} selectedOption={{value: authMethod, label: authMethod === 'new' ? 'API key / PAT' : 'Existing connection'}}
          onChange={({detail}) => {setAuthMethod(detail.selectedOption.value!); setAuthSaved(false);}}/>
      </FormField>
      {authMethod === 'new' ? <McpAuthentication api={api} identityKey={identityKey} name={name} endpoint={endpoint} onSaved={async id => {
        const updated = await api<Options>('/admin/mcp/onboarding-options');
        setOptions(updated); setConnectionId(id); setAuthMethod('existing'); setAuthSaved(true);
      }}/> : <FormField label="Authentication connection" description="Reuse saved API key, OAuth client credentials, or IAM authentication. Credentials are never included in the agent prompt.">
        <Select ariaLabel="Authentication connection" options={options?.connections.map(c => ({value: c.id, label: c.name, description: c.auth_type}))}
          selectedOption={connection ? {value: connection.id, label: connection.name} : null}
          onChange={({detail}) => setConnectionId(detail.selectedOption.value!)}/>
      </FormField>}
      {authSaved && <Alert type="success">Authentication saved</Alert>}
      {authMethod === 'existing' && connection && <Box color="text-body-secondary">Approved origins: {connection.allowed_origins.join(', ')}</Box>}
      <FormField label="Visible workspaces"><Multiselect ariaLabel="Onboarding workspaces"
        options={options?.workspaces.map(w => ({value: w, label: w}))} selectedOptions={workspaces.map(w => ({value: w, label: w}))}
        onChange={({detail}) => setWorkspaces(detail.selectedOptions.map(o => o.value!))}/></FormField>
      <SpaceBetween direction="horizontal" size="s">
        <Button variant="primary" loading={busy} disabled={authMethod !== 'existing' || !connection || !!intent || !!storageError} onClick={() => void connect()}>Connect and discover</Button>
        <Button disabled={busy || !!intent} onClick={() => setForm(false)}>Cancel</Button>
      </SpaceBetween>
    </SpaceBetween></Container>}
    <Table items={items} wrapLines selectionType="single" trackBy="id" selectedItems={items.filter(i => i.id === selectedId)}
      onSelectionChange={({detail}) => {if (!busy && !unresolved) {setSelectedId(detail.selectedItems[0]?.id || ''); setRecord(undefined); setSelectedTools([]); setForm(false);}}}
      columnDefinitions={[
        {id: 'name', header: 'Connection', width: 220, cell: i => i.name},
        {id: 'endpoint', header: 'Endpoint', width: 450, cell: i => <span style={{wordBreak: 'break-all'}}>{i.endpoint}</span>},
        {id: 'status', header: 'Status', width: 180, cell: i => <StatusIndicator type={i.phase === 'READY' ? 'success' : i.phase === 'REVIEW' ? 'info' : stopped(i.phase) ? 'warning' : 'loading'}>{phaseLabel(i.phase)}</StatusIndicator>},
      ]} empty={<Box>No existing MCP endpoints have been onboarded.</Box>}/>
    {record && <Container header={<Header variant="h2" actions={<Button disabled={busy} onClick={() => void check()}>Check status</Button>}>{record.name}</Header>}><SpaceBetween size="m">
      <KeyValuePairs columns={2} items={[
        {label: 'Endpoint', value: record.endpoint}, {label: 'Status', value: phaseLabel(record.phase)},
        {label: 'Gateway target', value: record.gateway_target_id || 'Pending'},
        {label: 'Registry record', value: record.registry_record_arn || 'Pending'},
        {label: 'Workspaces', value: record.workspaces.join(', ')},
      ]}/>
      {record.phase !== 'DELETED' && <RegistrationManagement key={record.id} api={api} identityKey={identityKey} record={record} options={options}
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
        <Header variant="h3">Review discovered tools</Header>
        <Box>Select up to 20 tools to make available. Approve and publish will approve this Registry record and grant access in the selected workspaces.</Box>
        {record.tools?.map(t => <SpaceBetween size="xs" key={t.name}>
          <Checkbox checked={selectedTools.includes(t.name)} disabled={selectedTools.length >= 20 && !selectedTools.includes(t.name)} onChange={({detail}) => setSelectedTools(previous => detail.checked ? [...previous, t.name] : previous.filter(n => n !== t.name))}>{t.name}</Checkbox>
          <Box>{t.description}</Box><ExpandableSection headerText="Input schema"><pre style={{whiteSpace: 'pre-wrap'}}>{JSON.stringify(t.inputSchema, null, 2)}</pre></ExpandableSection>
        </SpaceBetween>)}
        <Button variant="primary" disabled={busy || !selectedTools.length} onClick={() => void publish()}>Approve and publish</Button>
      </>}
      {record.phase === 'READY' && <Alert type="success" header="Connection published">{!record.legacy && 'The Registry record is approved. '}Switch to Business User and select this connection when creating an agent.</Alert>}
    </SpaceBetween></Container>}
  </SpaceBetween>;
}

export default function McpConnections(props: {api: Api; identityKey: string}) {
  return <Onboarding {...props}/>;
}
