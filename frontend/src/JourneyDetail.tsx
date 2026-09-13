import React, {useEffect, useRef, useState} from 'react';
import {
  Alert, Box, Button, Container, ExpandableSection, FormField, Header, KeyValuePairs,
  SpaceBetween, StatusIndicator, Table, Tabs, Textarea,
} from '@cloudscape-design/components';
import type {Api, Definition, Revision} from './JourneyBuilder';
import {ResearchReport} from './ResearchReport';

type Invocation = {id: string; phase: string; output?: string; error?: string; trace_id?: string; model_id?: string;
  tool_calls?: {name: string; arguments: unknown}[]};
type EvaluationCase = {id: string; input: string; output: string; score: number; passed: boolean; explanation: string;
  evaluator_id: string; trace_id: string; ignored_reference_fields: string[]; checks: {name: string; passed: boolean}[]};
type Detail = {
  id: string; current_version: number; definition: Definition & {foundation_name: string};
  deployment: {status: string; error?: string; binding?: {arn: string; version: string}};
  evaluation: {status: string; reason?: string; score: number | null; cases: EvaluationCase[]; error?: string; total?: number};
  versions: {version: number; digest: string; created: number}[];
  readiness: {deployable: boolean; issues: string[]}; last_invocation?: Invocation | null; mode: string;
};
const final = new Set(['DEPLOYED', 'SUCCEEDED', 'PASSED', 'FAILED_QUALITY', 'FAILED', 'ERROR', 'UNKNOWN', 'STALE', 'SKIPPED', 'NOT_STARTED', 'NOT_DEPLOYED']);
const label = (status: string) => ({
  QUEUED: 'Queued', WAIT_RUNTIME: 'Preparing AgentCore Runtime', SMOKE: 'Verifying the deployed agent', DEPLOYED: 'Deployed',
  EVAL_INVOKE: 'Running evaluation cases', EVAL_SCORE: 'Scoring with AgentCore', PASSED: 'Evaluation passed',
  FAILED_QUALITY: 'Needs improvement', SKIPPED: 'Skipped — no dataset', NOT_STARTED: 'Not started',
  NOT_DEPLOYED: 'Draft', FAILED: 'Deployment failed', ERROR: 'Evaluation could not complete',
  UNKNOWN: 'Outcome needs review', STALE: 'Version changed', SUCCEEDED: 'Completed',
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
  const mounted = useRef(true);
  const inFlight = useRef(false);
  useEffect(() => {
    mounted.current = true;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const current = await api<Detail>(`/journey/agents/${agentId}`);
        if (cancelled) return;
        setDetail(current);
        if (current.last_invocation) setInvocation(current.last_invocation);
        const running = !final.has(current.deployment.status) || !final.has(current.evaluation.status) ||
          (current.last_invocation && !final.has(current.last_invocation.phase));
        if (running) timer = setTimeout(poll, 2500);
      } catch (e) {
        if (!cancelled) { setError((e as Error).message); timer = setTimeout(poll, 5000); }
      }
    }
    void poll();
    return () => { cancelled = true; mounted.current = false; clearTimeout(timer); };
  }, [agentId, pollVersion]);
  async function action(kind: 'deploy' | 'evaluate' | 'invoke') {
    if (!detail || inFlight.current) return;
    inFlight.current = true;
    setBusy(true); setError('');
    try {
      await api(`/journey/agents/${agentId}/${kind}`, {version: detail.current_version, idempotency_key: crypto.randomUUID(),
        ...(kind === 'invoke' ? {input} : {})});
      if (mounted.current) setPollVersion(value => value + 1);
    } catch (e) { if (mounted.current) setError((e as Error).message); }
    finally { inFlight.current = false; if (mounted.current) setBusy(false); }
  }
  if (!detail) return <SpaceBetween size="l">{error && <Alert type="error">{error}</Alert>}<StatusIndicator type="loading">Loading agent</StatusIndicator></SpaceBetween>;
  const deployed = detail.deployment.status === 'DEPLOYED';
  const evaluationRunning = !final.has(detail.evaluation.status);
  const invocationRunning = !!invocation && !final.has(invocation.phase);
  return <SpaceBetween size="l">
    {error && <Alert type="error">{error}</Alert>}
    {!!detail.readiness.issues.length && <Alert type="warning" header="Review catalog capabilities">{detail.readiness.issues.join(' ')}</Alert>}
    <Container header={<Header variant="h2" actions={<SpaceBetween direction="horizontal" size="xs">
      <Button onClick={() => onRevise({agentId, version: detail.current_version, definition: detail.definition})}>Revise agent</Button>
      <Button iconName="refresh" onClick={() => setPollVersion(value => value + 1)}>Refresh status</Button>
      {!deployed && final.has(detail.deployment.status) && <Button variant="primary" disabled={busy || !detail.readiness.deployable}
        onClick={() => void action('deploy')}>Deploy to AgentCore</Button>}
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
      {id: 'try', label: 'Try agent', content: <Container header={<Header variant="h2">Run your deployed agent</Header>}><SpaceBetween size="l">
        <FormField label="Your question"><Textarea rows={4} value={input} onChange={({detail}) => setInput(detail.value)}
          placeholder="Ask your agent a question"/></FormField>
        <Button variant="primary" loading={busy || invocationRunning} disabled={!deployed || !detail.readiness.deployable || !input.trim() || invocationRunning}
          onClick={() => void action('invoke')}>Run agent</Button>
        {!deployed && <Box>Run becomes available after AgentCore deployment and its health check succeed.</Box>}
        {invocation?.error && <Alert type="warning">{invocation.error}</Alert>}
        {invocation?.output && <div aria-label="Agent output"><ResearchReport report={{report: invocation.output, citations: []}}/></div>}
        {invocation?.trace_id && <ExpandableSection headerText="Execution details"><KeyValuePairs columns={1} items={[
          {label: 'Trace', value: invocation.trace_id}, {label: 'Model', value: invocation.model_id},
          {label: 'Gateway tools called', value: invocation.tool_calls?.map(call => call.name).join(', ') || 'None'},
        ]}/></ExpandableSection>}
      </SpaceBetween></Container>},
      {id: 'evaluation', label: 'Evaluation results', content: <SpaceBetween size="l">
        <Container header={<Header variant="h2" actions={detail.definition.dataset.length > 0 &&
          <Button disabled={!deployed || busy || evaluationRunning || !detail.readiness.deployable} onClick={() => void action('evaluate')}>Run evaluation again</Button>}>
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
