import React, {useEffect, useRef, useState} from 'react';
import {Alert, Box, Button, Container, Header, Link, SpaceBetween, StatusIndicator, Table} from '@cloudscape-design/components';
import type {Api} from './JourneyBuilder';

type Item = {id: string; name: string; phase: string; mode?: string};
type Flow = {id: string; server_id: string; name: string; phase: string; authorization_url?: string; mode?: string};
type Intent = {server_id: string; idempotency_key: string; force_authentication: boolean};
type Callback = {state?: string; session_uri: string};
const callbackKey = 'mcp-user-oauth-callback';
export const agentAuthPopupKey = 'studio-agent-auth-popup';

export function captureOAuthCallback() {
  if (location.pathname !== '/oauth/callback') return;
  const query = new URLSearchParams(location.search);
  const state = query.get('state') || query.get('custom_state') || '';
  const session_uri = query.get('session_id') || '';
  sessionStorage.setItem(callbackKey, JSON.stringify({...(state ? {state} : {}), session_uri}));
  // Remove transient authorization parameters before further navigation.
  history.replaceState(null, '', '/#connections');
}

export function hasOAuthCallback() {
  return sessionStorage.getItem(callbackKey) !== null;
}

export function GatewayConsent({api, flowId, popup, managedPopup, continuesAutomatically}: {
  api: Api; flowId: string; popup?: React.RefObject<Window | null>; managedPopup?: boolean;
  continuesAutomatically?: boolean
}) {
  const [flow, setFlow] = useState<Flow>(), [error, setError] = useState('');
  const [manualLink, setManualLink] = useState(false);
  const navigated = useRef(false);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const value = await api<Flow>('/mcp/user-connections/flows/' + flowId);
        if (!active) return;
        setFlow(value);
        setError('');
        if (['CONSENT_REQUIRED', 'NEEDS_CHECK'].includes(value.phase)) timer = setTimeout(poll, 2500);
      } catch (e) {
        if (active) {
          setError((e as Error).message);
          timer = setTimeout(poll, 5000);
        }
      }
    }
    void poll();
    return () => {active = false; clearTimeout(timer);};
  }, [api, flowId]);
  useEffect(() => {
    if (flow?.phase !== 'CONSENT_REQUIRED' || !flow.authorization_url || navigated.current) return;
    if (managedPopup) return;
    navigated.current = true;
    const target = popup?.current;
    if (!target || target.closed) {
      setManualLink(true);
      return;
    }
    try {target.location.replace(flow.authorization_url);}
    catch {setManualLink(true);}
  }, [flow?.phase, flow?.authorization_url, popup, managedPopup]);
  return <Alert type={flow?.phase === 'CONNECTED' ? 'success' : 'info'} header="Provider authorization">
    <SpaceBetween size="s">
      {error && <Box>{error}</Box>}
      {flow?.phase === 'CONSENT_REQUIRED' && flow.authorization_url ? <>
        <Box>{manualLink ?
          `The sign-in tab did not open. Use the link below to sign in to ${flow.name}; your question is saved here.` :
          `Sign in to ${flow.name} in the new tab. This dialogue will continue after authorization.`}</Box>
        {manualLink && <Link href={flow.authorization_url} target="_blank" rel="noopener noreferrer" external={false}>
          Open provider sign-in</Link>}
      </> : <Box>{flow?.phase === 'CONNECTED' ?
        (continuesAutomatically ? 'Authorization complete. Continuing your question…' :
          'Authorization complete. Send your question again to continue.') :
        flow?.phase === 'EXPIRED' ? 'Sign-in expired. Send your question again to request a new link.' :
        'Waiting for provider sign-in. Your question has not been repeated.'}</Box>}
    </SpaceBetween>
  </Alert>;
}

