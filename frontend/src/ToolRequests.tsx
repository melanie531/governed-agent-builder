import React, {useEffect, useRef, useState} from 'react';
import {Alert, Box, Button, Container, FormField, Header, Input, Select, SpaceBetween, StatusIndicator, Table, Textarea} from '@cloudscape-design/components';
import type {Api} from './JourneyBuilder';
import {actionText} from './ProductText';

type ToolRequest = {id: string; requester_name: string; title: string; details: string; status: string; response: string; version: number};
const labels: Record<string, string> = {SUBMITTED: 'Submitted', IN_REVIEW: 'In review', FULFILLED: 'Fulfilled', DECLINED: 'Declined'};

export default function ToolRequests({api, admin = false}: {api: Api; admin?: boolean}) {
  const [items, setItems] = useState<ToolRequest[]>([]);
  const [title, setTitle] = useState(''), [details, setDetails] = useState('');
  const [error, setError] = useState(''), [notice, setNotice] = useState(''), [busy, setBusy] = useState(false);
  const [responses, setResponses] = useState<Record<string, string>>({});
  const [statuses, setStatuses] = useState<Record<string, string>>({});
  const token = useRef(crypto.randomUUID());
  const flight = useRef(false);
  const statusFor = (item: ToolRequest) => statuses[item.id] || (item.status === 'SUBMITTED' ? 'IN_REVIEW' : item.status);
  async function refresh() {setItems(await api<ToolRequest[]>('/tool-requests'));}
  useEffect(() => {void refresh().catch(e => setError(e.message));}, []);
  async function act(operation?: () => Promise<void>) {
    if (flight.current) return;
    flight.current = true; setBusy(true); setError(''); setNotice('');
    try {await operation?.(); await refresh();}
    catch (e) {setError((e as Error).message);}
    finally {flight.current = false; setBusy(false);}
  }
  return <SpaceBetween size="l">
    {error && <Alert type="error">{actionText(error)}</Alert>}
    {notice && <Alert type="success">{notice}</Alert>}
    {!admin && <Container header={<Header variant="h2" description="Describe a tool the AI Catalog does not offer. A platform administrator will review your request and respond here.">
      Request a new tool</Header>}><SpaceBetween size="l">
      <FormField label="What tool do you need?" constraintText="Short summary, at least 5 characters.">
        <Input ariaLabel="What tool do you need?" value={title} onChange={({detail}) => {setTitle(detail.value); token.current = crypto.randomUUID();}}/>
      </FormField>
      <FormField label="Details" description="Business context, expected use, and any constraints (optional).">
        <Textarea ariaLabel="Tool request details" value={details} onChange={({detail}) => {setDetails(detail.value); token.current = crypto.randomUUID();}}/>
      </FormField>
      <Button variant="primary" disabled={title.trim().length < 5 || busy} loading={busy} onClick={() => void act(async () => {
        await api('/tool-requests', {title, details, idempotency_key: token.current});
        setTitle(''); setDetails(''); token.current = crypto.randomUUID(); setNotice('Tool request sent.');
      })}>Send request</Button>
    </SpaceBetween></Container>}
    <Table wrapLines header={<Header variant="h2" actions={<Button disabled={busy} iconName="refresh" onClick={() => void act()}>Refresh requests</Button>}>
      {admin ? 'New tool requests' : 'Your tool requests'}</Header>} items={items} columnDefinitions={[
      ...(admin ? [{id: 'requester', header: 'Requester', cell: (item: ToolRequest) => item.requester_name}] : []),
      {id: 'title', header: 'Tool requested', cell: item => item.title},
      {id: 'details', header: 'Details', cell: item => item.details || '—'},
      {id: 'status', header: 'Status', cell: item => <StatusIndicator type={item.status === 'FULFILLED' ? 'success' : item.status === 'DECLINED' ? 'stopped' : 'pending'}>{labels[item.status]}</StatusIndicator>},
      {id: 'response', header: 'Administrator response', cell: item => admin ? <SpaceBetween size="s">
        <Textarea ariaLabel={`Response for ${item.title}`} value={responses[item.id] ?? item.response}
          onChange={({detail}) => setResponses({...responses, [item.id]: detail.value})}/>
        <Select ariaLabel={`Status for ${item.title}`} selectedOption={{value: statusFor(item), label: labels[statusFor(item)]}}
          options={['IN_REVIEW', 'FULFILLED', 'DECLINED'].map(value => ({value, label: labels[value]}))}
          onChange={({detail}) => setStatuses({...statuses, [item.id]: detail.selectedOption.value!})}/>
        <Button disabled={busy || (responses[item.id] ?? item.response).trim().length < 5} onClick={() => void act(async () => {
          await api(`/admin/tool-requests/${item.id}/response`, {version: item.version, status: statusFor(item), response: responses[item.id] ?? item.response});
          setNotice('Response saved.');
        })}>Save response</Button>
      </SpaceBetween> : item.response || 'Awaiting review'},
    ]} empty={<Box>No tool requests yet.</Box>}/>
  </SpaceBetween>;
}
