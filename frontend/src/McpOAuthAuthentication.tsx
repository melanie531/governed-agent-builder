import React, {useEffect, useRef, useState} from 'react';
import {Alert, Box, Button, FormField, Header, Input, Select, SpaceBetween} from '@cloudscape-design/components';
import type {Api} from './JourneyBuilder';

type Grant = 'AUTHORIZATION_CODE' | 'CLIENT_CREDENTIALS';
type Client = {auth_type: 'OAUTH'; client_id: string; issuer?: string; authorization_endpoint?: string; token_endpoint?: string;
  discovery_url?: string; client_authentication_method: string; grant_type: Grant};
type Intent = {name: string; endpoint: string; scopes: string[]; idempotency_key: string} & (Client | {provider_arn: string});
type Result = {phase: string; connection_id?: string; retry_available?: boolean; retry_count?: number; failure_code?: string};
export const oauthMethods = [
  {value: 'oauth-3lo', label: 'User sign-in (OAuth 3LO)', grant: 'AUTHORIZATION_CODE'},
  {value: 'oauth-2lo', label: 'Service credentials (OAuth 2LO)', grant: 'CLIENT_CREDENTIALS'},
] as const;
const userFields = [
  ['client_id', 'OAuth client ID'], ['issuer', 'OAuth issuer'],
  ['authorization_endpoint', 'Authorization endpoint'], ['token_endpoint', 'Token endpoint'],
] as const;
const serviceFields = [['client_id', 'OAuth client ID'], ['discovery_url', 'OAuth discovery URL']] as const;
const path = (intent: Intent) => '/admin/mcp/' + ('provider_arn' in intent ? 'oauth-credentials' : 'credentials');

