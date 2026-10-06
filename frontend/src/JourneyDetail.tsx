import React, {useEffect, useRef, useState} from 'react';
import {
  Alert, Box, Button, Container, ExpandableSection, FormField, Header, KeyValuePairs,
  SpaceBetween, StatusIndicator, Table, Tabs, Textarea,
} from '@cloudscape-design/components';
import type {Api, Definition, Revision} from './JourneyBuilder';
import {ResearchReport} from './ResearchReport';
import JourneyDelete from './JourneyDelete';
import {GatewayConsent} from './McpUserConnections';
import {providerStartKey} from './ProviderSignIn';

type Invocation = {id: string; phase: string; output?: string; error?: string; trace_id?: string; model_id?: string; authorization_flow_id?: string;
  tool_calls?: {name: string; arguments: unknown; status?: string}[]; resumable?: boolean; input?: string};
type GatewayReauthorization = {server_id: string; due: boolean; due_at?: number};
type EvaluationCase = {id: string; input: string; output: string; score: number; passed: boolean; explanation: string;
  evaluator_id: string; trace_id: string; ignored_reference_fields: string[]; checks: {name: string; passed: boolean}[]};
type Detail = {
  id: string; current_version: number; definition: Definition & {foundation_name: string};
  deployment: {status: string; error?: string; binding?: {arn: string; version: string; gateway_force_auth_v1?: boolean}; authorization_flow_id?: string};
  evaluation: {status: string; reason?: string; score: number | null; cases: EvaluationCase[]; error?: string; total?: number; authorization_flow_id?: string};
  versions: {version: number; digest: string; created: number}[];
  readiness: {deployable: boolean; issues: string[]}; last_invocation?: Invocation | null; mode: string;
  conversation?: {id: string; messages: {role: string; text: string; job_id: string; trace_id?: string}[]} | null;
  deletion?: {status: string; error?: string; runtimes?: number} | null;
  user_authorization_required?: boolean;
  preopen_provider_tab?: boolean;
  gateway_force_auth_supported?: boolean;
  gateway_reauthorization?: GatewayReauthorization[];
};
const final = new Set(['DEPLOYED', 'SUCCEEDED', 'PASSED', 'FAILED_QUALITY', 'FAILED', 'ERROR', 'UNKNOWN', 'STALE', 'SKIPPED', 'NOT_STARTED', 'NOT_DEPLOYED', 'DELETED', 'DELETE_FAILED', 'AUTHORIZATION_REQUIRED']);
const label = (status: string) => ({
  QUEUED: 'Queued', WAIT_RUNTIME: 'Preparing AgentCore Runtime', SMOKE: 'Finishing AgentCore Runtime setup', DEPLOYED: 'Deployed',
  EVAL_INVOKE: 'Running evaluation cases', EVAL_SCORE: 'Scoring with AgentCore', PASSED: 'Evaluation passed',
  FAILED_QUALITY: 'Needs improvement', SKIPPED: 'Skipped — no dataset', NOT_STARTED: 'Not started',
  NOT_DEPLOYED: 'Draft', FAILED: 'Deployment failed', ERROR: 'Evaluation could not complete',
  UNKNOWN: 'Outcome needs review', STALE: 'Version changed', SUCCEEDED: 'Completed',
  AUTHORIZATION_REQUIRED: 'Provider consent required',
  DELETE_RUNTIMES: 'Deleting AgentCore Runtimes', DELETE_DATA: 'Deleting agent data',
  DELETE_RECORDS: 'Removing saved records', DELETED: 'Deleted', DELETE_FAILED: 'Cleanup needs attention',
}[status] || status);
const statusType = (status: string): 'success' | 'error' | 'warning' | 'pending' | 'loading' =>
  ['DEPLOYED', 'PASSED', 'SUCCEEDED'].includes(status) ? 'success' :
    ['FAILED', 'ERROR'].includes(status) ? 'error' : ['UNKNOWN', 'STALE', 'FAILED_QUALITY'].includes(status) ? 'warning' :
      final.has(status) ? 'pending' : 'loading';

