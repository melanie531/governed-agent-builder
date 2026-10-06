import React, {useEffect, useRef, useState} from 'react';
import {Alert, Box, Button, Container, FormField, Header, Input, Modal, SpaceBetween, StatusIndicator, Table} from '@cloudscape-design/components';
import type {Api} from './JourneyBuilder';

type Deployment = {id: string; name: string; filename: string; phase: string; job_id?: string; revision: number;
  endpoint?: string; can_delete: boolean; reason: string; blockers: {id: string; name: string; kind: string}[];
  deleting?: boolean; failure_code?: string; retry_available?: boolean};
type Intent = {id: string; payload: {confirm_name: string; expected_revision: number; idempotency_key: string}};

export default function McpDeployments({api, identityKey, refreshKey}: {api: Api; identityKey: string; refreshKey: string}) {
  const key = 'mcp-deployment-deletion:' + identityKey;
  const [items, setItems] = useState<Deployment[]>([]), [selected, setSelected] = useState<Deployment>();
  const [pending, setPending] = useState<Intent>(), [confirming, setConfirming] = useState(false);
  const [confirmation, setConfirmation] = useState(''), [busy, setBusy] = useState(false), [checked, setChecked] = useState(false);
  const [error, setError] = useState(''), [success, setSuccess] = useState(false), [storageError, setStorageError] = useState('');
  const lock = useRef(false);
  const path = (id: string) => '/admin/mcp/deployments/' + id;
  async function load() {
    const result = await api<{items: Deployment[]}>('/admin/mcp/deployments');
    setItems(result.items);
    setSelected(previous => result.items.find(i => i.id === previous?.id) || (previous?.phase === 'DELETED' ? previous : undefined));
  }
  function accept(value: Deployment) {
    setSelected(value);
    setItems(previous => [...(value.phase === 'DELETED' ? [] : [value]), ...previous.filter(i => i.id !== value.id)]);
    if (value.phase === 'DELETED') {
      sessionStorage.removeItem(key);
      setPending(undefined); setConfirming(false); setSuccess(true);
    }
  }
  useEffect(() => {void load().catch(() => setError('Could not load MCP deployments.'));}, [refreshKey]);
  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(key);
      if (!raw) return;
      const intent = JSON.parse(raw) as Intent;
      if (!intent.id || !intent.payload?.idempotency_key) throw new Error();
      setPending(intent);
      void api<Deployment>(path(intent.id)).then(accept).catch(() => setError('Check the retained deletion status before continuing.'));
    } catch {setStorageError('Could not restore the retained deployment deletion.');}
  }, [key]);
  useEffect(() => {
    if (!selected?.deleting || selected.phase !== 'DELETING' || busy) return;
    let active = true, timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const result = await api<Deployment>(path(selected!.id));
        if (!active) return;
        accept(result);
        if (result.phase === 'DELETING') timer = setTimeout(poll, 3000);
      } catch {if (active) setError('Could not refresh deletion status. Check its saved status.');}
    }
    void poll();
    return () => {active = false; clearTimeout(timer);};
  }, [selected?.id, selected?.phase, busy]);
  async function act(operation: () => Promise<void>) {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError('');
    try {await operation();}
    catch (e) {setError((e as Error).message || 'Check the saved deletion status before continuing.');}
    finally {lock.current = false; setBusy(false);}
  }
  async function send(intent: Intent) {
    sessionStorage.setItem(key, JSON.stringify(intent)); setPending(intent); setChecked(false);
    setConfirming(false);
    try {
      const result = await api<Deployment>(path(intent.id) + '/delete', intent.payload);
      setConfirming(false);
      // The mutation response is a lifecycle receipt; read fresh dependency
      // metadata before enabling any other operation.
      if (result.phase === 'DELETED') accept({...selected!, ...result});
      else accept(await api<Deployment>(path(intent.id)));
    } catch (e) {
      if ([409, 422].includes((e as Error & {status?: number}).status || 0)) {
        sessionStorage.removeItem(key); setPending(undefined); await load();
      }
      throw e;
    }
  }
  return <Container header={<Header variant="h2"
    description="Manage runtimes and uploaded packages, including failed or unfinished deployments."
    actions={<Button disabled={busy} onClick={() => void act(load)}>Refresh deployments</Button>}>Uploaded MCP deployments</Header>}>
    <SpaceBetween size="m">
      {(error || storageError) && <Alert type="error">{storageError || error}</Alert>}
      {success && <Alert type="success">MCP deployment deleted.</Alert>}
      <Table items={items} trackBy="id" wrapLines selectionType="single"
        ariaLabels={{selectionGroupLabel: 'MCP deployments', itemSelectionLabel: (_, item) => 'Select ' + item.name}}
        selectedItems={items.filter(i => i.id === selected?.id)}
        onSelectionChange={({detail}) => {
          if (!busy && !pending) {setSelected(detail.selectedItems[0]); setSuccess(false); setError('');}
        }} columnDefinitions={[
          {id: 'name', header: 'Deployment', cell: item => item.name},
          {id: 'file', header: 'Package', cell: item => <span style={{overflowWrap: 'anywhere'}}>{item.filename}</span>},
          {id: 'phase', header: 'Status', cell: item => <StatusIndicator
            type={item.phase === 'READY' ? 'success' : item.phase === 'DELETING' ? 'loading' : 'warning'}>{item.phase}</StatusIndicator>},
        ]} empty={<Box>No uploaded MCP deployments.</Box>}/>
      {selected && selected.phase !== 'DELETED' && <SpaceBetween size="s">
        {selected.reason && <Alert type="info">{selected.reason} {selected.blockers.map(b => b.name).join(', ')}</Alert>}
        <Button disabled={busy || !!pending || !!storageError || !selected.can_delete} onClick={() => {
          setConfirmation(''); setConfirming(true);
        }}>Delete MCP deployment</Button>
        {selected.failure_code && <Box>Deployment status: {selected.failure_code}</Box>}
      </SpaceBetween>}
      {(pending || selected?.deleting && selected.phase !== 'DELETED') && <SpaceBetween size="s">
        <Box>A deletion request is retained until its resources are confirmed absent.</Box>
        <Button disabled={busy} onClick={() => void act(async () => {
          accept(await api<Deployment>(path(pending?.id || selected!.id))); setChecked(true);
        })}>Check deletion status</Button>
        {pending && checked && selected?.can_delete && <Button disabled={busy} onClick={() => void act(() => send(pending))}>
          Retry retained deletion request</Button>}
        {selected?.deleting && ['FAILED', 'NEEDS_RECONCILIATION'].includes(selected.phase) && <>
          <Button disabled={busy} onClick={() => void act(async () => {
            await api(path(selected.id) + '/reconcile', {}); accept(await api<Deployment>(path(selected.id)));
          })}>Reconcile deletion</Button>
          {selected.retry_available && <Button disabled={busy} onClick={() => void act(async () => {
            await api(path(selected.id) + '/retry', {job_id: selected.job_id}); accept(await api<Deployment>(path(selected.id)));
          })}>Retry deletion step</Button>}
        </>}
      </SpaceBetween>}
      <Modal visible={confirming} header="Delete MCP deployment" onDismiss={() => {if (!busy && !pending) setConfirming(false);}}
        footer={<SpaceBetween direction="horizontal" size="s">
          <Button disabled={busy || !!pending} onClick={() => setConfirming(false)}>Cancel</Button>
          <Button variant="primary" loading={busy} disabled={!!pending || confirmation !== selected?.name}
            onClick={() => void act(() => send({id: selected!.id, payload: {
              confirm_name: confirmation, expected_revision: selected!.revision, idempotency_key: crypto.randomUUID(),
            }}))}>Confirm deployment deletion</Button>
        </SpaceBetween>}>
        <SpaceBetween size="m">
          <Box>Delete this Runtime, its workload identity, uploaded ZIP and upload parts. This cannot be undone.
            Saved authentication, remote data and shared infrastructure remain. Diagnostic logs keep their 14-day retention.</Box>
          <FormField label={'Type ' + selected?.name + ' to confirm'}>
            <Input ariaLabel="Confirm deployment name" value={confirmation} onChange={({detail}) => setConfirmation(detail.value)}/>
          </FormField>
        </SpaceBetween>
      </Modal>
    </SpaceBetween>
  </Container>;
}
