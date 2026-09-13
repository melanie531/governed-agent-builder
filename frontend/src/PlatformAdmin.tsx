import React, {useEffect, useState} from 'react';
import {Alert, Box, Button, Container, ExpandableSection, FormField, Header, Input, KeyValuePairs, Multiselect, Select, SpaceBetween, StatusIndicator, Table} from '@cloudscape-design/components';
import type {Api} from './JourneyBuilder';
import {actionText} from './ProductText';

type Agent = {id: string; name: string; workspace: string; status: string; model_id: string; invocations: number; succeeded: number; failed: number; unknown: number; pending: number; error_rate: number | null; latency_p50_ms: number | null; latency_p95_ms: number | null; latency_samples: number};
type Overview = {hours: number; generated_at: number; scope: string; latency_scope: string; agents: Agent[]; pending_tool_requests: number; pending_access_requests: number};
type Capability = {id: string; name: string; description: string; kind: string; version: string; provider: string; approved: boolean; execution_ready: boolean; discoverable_workspaces: string[]; parent_id?: string; registry?: {arn: string}};
type Catalog = {items: Capability[]; workspaces: string[]};
type NativeRecord = {status: string; recordArn: string; statusReason?: string};
type Model = {id: string; name: string; provider: string; type: string};
type Metrics = {scope: string; start: string; end: string; models: ({model_id: string} & Record<string, string | number | null>)[]};
type Costs = {status: string; reason?: string; scope?: string; start?: string; end?: string; generated_at?: number; currency?: string; estimated?: boolean; total?: string | null; services?: {service: string; amount: string}[]; daily?: {date: string; amount: string | null}[]};
const latency = (value: number | null | undefined) => value == null ? 'No data' : `${(value / 1000).toFixed(2)} s`;
const number = (value: unknown) => typeof value === 'number' ? value.toLocaleString() : 'No data';
const money = (value: string | null | undefined) => value == null ? 'No data' : new Intl.NumberFormat('en-US', {style: 'currency', currency: 'USD', maximumFractionDigits: 4}).format(Number(value));