export default function JourneyDetail({api, agentId, onRevise}: {api: Api; agentId: string; onRevise: (revision: Revision) => void}) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [input, setInput] = useState('');
  const [invocation, setInvocation] = useState<Invocation | null>(null);
  const [tab, setTab] = useState('try');
  const [pollVersion, setPollVersion] = useState(0);
  const [conversationId, setConversationId] = useState<string | undefined>();
  const mounted = useRef(true);
  const inFlight = useRef(false);
  const authPopup = useRef<Window | null>(null);
  const pendingPopupJob = useRef<string | null>(null);
  const pendingPopupNonce = useRef<string | null>(null);
  const attemptedResumes = useRef(new Set<string>());
  useEffect(() => {
    mounted.current = true;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const current = await api<Detail>(`/journey/agents/${agentId}`);
        if (cancelled) return;
        setDetail(current);
        setError('');
        if (current.last_invocation) setInvocation(current.last_invocation);
        if (current.last_invocation?.phase === 'AUTHORIZATION_REQUIRED' && !current.last_invocation.resumable) {
          setInput(value => value || current.last_invocation?.input || '');
        }
        if (current.last_invocation?.id === pendingPopupJob.current &&
            final.has(current.last_invocation.phase) && current.last_invocation.phase !== 'AUTHORIZATION_REQUIRED') {
          if (authPopup.current && !authPopup.current.closed) authPopup.current.close();
          authPopup.current = null;
          pendingPopupJob.current = null;
          pendingPopupNonce.current = null;
        }
        let waitingForConsent = false;
        const pending = current.last_invocation;
        if (pending?.phase === 'AUTHORIZATION_REQUIRED' && pending.resumable && pending.authorization_flow_id) {
          const flow = await api<{phase: string}>('/mcp/user-connections/flows/' + pending.authorization_flow_id);
          if (cancelled) return;
          // The public flow API also uses NEEDS_CHECK while the callback is
          // completing. Keep reading until it settles; never restart consent.
          waitingForConsent = ['CONSENT_REQUIRED', 'NEEDS_CHECK'].includes(flow.phase);
          if (flow.phase === 'CONNECTED' && !attemptedResumes.current.has(pending.id)) {
            attemptedResumes.current.add(pending.id);
            try {
              await api(`/journey/agents/${agentId}/resume`, {version: current.current_version, job_id: pending.id});
              if (!cancelled) timer = setTimeout(poll, 100);
              return;
            } catch (e) {
              if (!cancelled) {
                setError('Provider sign-in completed, but the saved question could not continue: ' + (e as Error).message);
                // The callback can win the continuation race. Reconcile its
                // latest job once without repeating the failed mutation.
                timer = setTimeout(poll, 2500);
              }
              return;
            }
          }
        }
        const running = !final.has(current.deployment.status) || !final.has(current.evaluation.status) ||
          (current.last_invocation && !final.has(current.last_invocation.phase)) ||
          waitingForConsent ||
          (current.deletion && !final.has(current.deletion.status));
        if (running) timer = setTimeout(poll, 2500);
      } catch (e) {
        if (!cancelled) { setError((e as Error).message); timer = setTimeout(poll, 5000); }
      }
    }
    void poll();
    return () => { cancelled = true; mounted.current = false; clearTimeout(timer); };
  }, [agentId, pollVersion]);
  function openProviderTab() {
    // Open on the user gesture. The tab itself follows Gateway's asynchronous link.
    const nonce = crypto.randomUUID();
    authPopup.current = window.open('/?provider-start=' + encodeURIComponent(nonce), '_blank');
    pendingPopupNonce.current = authPopup.current ? nonce : null;
    if (authPopup.current) {
      authPopup.current.opener = null;
    }
  }
  async function submitAction(kind: 'deploy' | 'evaluate' | 'invoke', text = input,
    selectedConversation = (conversationId ?? detail?.conversation?.id) || null, version = detail?.current_version) {
    if (!detail || inFlight.current) return;
    inFlight.current = true;
    if (kind === 'invoke' && detail.gateway_force_auth_supported &&
        (detail.gateway_reauthorization || []).some(item =>
          item.due || (typeof item.due_at === 'number' && item.due_at <= Date.now() / 1000))) {
      openProviderTab();
    }
    setBusy(true); setError('');
    try {
      const result = await api<{job_id: string; conversation_id?: string}>(`/journey/agents/${agentId}/${kind}`, {version, idempotency_key: crypto.randomUUID(),
        ...(kind === 'invoke' ? {input: text, conversation_id: selectedConversation} : {})});
      if (kind === 'invoke') {
        pendingPopupJob.current = result.job_id;
        if (pendingPopupNonce.current) {
          try {
            localStorage.setItem(providerStartKey(pendingPopupNonce.current),
              JSON.stringify({agentId, jobId: result.job_id}));
          } catch {
            if (authPopup.current && !authPopup.current.closed) authPopup.current.close();
            authPopup.current = null;
            pendingPopupNonce.current = null;
          }
        }
        setConversationId(result.conversation_id); setInput('');
      }
      if (mounted.current) setPollVersion(value => value + 1);
    } catch (e) {
      if (authPopup.current && !authPopup.current.closed) authPopup.current.close();
      authPopup.current = null;
      pendingPopupJob.current = null;
      if (pendingPopupNonce.current) {
        try {localStorage.setItem(providerStartKey(pendingPopupNonce.current), JSON.stringify({failed: true}));}
        catch { /* The original dialogue still displays the request error. */ }
      }
      pendingPopupNonce.current = null;
      if (mounted.current) setError((e as Error).message);
    }
    finally { inFlight.current = false; if (mounted.current) setBusy(false); }
  }
  async function action(kind: 'deploy' | 'evaluate' | 'invoke') {
    if (!detail || inFlight.current) return;
    await submitAction(kind);
  }
  if (!detail) return <SpaceBetween size="l">{error && <Alert type="error">{error}</Alert>}<StatusIndicator type="loading">Loading agent</StatusIndicator></SpaceBetween>;
  if (detail.deletion?.status === 'DELETED') return <Alert type="success" header="Agent deleted">
    AgentCore Runtimes and this agent's saved data have been removed. Shared Gateway connections and the AI Catalog remain available.
  </Alert>;
  const deleting = !!detail.deletion;
  const deployed = detail.deployment.status === 'DEPLOYED' && !deleting;
  const evaluationRunning = !final.has(detail.evaluation.status);
  const invocationRunning = !!invocation && !final.has(invocation.phase);
  const currentConversation = conversationId ?? detail.conversation?.id;
  const messages = detail.conversation && detail.conversation.id === currentConversation ? detail.conversation.messages : [];
  const arn = detail.deployment.binding?.arn;
  const invocationConsentId = invocation?.phase === 'AUTHORIZATION_REQUIRED' ? invocation.authorization_flow_id : undefined;
  const evaluationConsentId = detail.evaluation.status === 'AUTHORIZATION_REQUIRED' ?
    detail.evaluation.authorization_flow_id : undefined;
  const apiExample = arn ? `import boto3, json, uuid

client = boto3.client("bedrock-agentcore", region_name="${arn.split(':')[3]}")
request_id = uuid.uuid4().hex
response = client.invoke_agent_runtime(
    agentRuntimeArn="${arn}",
    qualifier="DEFAULT",
    runtimeSessionId="gab-" + request_id,
    contentType="application/json",
    payload=json.dumps({"input": "Ask your question here", "request_id": request_id}).encode(),
)
result = json.loads(response["response"].read())
print(result["output"])
print(result["tool_calls"])
print(result["trace_id"])` : '';
  return <SpaceBetween size="l">
    {error && <Alert type="error">{error}</Alert>}
    {!!detail.gateway_reauthorization?.length && !detail.gateway_force_auth_supported && deployed && <Alert type="warning">
      Revise and deploy this agent version to enable provider sign-in for this Studio session.
    </Alert>}
    {detail.deletion && <Alert type={detail.deletion.status === 'DELETE_FAILED' ? 'error' : 'info'}
      header={label(detail.deletion.status)}>{detail.deletion.error || 'Cleanup is running. You can leave this page and return to check its result.'}</Alert>}
    {!!detail.readiness.issues.length && <Alert type="warning" header="Review catalog capabilities">{detail.readiness.issues.join(' ')}</Alert>}
    <Container header={<Header variant="h2" actions={<SpaceBetween direction="horizontal" size="xs">
      <Button disabled={deleting} onClick={() => onRevise({agentId, version: detail.current_version, definition: detail.definition})}>Revise agent</Button>
      <Button iconName="refresh" onClick={() => setPollVersion(value => value + 1)}>Refresh status</Button>
      {!deployed && !deleting && final.has(detail.deployment.status) && <Button variant="primary" disabled={busy || !detail.readiness.deployable}
        onClick={() => void action('deploy')}>Deploy to AgentCore</Button>}
      {(!deleting || detail.deletion?.status === 'DELETE_FAILED') && <JourneyDelete api={api} agentId={agentId}
        version={detail.current_version} onStarted={() => setPollVersion(value => value + 1)}/>}
    </SpaceBetween>}>Agent overview</Header>}>
      <KeyValuePairs columns={3} items={[
        {label: 'Template', value: detail.definition.foundation_name}, {label: 'Version', value: `v${detail.current_version}`},
        {label: 'Deployment', value: <StatusIndicator type={statusType(detail.deployment.status)}>{label(detail.deployment.status)}</StatusIndicator>},
        {label: 'Evaluation', value: <StatusIndicator type={statusType(detail.evaluation.status)}>{label(detail.evaluation.status)}</StatusIndicator>},
        {label: 'Evaluation score', value: detail.evaluation.score == null ? 'Not scored' : `${Math.round(detail.evaluation.score * 100)} / 100`},
      ]}/>
      {detail.deployment.error && <Alert type="error">{detail.deployment.error}</Alert>}
      {detail.evaluation.error && <Alert type="warning">{detail.evaluation.error} Deployment remains available if its status is Deployed.</Alert>}
    </Container>
    <Tabs activeTabId={tab} onChange={({detail}) => setTab(detail.activeTabId)} tabs={[
      {id: 'try', label: 'Chat', content: <Container header={<Header variant="h2" description="Chat with your deployed agent. Each reply uses the previous three turns; the latest ten turns are saved."
        actions={<Button disabled={busy || invocationRunning} onClick={() => {setConversationId(''); setInput(''); setInvocation(null);}}>New conversation</Button>}>
        Chat with your agent</Header>}><SpaceBetween size="l">
        {messages.map((message, index) => <Container key={message.job_id + message.role} header={<Header variant="h3">{message.role === 'user' ? 'You' : 'Agent'}</Header>}>
          <div aria-label={message.role === 'assistant' && index === messages.length - 1 ? 'Agent output' : undefined}>
            <ResearchReport report={{report: message.text, citations: []}}/>
          </div>
        </Container>)}
        <FormField label="Your question"><Textarea rows={4} value={input} onChange={({detail}) => setInput(detail.value)}
          placeholder="Ask your agent a question"/></FormField>
        <Button variant="primary" loading={busy || invocationRunning} disabled={!deployed || !detail.readiness.deployable || !input.trim() || invocationRunning}
          onClick={() => void action('invoke')}>Send message</Button>
        {invocationConsentId && <GatewayConsent key={invocationConsentId} api={api} flowId={invocationConsentId}
          popup={authPopup} managedPopup={pendingPopupJob.current === invocation?.id && !!pendingPopupNonce.current}
          continuesAutomatically={!!invocation?.resumable}/>}
        {!deployed && <Box>{detail.deployment.status === 'AUTHORIZATION_REQUIRED' ?
          'Deploy again to finish Runtime setup.' :
          'Run becomes available when the AgentCore Runtime endpoint is ready.'}</Box>}
        {invocation?.phase === 'AUTHORIZATION_REQUIRED' && !invocation.resumable &&
          <Alert type="warning">This question cannot be continued automatically because an earlier tool may have completed.
            Review the restored question and send it again after consent.</Alert>}
        {invocation?.error && <Alert type="warning">{invocation.error}</Alert>}
        {invocation?.tool_calls?.some(call => call.status === 'error') &&
          <Alert type="warning">An MCP tool returned an error. Its result was not verified.</Alert>}
        {!messages.length && currentConversation !== '' && invocation?.output && <div aria-label="Agent output"><ResearchReport report={{report: invocation.output, citations: []}}/></div>}
        {invocation?.trace_id && <ExpandableSection headerText="Execution details"><KeyValuePairs columns={1} items={[
          {label: 'Trace', value: invocation.trace_id}, {label: 'Model', value: invocation.model_id},
          {label: 'Gateway tools called', value: invocation.tool_calls?.map(call => call.name + (call.status === 'error' ? ' (error)' : '')).join(', ') || 'None'},
        ]}/></ExpandableSection>}
      </SpaceBetween></Container>},
      {id: 'api', label: 'API access', content: <Container header={<Header variant="h2">Call this deployed agent</Header>}><SpaceBetween size="m">
        <Box>{detail.user_authorization_required ?
          'Ask your question in Chat. Studio supplies your authenticated user context when a tool needs it. A direct IAM invocation without user context cannot run per-user tools.' :
          'Use an AWS identity allowed to invoke this Runtime. The call uses the same deployed model, instructions and Gateway tools as this chat.'}</Box>
        {deployed ? <><KeyValuePairs columns={1} items={[{label: 'AgentCore Runtime ARN', value: arn},
          {label: 'Required IAM action', value: 'bedrock-agentcore:InvokeAgentRuntime'}]}/>
          {!detail.user_authorization_required && <FormField label="Python API example"><Textarea readOnly rows={17}
            value={apiExample} ariaLabel="Python API example"/></FormField>}
        </> : <Box>The API example becomes available after deployment succeeds.</Box>}
      </SpaceBetween></Container>},
      {id: 'evaluation', label: 'Evaluation results', content: <SpaceBetween size="l">
        {evaluationConsentId && <GatewayConsent key={evaluationConsentId} api={api} flowId={evaluationConsentId}/>}
        <Container header={<Header variant="h2" actions={detail.definition.dataset.length > 0 &&
          <Button disabled={!deployed || busy || evaluationRunning || !detail.readiness.deployable} onClick={() => void action('evaluate')}>
            {detail.evaluation.status === 'NOT_STARTED' ? 'Run evaluation' : 'Run evaluation again'}</Button>}>
          AgentCore on-demand evaluation</Header>}><SpaceBetween size="m">
          <StatusIndicator type={statusType(detail.evaluation.status)}>{label(detail.evaluation.status)}</StatusIndicator>
          {detail.evaluation.status === 'SKIPPED' ? <Box>No dataset was supplied. No evaluation pipeline was run. Add cases in a new version when you want to evaluate.</Box> :
            <Box>{detail.evaluation.cases.length} of {detail.definition.dataset.length} cases scored. Each case must meet the {Math.round(detail.definition.minimum_score * 100)} / 100 threshold.</Box>}
          {detail.evaluation.status === 'FAILED_QUALITY' && <Alert type="warning">The agent is deployed. Review the cases below to improve its answers.</Alert>}
        </SpaceBetween></Container>
        {detail.evaluation.cases.map(item => <Container key={item.id} header={<Header variant="h2" description={item.input}
          info={<StatusIndicator type={item.passed ? 'success' : 'warning'}>{item.passed ? 'Passed' : 'Needs improvement'} · {Math.round(item.score * 100)} / 100</StatusIndicator>}>{item.id}</Header>}>
          <SpaceBetween size="l"><ResearchReport report={{report: item.output, citations: []}}/>
            <Box>{item.explanation}</Box>
            {!!item.ignored_reference_fields?.length && <Alert type="warning">The evaluator did not use these requirements: {item.ignored_reference_fields.join(', ')}.</Alert>}
            {!!item.checks.length && <Table header={<Header variant="h3">Additional format checks</Header>} items={item.checks}
              columnDefinitions={[{id: 'name', header: 'Check', cell: check => check.name},
                {id: 'passed', header: 'Result', cell: check => check.passed ? 'Passed' : 'Failed'}]}/>}
            <ExpandableSection headerText="Evaluation details"><Box>Evaluator: {item.evaluator_id}</Box><Box>Trace: {item.trace_id}</Box></ExpandableSection>
          </SpaceBetween>
        </Container>)}
      </SpaceBetween>},
      {id: 'versions', label: 'Versions', content: <Table header={<Header variant="h2">Saved versions</Header>} items={detail.versions}
        columnDefinitions={[{id: 'version', header: 'Version', cell: item => `v${item.version}`},
          {id: 'created', header: 'Saved', cell: item => new Date(item.created * 1000).toLocaleString()},
          {id: 'current', header: 'Status', cell: item => item.version === detail.current_version ? 'Current' : 'Previous version'}]}/>},
    ]}/>
  </SpaceBetween>;
}
