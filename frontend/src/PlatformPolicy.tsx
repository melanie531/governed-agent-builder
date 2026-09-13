import React, {useEffect, useState} from 'react';
import {Alert, Button, Container, FormField, Header, Input, SpaceBetween} from '@cloudscape-design/components';
import type {Api} from './JourneyBuilder';
import {actionText} from './ProductText';

export default function PlatformPolicy({api}: {api: Api}) {
  const [policy, setPolicy] = useState<{version: number; minimum_evaluation_score: number}>();
  const [score, setScore] = useState(''), [reason, setReason] = useState('');
  const [error, setError] = useState(''), [notice, setNotice] = useState(''), [busy, setBusy] = useState(false);
  async function load() {
    const current = await api<{version: number; minimum_evaluation_score: number}>('/admin/platform/policy');
    setPolicy(current); setScore(String(current.minimum_evaluation_score));
  }
  useEffect(() => {void load().catch(e => setError(e.message));}, []);
  return <Container header={<Header variant="h2" description="Evaluation runs only when a dataset is supplied. The platform threshold applies whenever evaluation is requested.">Evaluation governance</Header>}>
    <SpaceBetween size="m">
      {error && <Alert type="error">{actionText(error)}</Alert>}
      {notice && <Alert type="success">{notice}</Alert>}
      <FormField label="Minimum evaluation score" constraintText="0–1. Agents with datasets must meet this policy before they can run.">
        <Input ariaLabel="Minimum evaluation score" type="number" value={score} onChange={({detail}) => setScore(detail.value)}/>
      </FormField>
      <FormField label="Policy change reason"><Input ariaLabel="Policy change reason" value={reason} onChange={({detail}) => setReason(detail.value)}/></FormField>
      <SpaceBetween direction="horizontal" size="s">
        <Button variant="primary" loading={busy} disabled={!policy || !score.trim() || !Number.isFinite(Number(score)) || Number(score) < 0 || Number(score) > 1 || reason.trim().length < 5 || busy}
          onClick={async () => {
            setBusy(true); setError(''); setNotice('');
            try {
              await api('/admin/platform/policy', {version: policy!.version, minimum_evaluation_score: Number(score), reason});
              await load(); setNotice('Evaluation policy saved. Existing agents revalidate before their next action.');
            } catch (e) {setError((e as Error).message);}
            finally {setBusy(false);}
          }}>Save policy version</Button>
        <Button disabled={busy} onClick={() => void load().catch(e => setError(e.message))}>Refresh policy</Button>
      </SpaceBetween>
    </SpaceBetween>
  </Container>;
}
