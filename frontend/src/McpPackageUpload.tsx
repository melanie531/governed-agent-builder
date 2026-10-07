import React, {useEffect, useRef, useState} from 'react';
import {Alert, Box, Button, FormField, Select, SpaceBetween, StatusIndicator} from '@cloudscape-design/components';
import type {Api} from './JourneyBuilder';
import type {PythonMcp} from './McpPythonUpload';

type Payload = {name: string; filename: string; size: number; source_digest: string; idempotency_key: string};
type Intent = {payload: Payload; id?: string};
type Upload = PythonMcp & {size?: number; part_bytes?: number; part_count?: number; received_parts?: number[]};
const hash = async (bytes: ArrayBuffer) => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)),
  b => b.toString(16).padStart(2, '0')).join('');
const deploying = (record?: Upload) => !!record && !['UPLOADING', 'READY', 'FAILED', 'NEEDS_RECONCILIATION', 'DELETED'].includes(record.phase);

export default function McpPackageUpload({api, identityKey, name, onReady, onBusyChange, onReset, existingOnly = false, initial}: {
  api: Api; identityKey: string; name: string; onReady: (record: PythonMcp) => void; onBusyChange: (busy: boolean) => void; onReset: () => void;
  existingOnly?: boolean; initial?: PythonMcp;
}) {
  const key = 'mcp-package-upload:' + identityKey;
  const [file, setFile] = useState<{value: File; digest: string}>();
  const [intent, setIntent] = useState<Intent>(), [record, setRecord] = useState<Upload | undefined>(initial), [items, setItems] = useState<Upload[]>([]);
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [storageError, setStorageError] = useState('');
  const [checked, setChecked] = useState(false), [progress, setProgress] = useState('');
  const [loaded, setLoaded] = useState(false);
  const lock = useRef(false), generation = useRef(0);
  useEffect(() => {
    void api<{items: Upload[]}>('/admin/mcp/python').then(v => {
      setItems(v.items.filter(i => i.upload_type === 'package' && (!existingOnly || i.phase === 'READY'))); setLoaded(true);
    }).catch(() => {setLoaded(true); setError('Could not load saved package deployments.');});
    if (initial || existingOnly) return;
    try {
      const raw = sessionStorage.getItem(key);
      if (raw) {
        const saved = JSON.parse(raw) as Intent;
        if (!saved.payload?.idempotency_key || !saved.payload.source_digest) throw new Error();
        setIntent(saved);
        void api<Upload>(saved.id ? '/admin/mcp/packages/' + saved.id : '/admin/mcp/package-requests/' + saved.payload.idempotency_key)
          .then(setRecord).catch(() => setError('Check the retained package status before resuming.'));
      }
    } catch {setStorageError('Session storage is required to retain package upload progress.');}
  }, [key, existingOnly]);
  useEffect(() => {onBusyChange(busy); return () => onBusyChange(false);}, [busy, onBusyChange]);
  useEffect(() => {
    if (!deploying(record) || busy) return;
    let live = true, timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const next = await api<Upload>('/admin/mcp/python/' + record!.id);
        if (!live) return;
        setRecord(next);
        if (deploying(next)) timer = setTimeout(poll, 3000);
      } catch {if (live) setError('Could not refresh deployment status. Check its saved status.');}
    }
    void poll();
    return () => {live = false; clearTimeout(timer);};
  }, [record?.id, record?.phase, busy]);
  async function choose(selected?: File) {
    const current = ++generation.current;
    setFile(undefined); setError('');
    if (!selected) return;
    try {
      if (!selected.name.endsWith('.zip') || !selected.size || selected.size > 64 * 1024 * 1024) throw new Error();
      const digest = await hash(await selected.arrayBuffer());
      if (current === generation.current) setFile({value: selected, digest});
    } catch {setError('Choose a complete ZIP package of at most 64 MiB.');}
  }
  async function act(operation: () => Promise<void>) {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError(''); setChecked(false);
    try {await operation();}
    catch (e) {setError('Package upload stopped. Check package status before resuming. ' +
      ((e as Error & {status?: number}).status ? (e as Error).message : 'The last operation may have completed.'));}
    finally {lock.current = false; setBusy(false); setProgress('');}
  }
  function retain(saved: Intent) {
    sessionStorage.setItem(key, JSON.stringify(saved)); setIntent(saved);
  }
  async function upload(saved: Intent, resume: boolean) {
    if (!file || file.digest !== saved.payload.source_digest || file.value.name !== saved.payload.filename) {
      throw new Error('Choose the same ZIP before resuming.');
    }
    retain(saved);
    let current: Upload;
    if (saved.id) current = await api<Upload>('/admin/mcp/packages/' + saved.id);
    else {
      try {current = await api<Upload>('/admin/mcp/packages', saved.payload);}
      catch (e) {
        if ([413, 422, 429].includes((e as Error & {status?: number}).status || 0)) {
          sessionStorage.removeItem(key); setIntent(undefined);
        }
        throw e;
      }
      saved = {...saved, id: current.id}; retain(saved);
    }
    setRecord(current);
    if (current.phase !== 'UPLOADING') return;
    if (!current.part_bytes || !current.part_count || current.part_bytes > 2 * 1024 * 1024) throw new Error('Invalid upload limits');
    const chunkBytes = current.part_bytes, count = current.part_count;
    for (let index = 0; index < count; index++) {
      if (current.received_parts?.includes(index)) continue;
      setProgress(`Uploading part ${index + 1} of ${count}`);
      const bytes = await file.value.slice(index * chunkBytes, (index + 1) * chunkBytes).arrayBuffer();
      const values = new Uint8Array(bytes), pieces: string[] = [];
      for (let offset = 0; offset < values.length; offset += 32768) {
        pieces.push(String.fromCharCode(...values.subarray(offset, offset + 32768)));
      }
      current = await api<Upload>(`/admin/mcp/packages/${saved.id}/parts/${index}`,
        {data: btoa(pieces.join('')), digest: await hash(bytes), retry: resume});
      setRecord(current);
    }
    setProgress('Validating package and starting deployment');
    setRecord(await api<Upload>('/admin/mcp/packages/' + saved.id + '/deploy', {}));
    setFile(undefined);
  }
  const sameFile = !!file && !!intent && file.digest === intent.payload.source_digest && file.value.name === intent.payload.filename;
  const discoveryFailed = record?.failure_code === 'MCP_DISCOVERY_FAILED'
    || record?.stage === 'schema' && record.failure_code === 'ValueError';
  return <SpaceBetween size="m">
    {!existingOnly && <Box>Upload the complete server: main.py at the ZIP root, your supporting modules, and all third-party dependencies
      built for Python 3.13 on Linux ARM64. Serve Streamable HTTP on 0.0.0.0:8000/mcp.
      Maximum: 64 MiB ZIP, 256 MiB expanded. Requirements files are not installed during upload.</Box>}
    {existingOnly && <Box>Select a ready package to continue with its endpoint and tools. This does not upload or deploy another server.</Box>}
    {existingOnly && loaded && !items.length && !error && <Alert type="info">No ready packages are available. Choose Upload MCP package (.zip) to deploy one.</Alert>}
    {items.length > 0 && !intent && <FormField label="Saved MCP package deployments">
      <Select ariaLabel="Saved MCP package deployments" disabled={busy}
        options={items.map(i => ({value: i.id, label: i.name, description: i.phase}))}
        selectedOption={record ? {value: record.id, label: record.name} : null}
        onChange={({detail}) => {onReset(); setRecord(items.find(i => i.id === detail.selectedOption.value)); setFile(undefined);}}/>
    </FormField>}
    {(error || storageError) && <Alert type="error">{storageError || error}</Alert>}
    {!existingOnly && (!record || record.phase === 'UPLOADING') && <FormField label="MCP package ZIP"
      description={intent ? 'Select the same ZIP to resume. Completed parts are retained.' : 'Include your code, non-secret configuration and dependencies.'}>
      <input type="file" accept=".zip,application/zip" aria-label="MCP package ZIP" disabled={busy}
        onChange={event => void choose(event.target.files?.[0])}/>
    </FormField>}
    {!existingOnly && !intent && !record && <>
      <Button variant="primary" loading={busy} disabled={!!storageError || !file || name.trim().length < 2}
        onClick={() => void act(() => upload({payload: {name: name.trim(), filename: file!.value.name, size: file!.value.size,
          source_digest: file!.digest, idempotency_key: crypto.randomUUID()}}, false))}>Upload and deploy package</Button>
    </>}
    {intent && <Box>Retained package: {intent.payload.filename}. Package contents are not stored in browser storage.</Box>}
    {progress && <StatusIndicator type="loading">{progress}</StatusIndicator>}
    {(intent || record) && <Button disabled={busy} onClick={() => void act(async () => {
      try {
        const result = await api<Upload>(record ? (record.upload_type === 'package' ? '/admin/mcp/python/' : '/admin/mcp/packages/') + record.id
          : '/admin/mcp/package-requests/' + intent!.payload.idempotency_key);
        setRecord(result);
        if (intent && !intent.id) retain({...intent, id: result.id});
      } catch (e) {if ((e as Error & {status?: number}).status !== 404 || record) throw e;}
      setChecked(true);
    })}>Check package status</Button>}
    {intent && (!record || record.phase === 'UPLOADING') && <Button disabled={busy || !checked || !sameFile || !!storageError}
      onClick={() => void act(() => upload(intent, true))}>Resume same package</Button>}
    {record && <SpaceBetween size="s">
      <StatusIndicator type={record.phase === 'READY' ? 'success' : deploying(record) ? 'loading' : 'warning'}>{record.phase}</StatusIndicator>
      {record.failure_code && <Alert type="error">{record.failure_code === 'INVALID_PACKAGE'
        ? 'The ZIP was rejected before deployment. Check its root main.py, paths, Python syntax, dependencies and size limits.'
        : discoveryFailed ? <SpaceBetween size="s">
          <Box>The Runtime was deployed, but MCP tool discovery failed. Check the server's startup settings and logs.
            Reconcile package deployment to repeat discovery after resolving a temporary startup problem.</Box>
          <Box>Put your server's non-secret startup configuration in the package. To correct it, rebuild the ZIP and choose another package.</Box>
        </SpaceBetween>
        : 'Deployment status: ' + record.failure_code}</Alert>}
      {record.phase === 'READY' && !record.deleting && <Button variant="primary" disabled={busy} onClick={() => void act(async () => {
        const current = await api<Upload>('/admin/mcp/python/' + record.id);
        setRecord(current);
        if (current.phase !== 'READY' || current.deleting) return;
        try {sessionStorage.removeItem(key); setIntent(undefined); onReady(current);}
        catch {setStorageError('Could not update the retained package status.');}
      })}>Use this MCP package</Button>}
      {record.deleting && record.phase !== 'DELETED' && <Alert type="info">Manage this deletion in Uploaded MCP deployments.</Alert>}
      {record.phase === 'NEEDS_RECONCILIATION' && !record.deleting && <SpaceBetween direction="horizontal" size="s">
        <Button disabled={busy} onClick={() => void act(async () => {
          setRecord(await api<Upload>('/admin/mcp/python/' + record.id + '/reconcile', {}));
        })}>Reconcile package deployment</Button>
        {record.retry_available && <Button disabled={busy} onClick={() => void act(async () => {
          setRecord(await api<Upload>('/admin/mcp/python/' + record.id + '/retry', {job_id: record.job_id}));
        })}>Retry package deployment step</Button>}
      </SpaceBetween>}
      {record.phase === 'DELETED' && <Alert type="info">This MCP deployment was deleted. Choose another package to create a new deployment.</Alert>}
      {['READY', 'FAILED', 'NEEDS_RECONCILIATION', 'DELETED'].includes(record.phase) && <Button disabled={busy} onClick={() => {
        try {
          sessionStorage.removeItem(key);
          onReset();
          setItems(previous => [...(record.phase === 'DELETED' ? [] : [record]), ...previous.filter(i => i.id !== record.id)]);
          setIntent(undefined); setRecord(undefined); setFile(undefined); setChecked(false); setError('');
          setStorageError('');
        } catch {setStorageError('Could not clear the retained package request.');}
      }}>Choose another package</Button>}
    </SpaceBetween>}
  </SpaceBetween>;
}
