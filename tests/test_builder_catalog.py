"""Offline Builder/Catalog contract tests; no AWS clients or writes."""
import copy
import json
import pytest
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.live_catalog import grant_scope
from .conftest import ORIGIN, login


class Provider:
    def __init__(self):
        self.items = [self.item('model:native:target:claude', 'model'),
                      self.item('model:native:target:gpt', 'model'),
                      self.item('registry:native:record:tool:search', 'tool')]

    @staticmethod
    def item(cid, kind):
        return dict(id=cid, name=cid, description='Native descriptor source', version='7', kind=kind,
                    provider='Native source', approved=True, fixture=False, external=False,
                    discoverable_workspaces=['research'], requestable=True, supported=True,
                    integration_ready=False, execution_ready=False, source_revision='a'*64,
                    descriptor_version='2025-11-25', inputSchema={'type': 'object'},
                    execution_binding={'status': 'unverified', 'last_checked': None})

    def records(self):
        return copy.deepcopy(self.items)


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.setenv('CATALOG_MODE', 'live')
    provider = Provider()
    app = create_app(str(tmp_path/'state.sqlite'), demo_mode=True, worker_enabled=False, catalog_provider=provider)
    with app.state.store.tx() as db:
        row = db.select('foundations', columns=['body'], where=[('id', '=', 'research')]).fetchone()
        foundation = json.loads(row[0])
        foundation['native_bindings'] = {'approved': True, 'components': [
            {**{k: x[k] for k in ('id', 'version', 'source_revision')},
             **({'compatible_model_ids': [provider.items[0]['id']]} if x['kind'] == 'tool' else {})}
            for x in provider.items]}
        db.update('foundations', {'body': json.dumps(foundation)}, where=[('id', '=', 'research')])
    with TestClient(app, base_url=ORIGIN) as client:
        login(client)
        yield app, client, provider


def native_payload(payload, provider):
    payload.update(model_id=provider.items[0]['id'], tools=[provider.items[2]['id']], skills=[],
                   component_versions={x['id']: x['version'] for x in (provider.items[0], provider.items[2])})
    return payload


def test_live_does_not_erase_approved_foundations(live):
    app, client, provider = live
    provider.items = []
    data = client.get('/api/build-options').json()
    assert {x['id'] for x in data['foundations']} == {'research', 'knowledge'}
    assert all('native_bindings' not in x for x in data['foundations'])
    assert 'exact native_bindings' in data['integration_status']
    assert data['choices'] == {'models': [], 'tools': [], 'skills': []}


def test_unconfigured_provider_actionable_notconnected(tmp_path, monkeypatch):
    monkeypatch.setenv('CATALOG_MODE', 'live')
    monkeypatch.delenv('NATIVE_CATALOG_CONFIG', raising=False)
    app = create_app(str(tmp_path/'state.sqlite'), demo_mode=True, worker_enabled=False)
    with TestClient(app, base_url=ORIGIN) as c:
        login(c)
        result = c.get('/api/build-options').json()
        assert len(result['foundations']) == 2
        assert 'NotConnected' in result['integration_status'] and 'NATIVE_CATALOG_CONFIG' in result['integration_status']
        assert result['choices']['models'] == []
        assert 'bedrock-claude' not in json.dumps(result)


def test_same_ids_grants_descriptor_and_model_compatibility(live):
    app, c, p = live
    mid, other, tid = [x['id'] for x in p.items]
    query = {'foundation_id': 'research', 'model_id': mid}
    rows = c.get('/api/build-options', params=query).json()['choices']
    tool = rows['tools'][0]
    catalog = c.get('/api/catalog').json()['items']
    original = next(x for x in catalog if x['id'] == tid)
    for key in ('id', 'version', 'source_revision', 'descriptor_version', 'inputSchema', 'granted', 'requestable', 'execution_ready'):
        assert tool[key] == original[key]
    assert tool['requestable'] and not tool['deployable']
    with app.state.store.tx() as db:
        db.insert('grants', {'persona': 'alex', 'component': tid})
        db.insert('settings', {'key': grant_scope({'id': 'alex', 'workspace': 'research'}, tid), 'body': 'true'})
    assert c.get('/api/build-options', params=query).json()['choices']['tools'][0]['granted']
    assert c.get('/api/catalog/'+tid).json()['granted']
    assert c.get('/api/build-options', params={**query, 'model_id': other}).json()['choices']['tools'] == []
    p.items[2]['discoverable_workspaces'] = ['operations']
    assert tid not in c.get('/api/build-options', params=query).text
    assert tid not in c.get('/api/catalog').text


def test_save_unready_draft_no_runtime_and_deploy_blocked(live, payload):
    app, c, p = live
    original = native_payload(payload, p)
    r = c.post('/api/agents', json=original)
    assert r.status_code == 201, r.text
    saved = r.json()
    assert not saved['readiness']['deployable']
    assert {'grant_required', 'execution_not_ready', 'deployment_driver_missing'} <= {x['code'] for x in saved['readiness']['issues']}
    for key in ('prompt', 'dataset', 'rubric', 'component_versions'):
        assert saved[key] == original[key]
    for execution in ('live', 'fixture'):
        blocked = c.post(f"/api/agents/{saved['agent_id']}/deploy-test", json={'version': 1, 'execution_mode': execution, 'idempotency_key': 'blocked-native-draft'})
        assert blocked.status_code == 503
        assert blocked.json()['detail']['code'] == 'LIVE_EXECUTION_BLOCKED'
    with app.state.store.tx() as db:
        assert db.select('jobs', count=True).fetchone()[0] == 0
    assert c.get('/api/agents').json()[0]['id'] == saved['agent_id']


def test_revoked_selection_preserved_and_revisable(live, payload):
    app, c, p = live
    data = native_payload(payload, p)
    saved = c.post('/api/agents', json=data).json()
    p.items[2]['approved'] = False
    detail = c.get('/api/agents/'+saved['agent_id']).json()
    assert detail['definition']['tools'] == data['tools']
    assert 'selection_unavailable' in {x['code'] for x in detail['definition']['readiness']['issues']}
    data.update(base_version=1, prompt='Updated user prompt preserved despite revoked selection.')
    revised = c.post('/api/agents/'+saved['agent_id']+'/versions', json=data)
    assert revised.status_code == 201, revised.text
    assert revised.json()['tools'] == data['tools']
    assert revised.json()['version'] == 2
    data.pop('base_version')
    assert c.post('/api/agents', json=data).status_code == 422


def test_unbound_and_empty_drafts_are_not_deployable(live, payload):
    app, c, p = live
    data = native_payload(payload, p)
    data.update(foundation_id='knowledge')
    saved = c.post('/api/agents', json=data).json()
    assert 'binding_missing' in {x['code'] for x in saved['readiness']['issues']}
    data.update(model_id='', tools=[], skills=[], component_versions={})
    r = c.post('/api/agents', json=data)
    assert r.status_code == 201, r.text
    assert 'model_missing' in {x['code'] for x in r.json()['readiness']['issues']}


def test_stale_binding_does_not_become_compatible(live, payload):
    app, c, p = live
    p.items[0]['source_revision'] = 'b'*64
    result = c.get('/api/build-options', params={'foundation_id': 'research'}).json()
    assert p.items[0]['id'] not in [x['id'] for x in result['choices']['models']]
    saved = c.post('/api/agents', json=native_payload(payload, p)).json()
    assert 'binding_stale' in {x['code'] for x in saved['readiness']['issues']}
