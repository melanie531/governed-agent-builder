import React, {useEffect, useRef, useState} from 'react';
import {Alert, Box, Button, SpaceBetween} from '@cloudscape-design/components';
import type {Api} from './JourneyBuilder';

type Intent = {name: string; endpoint: string; idempotency_key: string; user_authorization?: boolean};
type Result = {phase: string; connection_id?: string};

export default function McpIamAuthentication({api, identityKey, name, endpoint, onSaved, exactName = false}: {
  api: Api; identityKey: string; name: string; endpoint: string; exactName?: boolean; onSaved: (id: string) => Promise<void>;
}) {
  const key = 'mcp-iam-authentication:' + identityKey;
  const [intent, setIntent] = useState<Intent>(), [missing, setMissing] = useState(false);
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const lock = useRef(false);
  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(key);
      if (raw) {
        const saved = JSON.parse(raw) as Intent;
        if (!saved.idempotency_key || !saved.endpoint || !saved.name) throw new Error();
        setIntent(saved);
      }
    } catch {setError('The retained IAM request could not be restored. Restore session storage before continuing.');}
  }, []);
  async function act(operation: () => Promise<Result>, checking = false) {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError('');
    try {
      const result = await operation();
      if (result.phase === 'READY' && result.connection_id) {
        await onSaved(result.connection_id);
        sessionStorage.removeItem(key); setIntent(undefined);
      } else if (result.phase === 'DELETED') {
        sessionStorage.removeItem(key); setIntent(undefined);
        setError('This IAM connection was deleted. Save a new connection to continue.');
      } else {setError('Check the saved IAM request status before continuing.');}
    } catch (e) {
      const status = (e as Error & {status?: number}).status;
      if (checking && status === 404) setMissing(true);
      else {
        if (!checking && [409, 422, 429].includes(status || 0)) {
          sessionStorage.removeItem(key); setIntent(undefined);
        }
        setError('IAM setup did not complete. Check the endpoint and saved request status before continuing.');
      }
    } finally {lock.current = false; setBusy(false);}
  }
  return <SpaceBetween size="s">
    <Box>AgentCore Gateway signs requests using its IAM service role. Use the encoded Runtime invocation URL ending in
      /invocations?qualifier=DEFAULT in this platform’s AWS account and region. No API key or PAT is required.</Box>
    <Alert type="info">The Runtime must allow IAM invocation, and the Gateway service role must have bedrock-agentcore:InvokeAgentRuntime
      permission for this Runtime. Saving this connection records authentication settings; it does not grant AWS permissions.</Alert>
    <Box>For Gateway-managed user consent, choose OAuth / per-user consent and the server’s OAuth-compatible HTTPS endpoint.</Box>
    {error && <Alert type="error">{error}</Alert>}
    {!intent && <Button loading={busy} disabled={!name.trim() || !endpoint.trim()} onClick={() => void act(async () => {
      const saved = {name: exactName ? name.trim() : name.trim().slice(0, 76) + ' IAM', endpoint: endpoint.trim(), idempotency_key: crypto.randomUUID()};
      sessionStorage.setItem(key, JSON.stringify(saved)); setIntent(saved); setMissing(false);
      return api<Result>('/admin/mcp/iam-credentials', saved);
    })}>Save IAM connection</Button>}
    {intent && <>
      <Box>The nonsecret IAM request is retained. Checking its status does not send it again.</Box>
      <Button loading={busy} onClick={() => void act(
        () => api<Result>('/admin/mcp/iam-credentials/' + intent.idempotency_key), true)}>Check IAM connection status</Button>
      {missing && <Button disabled={busy} onClick={() => void act(() => api<Result>('/admin/mcp/iam-credentials', intent))}>
        Retry retained IAM request
      </Button>}
    </>}
  </SpaceBetween>;
}
