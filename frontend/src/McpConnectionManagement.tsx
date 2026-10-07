import React, {useEffect, useRef, useState} from 'react';
import {Alert, Box, Button, Container, FormField, Header, Input, Modal, Multiselect, Select, SpaceBetween, Table, Textarea} from '@cloudscape-design/components';
import type {Api} from './JourneyBuilder';
import McpAuthentication from './McpAuthentication';
import McpIamAuthentication from './McpIamAuthentication';
import McpOAuthAuthentication, {oauthMethods} from './McpOAuthAuthentication';

type Auth = {id: string; name: string; allowed_origins: string[]; auth_type: string; header: string; prefix: string;
  revision: number; can_edit: boolean; can_delete: boolean; reason: string; references: {name: string}[];
  operation?: {token: string; owner: string}; owns_provider?: boolean; callback_url?: string; grant_type?: string};
type Status = {phase: string; token?: string; can_continue?: boolean; failure_code?: string; stage?: string};
type AuthIntent = {id: string; kind: 'edit' | 'delete'; token: string; rotate: boolean; payload: {[key: string]: unknown}};

export function AuthenticationConnections({api, identityKey, onChanged, runtimeIamSetup, oauthSetup, oauthGrants}: {api: Api; identityKey: string; onChanged: () => Promise<void>; runtimeIamSetup?: boolean; oauthSetup?: boolean; oauthGrants?: string[]}) {
  const key = 'mcp-auth-change:' + identityKey;
  const [items, setItems] = useState<Auth[]>([]), [selected, setSelected] = useState<Auth>();
  const [mode, setMode] = useState<'add' | 'edit' | 'delete'>(), [name, setName] = useState(''), [endpoint, setEndpoint] = useState('');
  const [header, setHeader] = useState('Authorization'), [prefix, setPrefix] = useState('Bearer'), [secret, setSecret] = useState('');
  const [confirmation, setConfirmation] = useState(''), [error, setError] = useState(''), [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false), [pending, setPending] = useState<AuthIntent>(), [status, setStatus] = useState<Status>();
  const [missing, setMissing] = useState(false);
  const [authMethod, setAuthMethod] = useState('pat');
  const lock = useRef(false);
  async function load() {
    const result = await api<{items: Auth[]}>('/admin/mcp/auth-connections');
    setItems(result.items);
    setSelected(previous => result.items.find(i => i.id === previous?.id));
  }
  useEffect(() => {
    void load().catch(e => setError(e.message));
    try {const raw = sessionStorage.getItem(key); if (raw) setPending(JSON.parse(raw));}
    catch {setError('Could not restore the retained credential operation.');}
  }, []);
  async function act(fn: () => Promise<void>) {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError('');
    try {await fn();} catch {setError('The authentication operation did not complete. Check its saved status before continuing.');}
    finally {lock.current = false; setBusy(false); setSecret('');}
  }
  async function accept(value: Status) {
    setStatus(value);
    if (['READY', 'DELETED'].includes(value.phase)) {
      setPending(undefined); setMode(undefined); sessionStorage.removeItem(key);
      setMessage(value.phase === 'DELETED' ? 'Authentication connection deleted.' : 'Authentication connection updated.');
      await load(); await onChanged();
    }
  }
  function open(value: 'add' | 'edit' | 'delete') {
    setMode(value); setError(''); setMessage(''); setSecret(''); setConfirmation('');
    setName(value === 'add' ? '' : selected!.name);
    setEndpoint(value === 'add' ? '' : selected!.allowed_origins[0]);
    setHeader(value === 'add' ? 'Authorization' : selected!.header); setPrefix(value === 'add' ? 'Bearer' : selected!.prefix);
  }
  async function submit() {
    await act(async () => {
      const token = crypto.randomUUID();
      const payload = mode === 'edit' ? {name, endpoint, header, prefix, expected_revision: selected!.revision, idempotency_key: token}
        : {confirm_name: confirmation, expected_revision: selected!.revision, idempotency_key: token};
      const intent: AuthIntent = {id: selected!.id, kind: mode as 'edit' | 'delete', token, rotate: !!secret, payload};
      sessionStorage.setItem(key, JSON.stringify(intent)); setPending(intent); setMissing(false);
      try {
        await accept(await api<Status>('/admin/mcp/auth-connections/' + intent.id + '/' + intent.kind,
          {...payload, ...(mode === 'edit' ? {secret} : {})}));
      } catch (e) {
        if ([409, 422, 429].includes((e as Error & {status?: number}).status || 0)) {
          sessionStorage.removeItem(key); setPending(undefined); await load();
        }
        throw e;
      }
    });
  }
  return <Container header={<Header variant="h2" description="Manage saved credentials. To set up a server and its authentication together, use Add MCP connection."
    actions={<SpaceBetween direction="horizontal" size="xs">
      <Button disabled={busy} onClick={() => void act(load)}>Refresh credentials</Button>
      <Button disabled={busy || !!pending} onClick={() => open('add')}>Add authentication connection</Button>
    </SpaceBetween>}>Authentication connections</Header>}><SpaceBetween size="m">
    {error && <Alert type="error">{error}</Alert>}
    {message && <Alert type="success">{message}</Alert>}
    <Table items={items} wrapLines trackBy="id" selectionType="single" selectedItems={selected ? [selected] : []}
      onSelectionChange={({detail}) => {setSelected(detail.selectedItems[0]); setMode(undefined); setMessage('');}}
      columnDefinitions={[
        {id: 'name', header: 'Name', cell: i => i.name},
        {id: 'type', header: 'Authentication', cell: i => i.auth_type === 'OAUTH' ? (i.grant_type === 'CLIENT_CREDENTIALS' ? 'OAuth 2LO' : 'OAuth 3LO') : i.auth_type},
        {id: 'origin', header: 'Approved endpoint host', cell: i => <span style={{wordBreak: 'break-all'}}>{i.allowed_origins.join(', ')}</span>},
        {id: 'usage', header: 'Used by', cell: i => i.references.map(r => r.name).join(', ') || (i.reason && !i.can_edit && !i.can_delete ? 'Deployment / operation' : 'Unused')},
      ]} empty={<Box>No saved authentication connections.</Box>}/>
    {selected && <SpaceBetween size="s">
      {selected.reason && <Alert type="info">{selected.reason}</Alert>}
      {selected.callback_url && selected.grant_type !== 'CLIENT_CREDENTIALS' && <Alert type="info" header="OAuth callback URL">
        Check that this URL is allowed by your provider. If it is already registered, no provider change is needed:
        <Box>{selected.callback_url}</Box>
      </Alert>}
      <SpaceBetween direction="horizontal" size="s">
        <Button disabled={busy || !!pending || !selected.can_edit} onClick={() => open('edit')}>Edit authentication</Button>
        <Button disabled={busy || !!pending || !selected.can_delete} onClick={() => open('delete')}>Delete authentication</Button>
        {selected.operation && !pending && <Button onClick={() => {
          setPending({id: selected.id, kind: 'edit', token: selected.operation!.token, rotate: true, payload: {}});
        }}>Restore authentication operation</Button>}
      </SpaceBetween>
    </SpaceBetween>}
    {pending && <Alert type="warning" header="Retained authentication operation"><SpaceBetween size="s">
      <Box>{status?.phase || 'Check the recorded result before another change.'} {status?.failure_code}</Box>
      {pending.rotate && <FormField label="Replacement API key or PAT" description="Reenter only if an explicit retry is needed.">
        <Input type="password" autoComplete="off" ariaLabel="Replacement API key or PAT" value={secret} onChange={({detail}) => setSecret(detail.value)}/>
      </FormField>}
      <Button disabled={busy} onClick={() => void act(async () => {
        try {setMissing(false); await accept(await api<Status>('/admin/mcp/auth-connections/' + pending.id + '/operations/' + pending.token));}
        catch (e) {if ((e as Error & {status?: number}).status === 404) setMissing(true); else throw e;}
      })}>Check saved authentication change</Button>
      {status?.can_continue && <Button disabled={busy || (status.stage === 'rotate' && !secret)} onClick={() => void act(async () => {
        await accept(await api<Status>('/admin/mcp/auth-connections/' + pending.id + '/operations/' + pending.token + '/continue',
          {secret, retry: status.phase === 'NEEDS_RECONCILIATION'}));
      })}>Continue authentication change</Button>}
      {missing && <Button disabled={busy || (pending.rotate && !secret) || !pending.payload.idempotency_key} onClick={() => void act(async () => {
        await accept(await api<Status>('/admin/mcp/auth-connections/' + pending.id + '/' + pending.kind,
          {...pending.payload, ...(pending.kind === 'edit' ? {secret} : {})}));
      })}>Retry saved authentication change</Button>}
    </SpaceBetween></Alert>}
    <Modal visible={!!mode && !pending} onDismiss={() => {if (!busy) {setMode(undefined); setSecret('');}}}
      header={mode === 'add' ? 'Add authentication connection' : mode === 'edit' ? 'Edit authentication connection' : 'Delete authentication connection'}
      footer={mode !== 'add' && <SpaceBetween direction="horizontal" size="s">
        <Button disabled={busy} onClick={() => setMode(undefined)}>Cancel</Button>
        <Button variant="primary" loading={busy} disabled={mode === 'delete' ? confirmation !== selected?.name : !name.trim() || !endpoint.trim()}
          onClick={() => void submit()}>{mode === 'delete' ? 'Confirm authentication deletion' : 'Save authentication changes'}</Button>
      </SpaceBetween>}>
      <SpaceBetween size="m">
        {mode === 'delete' ? <>
          <Box>{selected?.auth_type === 'OAUTH' && !selected.owns_provider
            ? 'Remove the saved OAuth reference. The provider, its secret and consent remain managed in AgentCore Identity.'
            : selected?.auth_type === 'GATEWAY_IAM_ROLE'
            ? 'Remove the saved IAM connection. The Runtime and its AWS IAM permissions remain unchanged.'
            : 'Remove this saved connection and its Gateway credential provider. Its stored secret will enter a seven-day recovery period.'}</Box>
          <FormField label={'Type ' + selected?.name + ' to confirm'}><Input ariaLabel="Confirm authentication name" value={confirmation} onChange={({detail}) => setConfirmation(detail.value)}/></FormField>
        </> : <>
          <FormField label="Authentication name"><Input ariaLabel="Authentication name" value={name} onChange={({detail}) => setName(detail.value)}/></FormField>
          <FormField label="Endpoint URL" description="The credential is restricted to this HTTPS host."><Input ariaLabel="Authentication endpoint URL" value={endpoint} onChange={({detail}) => setEndpoint(detail.value)}/></FormField>
          {mode === 'add' && <FormField label="Authentication method"><Select ariaLabel="New authentication method"
            options={[{value: 'pat', label: 'API key / PAT'}, {value: 'iam', label: 'AWS IAM / AgentCore Runtime', disabled: !runtimeIamSetup},
              ...oauthMethods.map(m => ({value: m.value, label: m.label,
                disabled: !oauthSetup || (!!oauthGrants && !oauthGrants.includes(m.grant))}))]}
            selectedOption={{value: authMethod, label: oauthMethods.find(m => m.value === authMethod)?.label
              || (authMethod === 'iam' ? 'AWS IAM / AgentCore Runtime' : 'API key / PAT')}}
            onChange={({detail}) => setAuthMethod(detail.selectedOption.value!)}/></FormField>}
          {mode === 'add' && authMethod.startsWith('oauth-') ? <McpOAuthAuthentication key={authMethod} api={api} identityKey={identityKey + ':standalone'}
            name={name} endpoint={endpoint} grant={authMethod === 'oauth-2lo' ? 'CLIENT_CREDENTIALS' : 'AUTHORIZATION_CODE'} allowedGrants={oauthGrants} onSaved={async () => {
            setMode(undefined); setMessage('Authentication connection added.'); await load(); await onChanged();
          }}/> : mode === 'add' && authMethod === 'iam' ? <McpIamAuthentication api={api} identityKey={identityKey + ':standalone'} name={name} endpoint={endpoint} exactName onSaved={async () => {
            setMode(undefined); setMessage('Authentication connection added.'); await load(); await onChanged();
          }}/> : mode === 'add' ? <McpAuthentication api={api} identityKey={identityKey + ':standalone'} name={name} endpoint={endpoint} exactName onSaved={async () => {
            setMode(undefined); setMessage('Authentication connection added.'); await load(); await onChanged();
          }}/> : <>
            <FormField label="Replacement API key or PAT" description="Leave empty to keep the existing value. Stored credentials are never displayed.">
              <Input type="password" autoComplete="off" ariaLabel="Replacement API key or PAT" value={secret} onChange={({detail}) => setSecret(detail.value)}/>
            </FormField>
            <FormField label="Header name"><Input ariaLabel="Edit credential header" value={header} onChange={({detail}) => setHeader(detail.value)}/></FormField>
            <FormField label="Header prefix"><Input ariaLabel="Edit credential prefix" value={prefix} onChange={({detail}) => setPrefix(detail.value)}/></FormField>
          </>}
        </>}
      </SpaceBetween>
    </Modal>
  </SpaceBetween></Container>;
}

