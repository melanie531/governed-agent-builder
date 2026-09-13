import React, {useEffect, useState} from 'react';
import {Alert, Box, Button, Modal, SpaceBetween, StatusIndicator, Table} from '@cloudscape-design/components';
import {stateText} from './ProductText';

type AccessRequest = {id: string; component: string; reason: string; status: string; decision?: string};
export default function CatalogAccessRequests({api, visible, onDismiss, names}: {
  api: <T>(path: string, body?: unknown) => Promise<T>;
  visible: boolean; onDismiss: () => void; names: Record<string, string>;
}) {
  const [items, setItems] = useState<AccessRequest[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    if (!visible) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    setItems([]);
    async function refresh() {
      setLoading(true);
      try {
        const rows = await api<AccessRequest[]>('/requests');
        if (!stopped) {setItems(rows); setError('');}
      } catch (e) {
        if (!stopped) setError((e as Error).message);
      } finally {
        if (!stopped) {setLoading(false); timer = setTimeout(refresh, 10000);}
      }
    }
    void refresh();
    return () => {stopped = true; clearTimeout(timer);};
  }, [api, visible]);
  return <Modal visible={visible} size="large" header="Your capability requests" onDismiss={onDismiss}
    footer={<Button onClick={onDismiss}>Back to catalog</Button>}>
    <SpaceBetween size="m">
      {error && <Alert type="error">{error}</Alert>}
      <Table loading={loading} loadingText="Loading access requests" items={items} columnDefinitions={[
        {id: 'capability', header: 'Capability', cell: row => names[row.component] || row.component},
        {id: 'reason', header: 'Business reason', cell: row => row.reason},
        {id: 'status', header: 'Status', cell: row => <StatusIndicator
          type={row.status === 'APPROVED' ? 'success' : row.status === 'REJECTED' ? 'error' : 'pending'}>
          {stateText(row.status)}</StatusIndicator>},
        {id: 'decision', header: 'Administrator response', cell: row => row.decision || 'Awaiting review'},
      ]} empty={<Box>No capability requests yet.</Box>}/>
    </SpaceBetween>
  </Modal>;
}