export function OAuthCallback({api, identityKey, business, onDone}: {api: Api; identityKey: string; business: boolean; onDone: () => void}) {
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [result, setResult] = useState<Flow>();
  const [agentPopup] = useState(() => sessionStorage.getItem(agentAuthPopupKey) === '1');
  const lock = useRef(false), attempted = useRef(false);
  const [callback] = useState<Callback | undefined>(() => {
    try {
      const value = JSON.parse(sessionStorage.getItem(callbackKey) || 'null') as Callback;
      if ((value?.state === undefined || /^[A-Za-z0-9_-]{32,128}$/.test(value.state)) &&
          /^urn:ietf:params:oauth:request_uri:[A-Za-z0-9-._~]+$/.test(value?.session_uri || '') &&
          value.session_uri.length <= 1024) return value;
    } catch { /* Render the invalid-callback message without sending its contents. */ }
    return undefined;
  });
  async function complete(check = false) {
    if (lock.current || !callback) return;
    lock.current = true; setBusy(true); setError('');
    try {
      const value = check && result ?
        await api<Flow>('/mcp/user-connections/flows/' + result.id + '/check', {}) :
        await api<Flow>('/mcp/user-connections/complete', callback);
      setResult(value);
      if (value.phase === 'CONNECTED') {
        sessionStorage.removeItem(callbackKey);
        sessionStorage.removeItem('mcp-user-authorization:' + identityKey);
        sessionStorage.removeItem(agentAuthPopupKey);
      }
    } catch (e) {setError((e as Error).message);}
    finally {lock.current = false; setBusy(false);}
  }
  useEffect(() => {
    if (!business || !callback || attempted.current) return;
    attempted.current = true;
    void complete();
  }, [business, callback]);
  useEffect(() => {
    if (!agentPopup || result?.phase !== 'CONNECTED') return;
    const timer = window.setTimeout(() => window.close(), 750);
    return () => window.clearTimeout(timer);
  }, [agentPopup, result?.phase]);
  return <Container header={<Header variant="h2">Complete account connection</Header>}><SpaceBetween size="m">
    <Box>Studio verifies the same signed-in user who started this connection. No query runs in this tab.</Box>
    {error && <Alert type="error">{error}</Alert>}
    {result?.phase === 'CONNECTED' ? <Alert type="success">{agentPopup ?
      'Authorization complete. This tab will close automatically; if it stays open, you can close it.' :
      'Authorization complete. Return to Studio or close this tab.'}</Alert> :
      !business ? <Alert type="info">Switch to Business User using your profile menu to finish connecting this account.</Alert> :
      !callback ? <Alert type="error">This authorization callback is invalid. Return to My connections and start again.</Alert> :
      result?.phase === 'NEEDS_CHECK' ? <Button loading={busy} onClick={() => void complete(true)}>
        Check authorization result</Button> :
      <StatusIndicator type="loading">Completing authorization</StatusIndicator>}
    {result?.phase === 'NEEDS_CHECK' && <Alert type="warning">The authorization outcome needs a status check. The consent completion request will not be sent again.</Alert>}
    {(!agentPopup || result?.phase !== 'CONNECTED') &&
      <Button disabled={busy || !business} onClick={() => {sessionStorage.removeItem(callbackKey); onDone();}}>Return to My connections</Button>}
  </SpaceBetween></Container>;
}