export default function PlatformAdmin({api, page}: {api: Api; page: string}) {
  const [overview, setOverview] = useState<Overview>(), [metrics, setMetrics] = useState<Metrics>(), [costs, setCosts] = useState<Costs>();
  const [error, setError] = useState(''), [loading, setLoading] = useState(false);
  const load = async () => {
    setLoading(true); setError('');
    try {
      if (page === 'overview' || page === 'performance') setOverview(await api<Overview>('/admin/platform/overview'));
      if (page === 'performance') setMetrics(await api<Metrics>('/admin/platform/performance/models'));
      if (page === 'costs') setCosts(await api<Costs>('/admin/platform/costs'));
    } catch (e) {setError((e as Error).message);}
    finally {setLoading(false);}
  };
  useEffect(() => {void load();}, [page]);
  if (page === 'registry') return <Registry api={api}/>;
  return <SpaceBetween size="l">
    <Box float="right"><Button loading={loading} iconName="refresh" onClick={() => void load()}>Refresh metrics</Button></Box>
    {error && <Alert type="error">{actionText(error)}</Alert>}
    {overview && page === 'overview' && <Container header={<Header variant="h2">Platform activity</Header>}>
      <KeyValuePairs columns={3} items={[
        {label: 'Agents', value: overview.agents.length},
        {label: 'Tool requests awaiting action', value: overview.pending_tool_requests},
        {label: 'Capability access approvals', value: overview.pending_access_requests},
      ]}/>
    </Container>}
    {overview && page !== 'costs' && <Table wrapLines
      header={<Header variant="h2" description={`Last ${overview.hours} hours. ${overview.scope}`}>Agent performance</Header>}
      items={overview.agents} columnDefinitions={[
        {id: 'name', header: 'Agent', cell: row => <SpaceBetween size="xxs"><Box>{row.name}</Box><Box variant="small">{row.workspace}</Box></SpaceBetween>},
        {id: 'status', header: 'Deployment', cell: row => row.status.replaceAll('_', ' ')},
        {id: 'model', header: 'Model', cell: row => row.model_id},
        {id: 'count', header: 'Invocations', cell: row => row.invocations},
        {id: 'errors', header: 'Error rate', cell: row => row.error_rate == null ? 'No completed calls' : `${(row.error_rate * 100).toFixed(1)}% (${row.failed})`},
        {id: 'unknown', header: 'Unknown / pending', cell: row => `${row.unknown} / ${row.pending}`},
        {id: 'p50', header: 'Latency p50', cell: row => latency(row.latency_p50_ms)},
        {id: 'p95', header: 'Latency p95', cell: row => latency(row.latency_p95_ms)},
        {id: 'samples', header: 'Latency samples', cell: row => row.latency_samples},
      ]} footer={overview.latency_scope} empty={<Box>No agents have been deployed through this platform.</Box>}/>}
    {page === 'performance' && metrics && <Table wrapLines
      header={<Header variant="h2" description={`${metrics.scope} ${metrics.start.slice(0, 10)} UTC (complete day).`}>Model performance</Header>}
      items={metrics.models} columnDefinitions={[
        {id: 'model', header: 'Model', cell: row => row.model_id},
        {id: 'invocations', header: 'Invocations', cell: row => number(row.invocations)},
        {id: 'errors', header: 'Client / server errors', cell: row => `${number(row.client_errors)} / ${number(row.server_errors)}`},
        {id: 'throttles', header: 'Throttled requests', cell: row => number(row.throttles)},
        {id: 'p50', header: 'Inference p50', cell: row => latency(row.latency_p50_ms as number | null)},
        {id: 'p95', header: 'Inference p95', cell: row => latency(row.latency_p95_ms as number | null)},
        {id: 'input', header: 'Input tokens', cell: row => number(row.input_tokens)},
        {id: 'output', header: 'Output tokens', cell: row => number(row.output_tokens)},
      ]} empty={<Box>No Catalog models to monitor.</Box>}/>}
    {page === 'costs' && costs && (costs.status === 'UNAVAILABLE' ? <Alert type="info" header="Cost data unavailable">{costs.reason}</Alert> : <>
      <Container header={<Header variant="h2" description={costs.scope}>Platform AWS cost</Header>}><KeyValuePairs columns={3} items={[
        {label: 'Project-tagged cost', value: money(costs.total)},
        {label: 'Period (UTC)', value: `${costs.start} to ${costs.end} (exclusive)`},
        {label: 'Billing status', value: costs.estimated ? 'Estimated — subject to billing updates' : 'Reported by Cost Explorer'},
      ]}/><Box margin={{top: 'm'}}>Cost Explorer data can lag by 24 hours or more. Untagged charges are excluded; this is not the total AWS account bill.</Box></Container>
      <Table header={<Header variant="h2">Cost by service</Header>} items={costs.services || []} columnDefinitions={[
        {id: 'service', header: 'AWS service', cell: row => row.service}, {id: 'amount', header: 'Cost (USD)', cell: row => money(row.amount)},
      ]} empty={<Box>No project-attributed billing data in this period.</Box>}/>
      {!!costs.daily?.length && <Table header={<Header variant="h2">Daily cost</Header>} items={costs.daily} columnDefinitions={[
        {id: 'date', header: 'Date (UTC)', cell: row => row.date}, {id: 'amount', header: 'Cost (USD)', cell: row => money(row.amount)},
      ]}/>}
    </>)}
  </SpaceBetween>;
}

