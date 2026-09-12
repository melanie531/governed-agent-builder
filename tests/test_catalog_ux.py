"""Offline source fixture, never authenticated live acceptance."""
import copy
import json
from pathlib import Path
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.live_catalog import RegistryCatalogProvider, LiveCatalog, Sources, record_revision
from .test_native_catalog import descriptor_record, Discovery, REGISTRY, REGISTRY_ARN, expose
from .conftest import login, ORIGIN


def test_real_declared_operation_fixture_grouping_and_workspace(tmp_path, monkeypatch):
    row = descriptor_record()
    tools = json.loads((Path(__file__).parent/'fixtures/aws-documentation-tools.json').read_text())
    row['descriptors']['mcpServer']['additionalData']['tools']['data'] = json.dumps(tools)
    policy = expose([row]); rid = f"registry:{REGISTRY}:{row['recordId']}"
    for tool in tools['tools']:
        policy[rid+':tool:'+tool['name']] = copy.deepcopy(policy[rid])
    provider = RegistryCatalogProvider(Discovery([row]), REGISTRY, policy, REGISTRY_ARN)
    live = LiveCatalog(Sources([]), provider, ttl=0)
    monkeypatch.setenv('CATALOG_MODE', 'live')
    app = create_app(str(tmp_path/'ux.sqlite'), demo_mode=True, worker_enabled=False, catalog_provider=live)
    with TestClient(app, base_url=ORIGIN) as client:
        login(client)
        data = client.get('/api/catalog').json()
        assert data['count'] == 1 and len(data['items']) == 6
        operations = [x for x in data['items'] if x['kind']=='tool']
        assert len(operations)==5 and all(x['parent_id']==rid for x in operations)
        assert {x['operation'] for x in operations} == {x['name'] for x in tools['tools']}
        assert all(not x['execution_ready'] for x in operations)
        child = operations[0]['id']
        login(client,'sam')
        assert client.get('/api/catalog').json()['count']==0
        assert client.get('/api/catalog').json()['items']==[]
        assert client.get('/api/catalog/'+child).status_code==404
        assert client.post('/api/requests',json={'component_id':child,'reason':'Invisible child request'}).status_code==404


def test_hidden_parent_cannot_expose_child(client, app):
    from .test_live_catalog import record
    login(client)
    parent = record(); parent.update(id='hidden-parent',kind='mcp_server',discoverable_workspaces=['operations'])
    child = record(); child.update(id='visible-child',parent_id=parent['id'])
    with app.state.store.tx() as db:
        for c in (parent,child): db.insert('components',{'id':c['id'],'body':json.dumps(c)})
    assert not any(x['id']==child['id'] for x in client.get('/api/catalog').json()['items'])
    assert client.get('/api/catalog/'+child['id']).status_code==404
    assert client.post('/api/requests',json={'component_id':child['id'],'reason':'Child scope cannot widen'}).status_code==404
