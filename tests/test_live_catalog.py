"""Synthetic offline evidence only. No live clients or credentials."""
import json
import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException
from backend.app import create_app
from backend.live_catalog import LiveCatalog, projection, grant_scope
from .conftest import login, ORIGIN


def exposure():
    return {'approved': True, 'name': 'Approved safe name', 'description': 'Read-only approved metadata',
            'data_handling': 'Synthetic data only', 'workspaces': ['research'], 'requestable': True}


def record():
    return {'id': 'registry:safe', 'name': 'Safe tool', 'description': 'Approved metadata', 'version': '7',
            'kind': 'tool', 'provider': 'AWS Agent Registry', 'approved': True, 'external': False,
            'fixture': False, 'integration_ready': False, 'requestable': True,
            'discoverable_workspaces': ['research'], 'origin': 'AWS Agent Registry', 'refreshed_at': 1,
            'private_endpoint': 'never disclose', 'credential': 'synthetic-do-not-export'}


@pytest.mark.parametrize('path', ['/api/catalog','/api/catalog/hidden','/api/catalog/hidden/versions/1'])
def test_catalog_auth(client,path):
    assert client.get(path).status_code == 401


def test_hidden_search_count_detail_versions_and_request(client,app):
    login(client)
    with app.state.store.tx() as db:
        c=record();c.update(id='hidden',discoverable_workspaces=['operations'])
        db.insert('components',{'id':'hidden','body':json.dumps(c)})
    for path in ['/api/catalog?q=Safe','/api/catalog?kind=agent']:
        assert client.get(path).json()['count']==0
    for path in ['/api/catalog/hidden','/api/catalog/hidden/versions/7']:
        assert client.get(path).status_code==404
    assert client.post('/api/requests',json={'component_id':'hidden','reason':'Please grant access'}).status_code==404
    assert 'private_endpoint' not in client.get('/api/catalog').text


def test_fixture_catalog_reports_native_not_connected_preserving_builder(client):
    login(client)
    catalog = client.get('/api/catalog').json()
    assert catalog['mode'] == 'fixture'
    assert catalog['native_connection_state'] == 'NotConnected'
    assert catalog['items'] and all(item['fixture'] for item in catalog['items'])
    assert client.get('/api/build-options').json()['foundations']


def test_available_requestable_approval_and_revoke(client):
    login(client)
    items={r['id']:r for r in client.get('/api/catalog').json()['items']}
    assert items['bedrock-claude']['usable']
    assert items['restricted-insights']['requestable'] and not items['restricted-insights']['usable']
    r=client.post('/api/requests',json={'component_id':'restricted-insights','reason':'Research strategy purpose'}).json()
    assert client.post('/api/admin/requests/'+r['id']+'/decision',json={'approve':True,'reason':'Cannot self approve'}).status_code==403
    login(client,'sam');assert client.get('/api/requests').json()==[]
    login(client,'admin')
    assert client.post('/api/admin/requests/'+r['id']+'/decision',json={'approve':True,'reason':'Approved for research'}).status_code==200
    audit=client.get('/api/admin/audit').json()
    assert any('Approved for research' in a['detail'] for a in audit)
    login(client);assert client.get('/api/catalog/restricted-insights').json()['usable']
    login(client,'admin');client.post('/api/admin/grants',json={'persona_id':'alex','component_id':'restricted-insights','enabled':False})
    login(client);assert not client.get('/api/catalog/restricted-insights').json()['usable']


@pytest.mark.parametrize('path',['/api/catalog','/api/capabilities','/api/catalog/bedrock-claude'])
def test_unconfigured_live_no_fallback(tmp_path,monkeypatch,path):
    monkeypatch.setenv('CATALOG_MODE','live')
    app=create_app(str(tmp_path/'live.sqlite'),demo_mode=True,worker_enabled=False)
    with TestClient(app,base_url=ORIGIN) as c:
        login(c);r=c.get(path);assert r.status_code==503
        assert 'Demo route alias' not in r.text
        assert c.get('/api/me').status_code==200


def test_live_failure_sanitized():
    class Broken:
        def records(self):raise RuntimeError('private credential detail')
    with pytest.raises(HTTPException) as error:LiveCatalog(Broken(),Broken()).records()
    assert error.value.status_code==503 and 'private' not in error.value.detail


def test_live_grant_workspace_and_integration_block(app):
    persona={'id':'alex','workspace':'research','external_allowed':False}
    item=record()
    with app.state.store.tx() as db:
        db.insert('grants',{'persona':'alex','component':item['id']})
        assert not projection(db,persona,item)['granted']
        db.insert('settings',{'key':grant_scope(persona,item['id']),'body':'true'})
        p=projection(db,persona,item)
        assert p['granted'] and not p['usable'] and p['status']=='blocked'
        assert 'credential' not in p and 'private_endpoint' not in p
        item['integration_ready']=True
        assert projection(db,persona,item)['usable']
        item['discoverable_workspaces'].append('operations')
        assert not projection(db,{**persona,'workspace':'operations'},item)['usable']


def test_live_mode_never_creates_fixture_definition(tmp_path,monkeypatch,payload):
    monkeypatch.setenv('CATALOG_MODE','live')
    class Provider:
        def records(self):return [record()]
    app=create_app(str(tmp_path/'live.sqlite'),demo_mode=True,worker_enabled=False,catalog_provider=Provider())
    with TestClient(app,base_url=ORIGIN) as c:
        login(c)
        assert c.get('/api/catalog').json()['items'][0]['requestable']
        assert c.post('/api/agents',json=payload).status_code==422
        req=c.post('/api/requests',json={'component_id':'registry:safe','reason':'Approved business purpose'}).json()
        login(c,'admin')
        assert c.post('/api/admin/requests/'+req['id']+'/decision',json={'approve':True,'reason':'Scoped permission approved'}).status_code==200
        login(c)
        item=c.get('/api/catalog/registry:safe').json()
        assert item['granted'] and not item['usable']


def test_request_pins_version_and_rejects_whitespace(client):
    login(client)
    assert client.post('/api/requests',json={'component_id':'restricted-insights','reason':'     '}).status_code==422
    r=client.post('/api/requests',json={'component_id':'restricted-insights','reason':'Purpose before version change'}).json()
    login(client,'admin')
    client.post('/api/admin/catalog/components/restricted-insights',json={'approved':True})
    path='/api/admin/requests/'+r['id']+'/decision'
    assert client.post(path,json={'approve':True,'reason':'Approve stale request'}).status_code==409
    assert client.post(path,json={'approve':False,'reason':'Reject stale version request'}).status_code==200


def test_foundation_manifest_does_not_enumerate_forbidden_ids(client):
    login(client,'sam')
    assert 'external-gemini' not in client.get('/api/build-options').text


def test_request_quota(client,app):
    login(client)
    import time
    with app.state.store.tx() as db:
        for i in range(30):
            db.insert('requests',{'id':f'synthetic-{i}','requester':'alex','workspace':'research','component':'old-synthetic','reason':'synthetic quota','status':'REJECTED','decision':'synthetic','created':time.time()})
    assert client.post('/api/requests',json={'component_id':'restricted-insights','reason':'Exceeds request budget'}).status_code==429