function Registry({api}: {api: Api}) {
  const [catalog, setCatalog] = useState<Catalog>({items: [], workspaces: []});
  const [selected, setSelected] = useState(''), [record, setRecord] = useState<NativeRecord>();
  const [reason, setReason] = useState(''), [workspaces, setWorkspaces] = useState<string[]>([]);
  const [models, setModels] = useState<Model[]>([]), [modelId, setModelId] = useState('');
  const [modelWorkspaces, setModelWorkspaces] = useState<string[]>([]), [registrationReason, setRegistrationReason] = useState('');
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [registryError, setRegistryError] = useState('');
  const item = catalog.items.find(item => item.id === selected);
  async function refresh() {const current = await api<Catalog>('/admin/platform/catalog'); setCatalog(current); return current;}
  useEffect(() => {void refresh().catch(e => setError(e.message));}, []);
  useEffect(() => {void api('/admin/platform/registry').catch(e => setRegistryError(e.message));}, []);
  useEffect(() => {
    setRecord(undefined);
    if (!item) return;
    setWorkspaces(item.discoverable_workspaces);
    let active = true;
    if (item.registry) void api<NativeRecord>(`/admin/platform/catalog/${item.id}/registry`)
      .then(value => {if (active) setRecord(value);}).catch(e => {if (active) setError(e.message);});
    return () => {active = false;};
  }, [item?.id, item?.version, item?.registry?.arn]);
  async function act(operation: () => Promise<unknown>, message: string) {
    if (busy) return;
    setBusy(true); setError(''); setNotice('');
    try {
      await operation(); const current = await refresh();
      const refreshed = current.items.find(row => row.id === selected);
      if (refreshed?.registry) setRecord(await api<NativeRecord>(`/admin/platform/catalog/${selected}/registry`));
      setNotice(message);
    } catch (e) {setError((e as Error).message);}
    finally {setBusy(false);}
  }
  const action = (name: string, extra = {}) => api(`/admin/platform/catalog/${selected}/${name}`, {version: item!.version, reason, ...extra});
  const workspaceField = <FormField label="Visible workspaces"><Multiselect ariaLabel="Visible workspaces"
    options={catalog.workspaces.map(value => ({value, label: value}))}
    selectedOptions={workspaces.map(value => ({value, label: value}))}
    onChange={({detail}) => setWorkspaces(detail.selectedOptions.map(option => option.value!))}/></FormField>;
  return <SpaceBetween size="l">
    {registryError && <Alert type="info" header="AgentCore Registry unavailable">{actionText(registryError)}. New Registry approvals and publication require this connection.</Alert>}
    {error && <Alert type="error">{actionText(error)}</Alert>}
    {notice && <Alert type="success">{notice}</Alert>}
    <Table selectionType="single" selectedItems={item ? [item] : []} trackBy="id"
      onSelectionChange={({detail}) => {setSelected(detail.selectedItems[0]?.id || ''); setReason(''); setNotice(''); setError('');}}
      header={<Header variant="h2" description="Register resource metadata, approve it in AgentCore Registry, then publish access through the AI Catalog."
        actions={<Button disabled={busy} onClick={() => void act(async () => {}, 'Catalog refreshed.')}>Refresh catalog</Button>}>Registry & AI Catalog</Header>}
      items={catalog.items.filter(row => row.kind !== 'tool')} columnDefinitions={[
        {id: 'name', header: 'Capability', cell: row => row.name},
        {id: 'kind', header: 'Type', cell: row => row.kind === 'mcp_server' ? 'MCP server' : row.kind === 'model' ? 'Model' : 'Skill'},
        {id: 'version', header: 'Catalog version', cell: row => row.version},
        {id: 'availability', header: 'Catalog availability', cell: row => <StatusIndicator type={row.approved ? 'success' : 'stopped'}>{row.approved ? 'Published' : 'Not published'}</StatusIndicator>},
        {id: 'scope', header: 'Workspaces', cell: row => row.discoverable_workspaces.join(', ')},
        {id: 'registry', header: 'AgentCore Registry', cell: row => row.registry ? 'Registered' : 'Not registered'},
      ]} empty={<Box>No platform capabilities have been registered.</Box>}/>
    {item && <Container header={<Header variant="h2" description={item.description}>{item.name}</Header>}><SpaceBetween size="m">
      <KeyValuePairs columns={2} items={[{label: 'Registry status', value: record?.status.replaceAll('_', ' ') || (item.registry ? 'Loading' : 'Not registered')},
        {label: 'Connection', value: item.execution_ready ? 'Validated' : 'Validation required'}]}/>
      {record?.statusReason && <Box>{record.statusReason}</Box>}
      {item.kind === 'mcp_server' && <ExpandableSection headerText="Tools exposed by this MCP connection">
        {catalog.items.filter(row => row.parent_id === item.id).map(tool => <Box key={tool.id}>{tool.name} — {tool.approved ? 'Approved' : 'Not approved'}</Box>)}
      </ExpandableSection>}
      {workspaceField}
      <FormField label="Decision reason"><Input ariaLabel="Catalog decision reason" value={reason} onChange={({detail}) => setReason(detail.value)}/></FormField>
      <SpaceBetween direction="horizontal" size="s">
        {!item.registry && <Button disabled={busy || !!registryError || reason.trim().length < 5} onClick={() => void act(() => action('register'), 'Registry registration submitted. Refresh to check its status.')}>Register in AgentCore</Button>}
        {item.kind === 'model' && !item.execution_ready && <Button disabled={busy || reason.trim().length < 5} onClick={() => void act(() => action('validate-model'), 'Model connection validated.')}>Validate model connection</Button>}
        {record?.status === 'DRAFT' && <Button disabled={busy || reason.trim().length < 5} onClick={() => void act(() => action('submit'), 'Submitted for Registry approval.')}>Submit for approval</Button>}
        {record?.status === 'PENDING_APPROVAL' && <>
          <Button disabled={busy || reason.trim().length < 5} onClick={() => void act(() => action('decision', {approve: true}), 'Registry record approved. Publish it to make it available in the Catalog.')}>Approve Registry record</Button>
          <Button disabled={busy || reason.trim().length < 5} onClick={() => void act(() => action('decision', {approve: false}), 'Registry record rejected.')}>Reject Registry record</Button>
        </>}
        {record?.status === 'APPROVED' && <Button variant="primary" disabled={busy || !item.execution_ready || !workspaces.length || reason.trim().length < 5}
          onClick={() => void act(() => action('availability', {approved: true, workspaces}), 'Catalog version published. Users request access from the AI Catalog.')}>Publish to AI Catalog</Button>}
        {item.approved && <Button disabled={busy || !workspaces.length || reason.trim().length < 5} onClick={() => void act(() => action('availability', {approved: false, workspaces}), 'Capability withdrawn. Existing agents must revalidate before execution.')}>Withdraw from Catalog</Button>}
      </SpaceBetween>
    </SpaceBetween></Container>}
    <Container header={<Header variant="h2" description="Discover available Bedrock models and inference profiles. Registration creates an unpublished model for review.">Add a model</Header>}><SpaceBetween size="m">
      <Button disabled={busy} onClick={() => void act(async () => setModels(await api<Model[]>('/admin/platform/models/discovery')), 'Bedrock model discovery refreshed.')}>Discover Bedrock models</Button>
      {!!models.length && <>
        <FormField label="Bedrock model or inference profile"><Select ariaLabel="Discovered Bedrock model" filteringType="auto"
          options={models.map(model => ({value: model.id, label: model.name, description: `${model.type} · ${model.id}`}))}
          selectedOption={models.find(model => model.id === modelId) ? {value: modelId, label: models.find(model => model.id === modelId)!.name} : null}
          onChange={({detail}) => setModelId(detail.selectedOption.value!)}/></FormField>
        <FormField label="Model workspaces"><Multiselect ariaLabel="Model workspaces"
          options={catalog.workspaces.map(value => ({value, label: value}))}
          selectedOptions={modelWorkspaces.map(value => ({value, label: value}))}
          onChange={({detail}) => setModelWorkspaces(detail.selectedOptions.map(option => option.value!))}/></FormField>
        <FormField label="Registration reason"><Input ariaLabel="Model registration reason" value={registrationReason} onChange={({detail}) => setRegistrationReason(detail.value)}/></FormField>
        <Button variant="primary" disabled={busy || !modelId || !modelWorkspaces.length || registrationReason.trim().length < 5}
          onClick={() => void act(async () => {const result = await api<{id: string}>('/admin/platform/models', {model_id: modelId, workspaces: modelWorkspaces, reason: registrationReason}); setSelected(result.id);}, 'Model registered. Validate its connection and complete Registry approval before publication.')}>Register model</Button>
      </>}
    </SpaceBetween></Container>
  </SpaceBetween>;
}
