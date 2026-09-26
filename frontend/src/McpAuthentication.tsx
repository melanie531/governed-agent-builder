import React, {useEffect, useRef, useState} from 'react';
import {Alert, Box, Button, FormField, Input, SpaceBetween} from '@cloudscape-design/components';
import type {Api} from './JourneyBuilder';

type Status = {phase: string; connection_id?: string; retry_available?: boolean; retry_count?: number; failure_code?: string};
export default function McpAuthentication({api, identityKey, name, endpoint, onSaved, exactName = false}: {
  api: Api; identityKey: string; name: string; endpoint: string; exactName?: boolean; onSaved: (id: string) => Promise<void>;
}) {
  const key = 'mcp-authentication:' + identityKey;
  const [secret, setSecret] = useState(''), [header, setHeader] = useState('Authorization'), [prefix, setPrefix] = useState('Bearer');
  const [token, setToken] = useState(''), [status, setStatus] = useState<Status>(), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [retryReady, setRetryReady] = useState(false);
  const lock = useRef(false);
  useEffect(() => {
    try {setToken(sessionStorage.getItem(key) || '');}
    catch {setError('Session storage is required to retain authentication request status.');}
  }, []);
  async function act(operation: () => Promise<Status>) {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError('');
    try {
      const result = await operation(); setStatus(result);
      if (result.phase === 'READY' && result.connection_id) {
        await onSaved(result.connection_id);
        sessionStorage.removeItem(key);
      }
    } catch (e) {
      if ((e as Error & {status?: number}).status === 404) setRetryReady(true);
      if ([422, 429].includes((e as Error & {status?: number}).status || 0)) {
        sessionStorage.removeItem(key); setToken('');
      }
      // Never include a request body or provider exception in product errors.
      setError('Authentication setup did not complete. Check the saved request status before continuing.');
    } finally {setSecret(''); lock.current = false; setBusy(false);}
  }
  return <SpaceBetween size="s">
    <Box>Save an API key or personal access token for this endpoint. AgentCore Gateway supplies it when calling the server.</Box>
    {error && <Alert type="error">{error}</Alert>}
    {(!token || retryReady) && <>
      <FormField label="API key or PAT"><Input type="password" autoComplete="off" ariaLabel="API key or PAT"
        value={secret} onChange={({detail}) => setSecret(detail.value)}/></FormField>
      <FormField label="Header name"><Input ariaLabel="Credential header" value={header} onChange={({detail}) => setHeader(detail.value)}/></FormField>
      <FormField label="Header prefix" description="Use Bearer for a bearer token, or leave empty when the server expects the raw API key.">
        <Input ariaLabel="Credential prefix" value={prefix} onChange={({detail}) => setPrefix(detail.value)}/>
      </FormField>
      <Button disabled={!name.trim() || !endpoint.trim() || !secret || busy} loading={busy} onClick={() => void act(async () => {
        const requestToken = token || crypto.randomUUID();
        sessionStorage.setItem(key, requestToken); setToken(requestToken);
        setRetryReady(false);
        const value = secret; setSecret('');
        return api<Status>('/admin/mcp/credentials', {name: exactName ? name.trim() : name.trim().slice(0, 65) + ' authentication', endpoint: endpoint.trim(),
          header, prefix, secret: value, idempotency_key: requestToken});
      })}>{retryReady ? 'Retry authentication request' : 'Save authentication'}</Button>
    </>}
    {token && <SpaceBetween size="s">
      <Box>Authentication request retained. The secret value is not saved in this browser.</Box>
      <Button loading={busy} onClick={() => void act(() => api<Status>('/admin/mcp/credentials/' + token))}>Check authentication status</Button>
      {status?.phase === 'CONTINUE' && <Button disabled={busy}
        onClick={() => void act(() => api<Status>('/admin/mcp/credentials/' + token + '/continue', {}))}>Continue authentication setup</Button>}
      {status?.phase === 'NEEDS_RECONCILIATION' && <Alert type="warning">Authentication setup needs reconciliation. Check status again; contact the platform operator if it remains unresolved.</Alert>}
      {status?.failure_code && <Box>Service status: {status.failure_code}</Box>}
      {status?.retry_available && <Button disabled={busy} onClick={() => void act(() => api<Status>(
        '/admin/mcp/credentials/' + token + '/retry', {expected_attempt: status.retry_count}))}>Retry provider creation</Button>}
    </SpaceBetween>}
  </SpaceBetween>;
}
