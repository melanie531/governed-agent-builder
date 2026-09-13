import React, {useState} from 'react';
import {Alert, Box, Button, FormField, Input, Modal, SpaceBetween} from '@cloudscape-design/components';
import type {Api} from './JourneyBuilder';

type Preview = {confirmation_token: string; name: string; versions: number; resources: string[]; retained: string[]};

export default function JourneyDelete({api, agentId, version, onStarted}: {
  api: Api; agentId: string; version: number; onStarted: () => void;
}) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [typed, setTyped] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function review() {
    setBusy(true); setError(''); setTyped('');
    try { setPreview(await api<Preview>(`/journey/agents/${agentId}/deletion-preview`, {version})); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  async function remove() {
    if (!preview || busy || typed !== preview.name) return;
    setBusy(true); setError('');
    try {
      await api(`/journey/agents/${agentId}/delete`, {version, idempotency_key: crypto.randomUUID(),
        confirmation_token: preview.confirmation_token, confirm_name: typed});
      setPreview(null); onStarted();
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  return <>
    <Button loading={busy} onClick={() => void review()}>Delete agent</Button>
    {error && !preview && <Alert type="error">{error}</Alert>}
    <Modal visible={!!preview} header="Permanently delete agent" onDismiss={() => !busy && setPreview(null)}
      closeAriaLabel="Cancel deletion" footer={<Box float="right"><SpaceBetween direction="horizontal" size="xs">
        <Button disabled={busy} onClick={() => setPreview(null)}>Cancel</Button>
        <Button variant="primary" loading={busy} disabled={!preview || typed !== preview.name}
          onClick={() => void remove()}>Permanently delete</Button>
      </SpaceBetween></Box>}>
      {preview && <SpaceBetween size="m">
        <Alert type="warning">This permanently deletes {preview.name} and resources from all {preview.versions} saved versions.</Alert>
        <Box>Resources to delete:</Box><ul>{preview.resources.map(value => <li key={value}>{value}</li>)}</ul>
        <Box>Resources retained:</Box><ul>{preview.retained.map(value => <li key={value}>{value}</li>)}</ul>
        <FormField label={`Type ${preview.name} to confirm`} description="This confirmation expires after five minutes.">
          <Input ariaLabel="Agent name to confirm deletion" value={typed} onChange={({detail}) => setTyped(detail.value)}/>
        </FormField>
        {error && <Alert type="error">{error}</Alert>}
      </SpaceBetween>}
    </Modal>
  </>;
}