export default function McpUserConnections({api, identityKey}: {api: Api; identityKey: string}) {
  const key = 'mcp-user-authorization:' + identityKey;
  const [items, setItems] = useState<Item[]>([]), [flow, setFlow] = useState<Flow>();
  const [intent, setIntent] = useState<Intent>(), [missing, setMissing] = useState(false);
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const lock = useRef(false);
  async function load() {setItems((await api<{items: Item[]}>('/mcp/user-connections')).items);}
  useEffect(() => {
    void load().catch(e => setError(e.message));
    try {
      const saved = sessionStorage.getItem(key);
      if (saved) setIntent(JSON.parse(saved));
    } catch {setError('The saved authorization request could not be restored.');}
  }, [identityKey]);
  async function act(operation: () => Promise<Flow>, checking = false) {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError('');
    try {
      const result = await operation(); setFlow(result); setMissing(false);
      if (result.phase === 'CONNECTED') {
        sessionStorage.removeItem(key); setIntent(undefined);
        await load();
      }
    } catch (e) {
      if (checking && (e as Error & {status?: number}).status === 404) setMissing(true);
      else setError((e as Error).message);
    } finally {lock.current = false; setBusy(false);}
  }
  async function start(item: Item) {
    const saved = {server_id: item.id, idempotency_key: crypto.randomUUID(),
      force_authentication: item.phase === 'CONNECTED'};
    sessionStorage.setItem(key, JSON.stringify(saved)); setIntent(saved); setFlow(undefined);
    await act(() => api<Flow>('/mcp/user-connections/' + saved.server_id + '/authorize', {
      idempotency_key: saved.idempotency_key, force_authentication: saved.force_authentication}));
  }
  return <SpaceBetween size="l">
    <Box>Connect your own account for approved MCP servers that use per-user authorization. Queries use the provider permissions you authorize.</Box>
    {error && <Alert type="error">{error}</Alert>}
    <Table header={<Header variant="h2" actions={<Button disabled={busy} onClick={() => void load().catch(e => setError(e.message))}>Refresh connections</Button>}>
      Your account connections</Header>} items={items} trackBy="id" columnDefinitions={[
      {id: 'name', header: 'MCP server', cell: item => item.name},
      {id: 'status', header: 'Your authorization', cell: item => <StatusIndicator type={item.phase === 'CONNECTED' ? 'success' : 'pending'}>
        {item.phase === 'CONNECTED' ? 'Connected' : 'Not connected'}</StatusIndicator>},
      {id: 'action', header: 'Action', cell: item => item.mode === 'gateway' ?
        <Box>Run an agent using this server. Gateway requests consent when needed.</Box> :
        <Button disabled={busy || !!intent} onClick={() => void start(item)}>
          {item.phase === 'CONNECTED' ? 'Reconnect account' : 'Connect account'}</Button>},
    ]} empty={<Box>No approved MCP connections require individual sign-in.</Box>}/>
    {flow?.phase === 'CONSENT_REQUIRED' && flow.authorization_url && <Alert type="info" header={'Authorize access for ' + flow.name}>
      <SpaceBetween size="s"><Box>Sign in at the provider and review the requested access. You will return to Studio to finish connecting.</Box>
        <Link href={flow.authorization_url} external={false}>Continue to provider</Link>
      </SpaceBetween>
    </Alert>}
    {flow?.phase === 'CONNECTED' && <Alert type="success">Your account connection is ready. Return to your agent to run a query.</Alert>}
    {intent && <SpaceBetween size="s">
      {flow?.phase === 'NEEDS_CHECK' && <Alert type="warning">The authorization outcome needs a check. No consent request is automatically repeated.</Alert>}
      {flow?.phase === 'EXPIRED' && <Alert type="warning">Authorization expired. Start a new attempt.</Alert>}
      <Button disabled={busy} onClick={() => void act(
        () => api<Flow>('/mcp/user-connections/requests/' + intent.idempotency_key), true)}>Check saved authorization request</Button>
      {flow && ['NEEDS_CHECK', 'CONSENT_REQUIRED'].includes(flow.phase) && <Button disabled={busy} onClick={() => void act(
        () => api<Flow>('/mcp/user-connections/flows/' + flow.id + '/check', {}))}>Check connection status</Button>}
      {missing && <Button disabled={busy} onClick={() => void act(() => api<Flow>(
        '/mcp/user-connections/' + intent.server_id + '/authorize',
        {idempotency_key: intent.idempotency_key, force_authentication: intent.force_authentication}))}>Retry saved authorization request</Button>}
      <Button disabled={busy} onClick={() => {sessionStorage.removeItem(key); setIntent(undefined); setFlow(undefined); setMissing(false);}}>
        Start a new authorization attempt
      </Button>
    </SpaceBetween>}
  </SpaceBetween>;
}