export default function McpOAuthAuthentication({api, identityKey, name, endpoint, onSaved, grant, allowedGrants}: {
  api: Api; identityKey: string; name: string; endpoint: string; onSaved: (id: string) => Promise<void>; grant: Grant; allowedGrants?: string[];
}) {
  const key = 'mcp-gateway-oauth-reference:' + identityKey;
  const userConsent = grant === 'AUTHORIZATION_CODE';
  const fields = userConsent ? userFields : serviceFields;
  const [provider, setProvider] = useState(''), [scopes, setScopes] = useState('');
  const [mode, setMode] = useState('new'), [secret, setSecret] = useState('');
  const [client, setClient] = useState<Client>({auth_type: 'OAUTH', client_id: '',
    ...(userConsent ? {issuer: '', authorization_endpoint: '', token_endpoint: ''} : {discovery_url: ''}),
    client_authentication_method: 'CLIENT_SECRET_POST', grant_type: grant});
  const [intent, setIntent] = useState<Intent>(), [missing, setMissing] = useState(false);
  const [status, setStatus] = useState<Result>();
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [storageError, setStorageError] = useState('');
  const lock = useRef(false);
  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(key);
      if (raw) {
        const saved = JSON.parse(raw) as Intent;
        if (!saved.idempotency_key || !saved.endpoint
          || (!('provider_arn' in saved) && !oauthMethods.some(m => m.grant === saved.grant_type))) throw new Error();
        setIntent(saved);
      }
    } catch {setStorageError('The saved OAuth request is unavailable. Restore session storage before continuing.');}
  }, [key]);
  async function act(operation: () => Promise<Result>, checking = false) {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError('');
    try {
      const result = await operation(); setStatus(result); setMissing(false);
      if (result.phase === 'READY' && result.connection_id) {
        await onSaved(result.connection_id);
        sessionStorage.removeItem(key); setIntent(undefined);
      } else if (result.phase === 'DELETED') {
        sessionStorage.removeItem(key); setIntent(undefined);
        setError('This connection was deleted. Save a new connection to continue.');
      }
    } catch (e) {
      const status = (e as Error & {status?: number}).status;
      if (checking && status === 404) setMissing(true);
      else {
        if (!checking && [422, 429].includes(status || 0)) {
          sessionStorage.removeItem(key); setIntent(undefined);
        }
        setError('OAuth setup did not complete. Check the saved request status before continuing.');
      }
    } finally {setSecret(''); lock.current = false; setBusy(false);}
  }
  const savedGrant = intent && ('provider_arn' in intent ? 'AUTHORIZATION_CODE' : intent.grant_type);
  const heading = userConsent ? 'Set up user sign-in (3LO)' : 'Set up service access (2LO)';
  if (savedGrant && savedGrant !== grant) return <SpaceBetween size="s">
    <Header variant="h3">{heading}</Header>
    <Alert type="warning" header="Saved OAuth request needs reconciliation">
      Select {savedGrant === 'AUTHORIZATION_CODE' ? 'User sign-in (OAuth 3LO)' : 'Service credentials (OAuth 2LO)'} to
      finish the saved request before starting another setup.
    </Alert>
  </SpaceBetween>;
  return <SpaceBetween size="s">
    <Header variant="h3">{heading}</Header>
    <Box>{userConsent
      ? 'Each user signs in to the provider and approves access when an agent calls a tool. Configure an authorization-code client, then register the callback URL shown after saving.'
      : 'Gateway uses a service client to request tokens. Supply the provider’s HTTPS discovery URL and client credentials. Users do not sign in or approve consent for this connection.'}</Box>
    {!intent && <>
    {userConsent && <FormField label="OAuth provider setup"><Select ariaLabel="OAuth provider setup"
      options={[{value: 'new', label: 'Create provider'}, {value: 'existing', label: 'Existing provider'}]}
      selectedOption={{value: mode, label: mode === 'new' ? 'Create provider' : 'Existing provider'}}
      disabled={busy} onChange={({detail}) => setMode(detail.selectedOption.value!)}/></FormField>}
    {mode === 'existing' ? <FormField label="OAuth provider ARN" description="Use an existing 3LO provider backed by this installation’s Secrets Manager secret.">
      <Input ariaLabel="OAuth provider ARN" value={provider} disabled={busy}
        onChange={({detail}) => setProvider(detail.value)}/>
    </FormField> : <>
      {fields.map(([field, label]) => <FormField key={field} label={label}
        description={field === 'discovery_url' ? 'The provider’s OAuth or OpenID discovery document supplies its token endpoint and supported client authentication methods.' : undefined}>
        <Input ariaLabel={label} value={client[field] || ''} disabled={busy}
          onChange={({detail}) => setClient(previous => ({...previous, [field]: detail.value}))}/>
      </FormField>)}
      <FormField label="Client authentication"><Select ariaLabel="Client authentication"
        options={[{value: 'CLIENT_SECRET_POST', label: 'Client secret in POST body'}, {value: 'CLIENT_SECRET_BASIC', label: 'HTTP Basic'}]}
        selectedOption={{value: client.client_authentication_method,
          label: client.client_authentication_method === 'CLIENT_SECRET_POST' ? 'Client secret in POST body' : 'HTTP Basic'}}
        disabled={busy} onChange={({detail}) => setClient(previous => ({...previous, client_authentication_method: detail.selectedOption.value!}))}/></FormField>
      <FormField label="OAuth client secret" description="Stored in Secrets Manager. Never saved in browser storage.">
        <Input type="password" autoComplete="off" ariaLabel="OAuth client secret" value={secret}
          disabled={busy} onChange={({detail}) => setSecret(detail.value)}/>
      </FormField>
    </>}
    <FormField label="OAuth scopes" description="Enter the provider’s scopes separated by spaces.">
      <Input ariaLabel="OAuth scopes" value={scopes} disabled={busy}
        onChange={({detail}) => setScopes(detail.value)}/>
    </FormField>
    </>}
    {(error || storageError) && <Alert type="error">{storageError || error}</Alert>}
    {!intent && <Button loading={busy} disabled={!!storageError || (!!allowedGrants && !allowedGrants.includes(grant))
      || !name.trim() || !endpoint.trim() || !scopes.trim()
      || (mode === 'existing' ? !provider.trim() : !secret || fields.some(([f]) => !client[f]?.trim()))}
      onClick={() => void act(async () => {
        const saved: Intent = {name: name.trim().slice(0, 74) + ' OAuth', endpoint: endpoint.trim(),
          ...(mode === 'existing' ? {provider_arn: provider.trim()} : client),
          scopes: scopes.trim().split(/\s+/), idempotency_key: crypto.randomUUID()};
        sessionStorage.setItem(key, JSON.stringify(saved)); setIntent(saved); setMissing(false);
        return api<Result>(path(saved), {...saved, ...(mode === 'new' ? {secret} : {})});
      })}>Save OAuth connection</Button>}
    {intent && <>
      <Box>Saved request for {intent.name} at {intent.endpoint}. The client secret is not retained in this browser.</Box>
      {status?.failure_code && <Box>Service status: {status.failure_code}</Box>}
      <Button loading={busy} onClick={() => void act(
        () => api<Result>(path(intent) + '/' + intent.idempotency_key), true)}>Check OAuth connection status</Button>
      {missing && !('provider_arn' in intent) && <FormField label="OAuth client secret" description="Reenter to retry the exact request after its absence was confirmed.">
        <Input type="password" autoComplete="off" ariaLabel="OAuth client secret" value={secret} onChange={({detail}) => setSecret(detail.value)}/>
      </FormField>}
      {missing && <Button disabled={busy || (!('provider_arn' in intent) && !secret)} onClick={() => void act(
        () => api<Result>(path(intent), {...intent, ...('provider_arn' in intent ? {} : {secret})}))}>Retry retained OAuth request</Button>}
      {status?.phase === 'CONTINUE' && <Button disabled={busy} onClick={() => void act(
        () => api<Result>(path(intent) + '/' + intent.idempotency_key + '/continue', {}))}>Continue OAuth setup</Button>}
      {status?.retry_available && <Button disabled={busy} onClick={() => void act(
        () => api<Result>(path(intent) + '/' + intent.idempotency_key + '/retry',
          {expected_attempt: status.retry_count}))}>Retry OAuth provider creation</Button>}
    </>}
  </SpaceBetween>;
}
