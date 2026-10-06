import React, {useEffect, useRef, useState} from 'react';
import {Alert, Box, Button, FormField, Input, Select, SpaceBetween, StatusIndicator} from '@cloudscape-design/components';
import type {Api} from './JourneyBuilder';

type Settings = {snowflake_account: string; snowflake_role: string; warehouse: string};
type Intent = Settings & {name: string; filename: string; source_digest: string; idempotency_key: string; id?: string};
export type PythonMcp = {id: string; job_id: string; name: string; filename: string; source_digest: string; phase: string;
  endpoint: string; tools?: {name: string; description: string; inputSchema: {[key: string]: unknown}}[];
  upload_type?: 'package'; connection_mode?: 'PACKAGE' | 'IAM' | 'SNOWFLAKE_OAUTH'; stage?: string;
  bearer_endpoint?: string; deleting?: boolean;
  failure_code?: string; retry_available?: boolean};
const active = (record?: PythonMcp) => !!record && !['READY', 'FAILED', 'NEEDS_RECONCILIATION'].includes(record.phase);
const fields = [['snowflake_account', 'Snowflake account'], ['snowflake_role', 'Snowflake reader role'], ['warehouse', 'Snowflake warehouse']] as const;

export default function McpPythonUpload({api, identityKey, name, bundle, onReady}: {
  api: Api; identityKey: string; name: string; bundle: string; onReady: (record: PythonMcp) => void;
}) {
  const key = 'mcp-python-upload:' + identityKey;
  const [settings, setSettings] = useState<Settings>({snowflake_account: '', snowflake_role: '', warehouse: ''});
  const [file, setFile] = useState<{name: string; source: string; digest: string}>();
  const [intent, setIntent] = useState<Intent>(), [record, setRecord] = useState<PythonMcp>(), [items, setItems] = useState<PythonMcp[]>([]);
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [storageError, setStorageError] = useState('');
  const [missing, setMissing] = useState(false);
  const lock = useRef(false), fileRead = useRef(0);
  useEffect(() => {
    void api<{items: PythonMcp[]}>('/admin/mcp/python').then(v => setItems(v.items.filter(i => !i.upload_type))).catch(() => setError('Could not load Python deployments.'));
    try {
      const raw = sessionStorage.getItem(key);
      if (raw) {
        const saved = JSON.parse(raw) as Intent;
        if (!saved.idempotency_key || !saved.source_digest) throw new Error();
        setIntent(saved);
        if (saved.id) void api<PythonMcp>('/admin/mcp/python/' + saved.id).then(setRecord).catch(() => setError('Check the saved Python upload status.'));
      }
    } catch {setStorageError('Session storage is required to retain the Python upload request.');}
  }, [key]);
  useEffect(() => {
    if (!active(record) || busy) return;
    let live = true, timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const next = await api<PythonMcp>('/admin/mcp/python/' + record!.id);
        if (!live) return;
        setRecord(next);
        if (active(next)) timer = setTimeout(poll, 3000);
      } catch {if (live) setError('Could not refresh Python deployment status. Check its saved status.');}
    }
    void poll();
    return () => {live = false; clearTimeout(timer);};
  }, [record?.id, record?.phase, busy]);
  async function chooseFile(selected?: File) {
    const generation = ++fileRead.current;
    setFile(undefined); setError('');
    if (!selected) return;
    try {
      if (!selected.name.endsWith('.py') || selected.size > 65536) throw new Error();
      const bytes = await selected.arrayBuffer();
      const source = new TextDecoder('utf-8', {fatal: true}).decode(bytes);
      const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(source));
      if (generation === fileRead.current) setFile({name: selected.name, source,
        digest: Array.from(new Uint8Array(hash), b => b.toString(16).padStart(2, '0')).join('')});
    } catch {setError('Choose a UTF-8 Python file of at most 64 KiB.');}
  }
  async function act(operation: () => Promise<void>) {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError('');
    try {await operation();}
    catch {setError('Python deployment did not complete. Check the retained upload status before continuing.');}
    finally {lock.current = false; setBusy(false);}
  }
  async function submit(saved: Intent) {
    if (!file || file.digest !== saved.source_digest || file.name !== saved.filename) {
      throw new Error('Choose the same Python file before retrying.');
    }
    sessionStorage.setItem(key, JSON.stringify(saved)); setIntent(saved); setMissing(false);
    try {
      const {source_digest: _, id: __, ...payload} = saved;
      const result = await api<PythonMcp>('/admin/mcp/python', {...payload, source: file.source});
      const accepted = {...saved, id: result.id};
      sessionStorage.setItem(key, JSON.stringify(accepted)); setIntent(accepted); setRecord(result); setFile(undefined);
    } catch (e) {
      if ([413, 422, 429].includes((e as Error & {status?: number}).status || 0)) {
        sessionStorage.removeItem(key); setIntent(undefined);
      }
      throw e;
    }
  }
  return <SpaceBetween size="m">
    <Box>Installed bundle: {bundle}. Upload a FastMCP entry point that serves Streamable HTTP on port 8000.
      Keep credentials out of the file. Configure Snowflake OAuth after deployment.</Box>
    {items.length > 0 && <FormField label="Saved Python deployments">
      <Select ariaLabel="Saved Python deployments" disabled={busy || !!intent}
        options={items.map(i => ({value: i.id, label: i.name, description: i.phase}))}
        selectedOption={record ? {value: record.id, label: record.name} : null}
        onChange={({detail}) => setRecord(items.find(i => i.id === detail.selectedOption.value))}/>
    </FormField>}
    {(error || storageError) && <Alert type="error">{storageError || error}</Alert>}
    {(!record && !intent || missing) && <FormField label="Python MCP file">
      <input type="file" accept=".py" aria-label="Python MCP file" disabled={busy}
        onChange={event => void chooseFile(event.target.files?.[0])}/>
    </FormField>}
    {!record && !intent && <>
      {fields.map(([field, label]) => <FormField key={field} label={label}
        description={field === 'snowflake_account' ? 'Use the organization-account identifier, without a URL.'
          : field === 'snowflake_role' ? 'Use a dedicated role with only the data access this MCP needs.' : undefined}>
        <Input ariaLabel={label} value={settings[field]} disabled={busy} onChange={({detail}) => setSettings({...settings, [field]: detail.value})}/>
      </FormField>)}
      <Button variant="primary" loading={busy} disabled={!!storageError || !file || name.trim().length < 2 || fields.some(([f]) => !settings[f].trim())}
        onClick={() => void act(() => submit({...settings, name: name.trim(), filename: file!.name,
          source_digest: file!.digest, idempotency_key: crypto.randomUUID()}))}>Deploy Python MCP</Button>
    </>}
    {intent && <Box>Retained upload: {intent.filename}. Source content is not saved in browser storage.</Box>}
    {(intent || record) && <Button loading={busy} onClick={() => void act(async () => {
      try {
        const result = await api<PythonMcp>(record ? '/admin/mcp/python/' + record.id
          : '/admin/mcp/python-requests/' + intent!.idempotency_key);
        setRecord(result); setMissing(false);
      } catch (e) {
        if (!record && (e as Error & {status?: number}).status === 404) setMissing(true);
        else throw e;
      }
    })}>Check Python upload status</Button>}
    {missing && intent && <Button disabled={busy || !file || file.digest !== intent.source_digest || file.name !== intent.filename}
      onClick={() => void act(() => submit(intent))}>Retry retained Python upload</Button>}
    {record && <>
      <StatusIndicator type={record.phase === 'READY' ? 'success' : active(record) ? 'loading' : 'warning'}>{record.phase}</StatusIndicator>
      {record.failure_code && <Box>Service status: {record.failure_code}</Box>}
      {record.phase === 'READY' ? <Button variant="primary" disabled={busy} onClick={() => {
        try {sessionStorage.removeItem(key); setIntent(undefined); onReady(record);}
        catch {setStorageError('Could not update the retained upload status.');}
      }}>Use this Python MCP</Button> : !active(record) && <SpaceBetween size="s">
        <SpaceBetween direction="horizontal" size="s">
          <Button disabled={busy} onClick={() => void act(async () => {
            setRecord(await api<PythonMcp>('/admin/mcp/python/' + record.id + '/reconcile', {}));
          })}>Reconcile Python deployment</Button>
          {record.retry_available && <Button disabled={busy} onClick={() => void act(async () => {
            setRecord(await api<PythonMcp>('/admin/mcp/python/' + record.id + '/retry', {job_id: record.job_id}));
          })}>Retry Python deployment step</Button>}
          <Button disabled={busy} onClick={() => {
            if (lock.current) return;
            try {sessionStorage.removeItem(key);}
            catch {setStorageError('Could not clear the retained Python upload request.'); return;}
            ++fileRead.current;
            setItems(previous => [record, ...previous.filter(item => item.id !== record.id)]);
            setIntent(undefined); setRecord(undefined); setFile(undefined); setMissing(false);
            setError(''); setStorageError('');
          }}>Choose another Python MCP</Button>
        </SpaceBetween>
        <Box>Choose another Python MCP to start a new upload or select a saved deployment. This deployment and its history remain saved.</Box>
      </SpaceBetween>}
    </>}
  </SpaceBetween>;
}
