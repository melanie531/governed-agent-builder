import React, {useEffect, useState} from 'react';
import {Box, SpaceBetween, StatusIndicator} from '@cloudscape-design/components';
import type {Api} from './JourneyBuilder';
import {agentAuthPopupKey} from './McpUserConnections';

type Ticket = {agentId: string; jobId: string};
type Invocation = {id: string; phase: string; authorization_flow_id?: string};
type Detail = {last_invocation?: Invocation | null};
type Flow = {phase: string; authorization_url?: string};

export const providerStartKey = (nonce: string) => 'studio-provider-start:' + nonce;
const identifier = /^[A-Za-z0-9_-]{1,128}$/;
const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

function gatewayAuthorizationUrl(value: string): string {
  const url = new URL(value);
  if (url.protocol !== 'https:' ||
      !/^bedrock-agentcore\.[a-z0-9-]+\.amazonaws\.com$/.test(url.hostname) ||
      url.pathname !== '/identities/oauth2/authorize' ||
      !url.searchParams.has('request_uri')) {
    throw new Error('Gateway returned an unexpected provider sign-in address.');
  }
  return url.href;
}

export default function ProviderSignIn({api, nonce}: {api: Api; nonce: string}) {
  const [message, setMessage] = useState(
    'Studio is starting a fresh provider sign-in for your question. Keep this tab open; it will redirect to the provider.');
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let active = true;
    sessionStorage.setItem(agentAuthPopupKey, '1');
    async function run() {
      const deadline = Date.now() + 10 * 60 * 1000;
      let ticket: Ticket | undefined;
      while (active && Date.now() < deadline && !ticket) {
        const raw = localStorage.getItem(providerStartKey(nonce));
        if (raw) {
          localStorage.removeItem(providerStartKey(nonce));
          const value = JSON.parse(raw) as Ticket & {failed?: boolean};
          if (value.failed) throw new Error('Studio could not send the question. Return to the agent dialogue.');
          if (!identifier.test(value.agentId) || !identifier.test(value.jobId)) {
            throw new Error('The provider sign-in request was invalid.');
          }
          ticket = value;
        } else {
          await delay(300);
        }
      }
      if (!ticket || !active) throw new Error('Provider sign-in did not start. Return to the agent dialogue.');
      while (active && Date.now() < deadline) {
        const detail = await api<Detail>('/journey/agents/' + encodeURIComponent(ticket.agentId));
        const invocation = detail.last_invocation;
        if (invocation?.id === ticket.jobId) {
          if (invocation.phase === 'AUTHORIZATION_REQUIRED' && invocation.authorization_flow_id) {
            const flow = await api<Flow>('/mcp/user-connections/flows/' +
              encodeURIComponent(invocation.authorization_flow_id));
            if (flow.phase === 'CONSENT_REQUIRED' && flow.authorization_url) {
              window.location.replace(gatewayAuthorizationUrl(flow.authorization_url));
              return;
            }
            if (flow.phase === 'EXPIRED') throw new Error('Provider sign-in expired. Return to the agent dialogue.');
            if (flow.phase === 'CONNECTED') {
              setMessage('Authorization is complete. Return to the agent dialogue.');
              window.close();
              return;
            }
          } else if (['SUCCEEDED', 'FAILED', 'ERROR', 'UNKNOWN'].includes(invocation.phase)) {
            setMessage('Provider sign-in was not needed. Return to the agent dialogue.');
            window.close();
            return;
          }
        }
        await delay(1500);
      }
      throw new Error('Provider sign-in timed out. Return to the agent dialogue.');
    }
    void run().catch(error => {
      if (active) {
        setFailed(true);
        setMessage(error instanceof Error ? error.message : 'Provider sign-in could not start.');
      }
    });
    return () => {active = false;};
  }, [api, nonce]);

  return <main style={{maxWidth: 720, margin: '48px auto', padding: '0 24px'}}>
    <SpaceBetween size="m">
      <StatusIndicator type={failed ? 'error' : 'loading'}>{failed ? 'Sign-in could not start' : 'Starting provider sign-in'}</StatusIndicator>
      <Box>{message}</Box>
    </SpaceBetween>
  </main>;
}