type ManagedRecord = {id: string; name: string; description: string; endpoint: string; connection_id: string; workspaces: string[]; tool_schema?: object[];
  phase: string; revision?: number; legacy?: boolean; can_edit: boolean; can_delete: boolean; reason: string; blockers: {name: string}[]};
export function RegistrationManagement({api, identityKey, record, options, onChanged}: {
  api: Api; identityKey: string; record: {id: string; phase: string; legacy?: boolean};
  options?: {connections: {id: string; name: string; requires_schema?: boolean}[]; workspaces: string[]}; onChanged: () => Promise<void>;
}) {
  const path = '/admin/mcp/management/' + (record.legacy ? 'servers/' : 'onboarding/') + record.id;
  const key = 'mcp-registration-change:' + identityKey + ':' + record.id;
  const [info, setInfo] = useState<ManagedRecord>(), [mode, setMode] = useState<'edit' | 'delete'>();
  const [form, setForm] = useState<ManagedRecord>(), [confirmation, setConfirmation] = useState('');
  const [toolSchema, setToolSchema] = useState('');
  const requiresSchema = !!options?.connections.find(c => c.id === form?.connection_id)?.requires_schema;
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [pending, setPending] = useState<{kind: string; payload: object}>();
  const lock = useRef(false);
  useEffect(() => {
    void api<ManagedRecord>(path).then(setInfo).catch(e => setError(e.message));
    try {const raw = sessionStorage.getItem(key); if (raw) setPending(JSON.parse(raw));}
    catch {setError('Could not restore the retained connection change.');}
  }, [record.id, record.phase]);
  async function send(kind: string, payload: object) {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError('');
    try {
      sessionStorage.setItem(key, JSON.stringify({kind, payload})); setPending({kind, payload});
      await api(path + '/' + kind, payload);
      sessionStorage.removeItem(key); setPending(undefined); setMode(undefined);
      await onChanged();
    } catch (e) {
      if ([409, 422].includes((e as Error & {status?: number}).status || 0)) {
        sessionStorage.removeItem(key); setPending(undefined);
      }
      setError((e as Error).message);
    } finally {lock.current = false; setBusy(false);}
  }
  function saveEdit() {
    let schema: {tools: object[]} | undefined;
    if (requiresSchema) {
      try {
        schema = JSON.parse(toolSchema);
        if (!Array.isArray(schema?.tools) || !schema.tools.length) throw new Error();
      } catch {setError('Paste valid tools/list JSON containing a nonempty tools array.'); return;}
    }
    void send('edit', {name: form!.name, description: form!.description, endpoint: form!.endpoint,
      connection_id: form!.connection_id, workspaces: form!.workspaces,
      ...(schema ? {tool_schema: schema.tools} : {}), expected_revision: info!.revision || 1,
      idempotency_key: crypto.randomUUID()});
  }
  return <SpaceBetween size="s">
    {error && <Alert type="error">{error}</Alert>}
    {info?.reason && <Alert type="info">{info.reason} {info.blockers.map(b => b.name).join(', ')}</Alert>}
    <SpaceBetween direction="horizontal" size="s">
      <Button disabled={busy || !!pending || !info?.can_edit} onClick={() => {
        setForm(info); setToolSchema(info?.tool_schema ? JSON.stringify({tools: info.tool_schema}, null, 2) : ''); setMode('edit');
      }}>Edit connection</Button>
      <Button disabled={busy || !!pending || !info?.can_delete} onClick={() => {setConfirmation(''); setMode('delete');}}>Delete connection</Button>
    </SpaceBetween>
    {pending && <Alert type="warning">A change request is retained. Replaying it checks the existing request before taking action.
      <Button disabled={busy} onClick={() => void send(pending.kind, pending.payload)}>Check retained connection change</Button>
    </Alert>}
    <Modal visible={!!mode} header={mode === 'edit' ? 'Edit MCP connection' : 'Delete MCP connection'} size="large"
      onDismiss={() => {if (!busy && !pending) setMode(undefined);}}
      footer={<SpaceBetween direction="horizontal" size="s">
        <Button disabled={busy || !!pending} onClick={() => setMode(undefined)}>Cancel</Button>
        <Button variant="primary" loading={busy} disabled={!!pending || (mode === 'delete' ? confirmation !== info?.name : !form?.name.trim() || !form.workspaces.length)}
          onClick={() => mode === 'edit' ? saveEdit() : void send('delete',
            {confirm_name: confirmation, expected_revision: info!.revision || 1, idempotency_key: crypto.randomUUID()})}>
          {mode === 'edit' ? (requiresSchema ? 'Save and review' : 'Save and rediscover') : 'Confirm connection deletion'}
        </Button>
      </SpaceBetween>}>
      <SpaceBetween size="m">
        {mode === 'delete' ? <>
          <Box>Remove this connection from Studio, AI Registry and AgentCore Gateway. The remote MCP server and saved authentication remain available.</Box>
          <FormField label={'Type ' + info?.name + ' to confirm'}><Input ariaLabel="Confirm connection name" value={confirmation} onChange={({detail}) => setConfirmation(detail.value)}/></FormField>
        </> : form && <>
          <Alert type="info">Saving withdraws this registration and reconnects the endpoint. Review and publish its updated tools before using it.</Alert>
          <FormField label="Connection name"><Input ariaLabel="Edit connection name" value={form.name} onChange={({detail}) => setForm({...form, name: detail.value})}/></FormField>
          <FormField label="Description"><Textarea ariaLabel="Edit connection description" value={form.description} onChange={({detail}) => setForm({...form, description: detail.value})}/></FormField>
          <FormField label="MCP endpoint URL"><Input ariaLabel="Edit MCP endpoint URL" value={form.endpoint} onChange={({detail}) => setForm({...form, endpoint: detail.value})}/></FormField>
          <FormField label="Authentication connection"><Select ariaLabel="Edit authentication connection"
            selectedOption={options?.connections.filter(c => c.id === form.connection_id).map(c => ({value: c.id, label: c.name}))[0] || null}
            options={options?.connections.map(c => ({value: c.id, label: c.name}))}
            onChange={({detail}) => setForm({...form, connection_id: detail.selectedOption.value!})}/></FormField>
          {requiresSchema && <FormField label="MCP tool schema JSON" description="Review the server's current tools/list schema before saving.">
            <Textarea ariaLabel="Edit MCP tool schema JSON" value={toolSchema} rows={10} onChange={({detail}) => setToolSchema(detail.value)}/>
          </FormField>}
          <FormField label="Visible workspaces"><Multiselect ariaLabel="Edit connection workspaces"
            selectedOptions={form.workspaces.map(w => ({value: w, label: w}))} options={options?.workspaces.map(w => ({value: w, label: w}))}
            onChange={({detail}) => setForm({...form, workspaces: detail.selectedOptions.map(o => o.value!)})}/></FormField>
        </>}
      </SpaceBetween>
    </Modal>
  </SpaceBetween>;
}
