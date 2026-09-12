"""Offline synthetic bindings and SDK mocks only; never cloud acceptance."""
import copy
import json
import pytest
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.live_catalog import grant_scope
from backend import foundation_runs as runs
from tests.conftest import ORIGIN, login
from tests.test_foundation_wiring import install
from tests.test_serverless import cloud, sign_in


class Provider:
    def __init__(self, items): self.items = items
    def records(self): return copy.deepcopy(self.items)


def connected(tmp_path, monkeypatch, payload, cloud):
    monkeypatch.setenv('CATALOG_MODE', 'live')
    provider = Provider([])
    old_app, client, _ = cloud
    sign_in(cloud)
    owner = client.get('/api/me').json()['persona']
    app = create_app(repository=old_app.state.store, worker_enabled=False, catalog_provider=provider)
    app.state.hosted_auth.keys = old_app.state.hosted_auth.keys
    data = copy.deepcopy(payload)
    data['dataset'] = data['dataset'][:1]
    mapping = {cid: 'native-' + cid for cid in data['component_versions']}
    data.update(model_id=mapping[data['model_id']], tools=[mapping[x] for x in data['tools']],
                skills=[mapping[x] for x in data['skills']],
                component_versions={mapping[k]: v for k, v in data['component_versions'].items()})
    with app.state.store.tx() as db:
        for cid, nid in mapping.items():
            item = json.loads(db.select('components', where=[('id', '=', cid)]).fetchone()['body'])
            item.update(id=nid, fixture=False, source_revision='a'*64, execution_ready=True, integration_ready=True,
                        discoverable_workspaces=['research'], execution_binding={'status': 'verified'})
            provider.items.append(item)
            db.insert('components', {'id': nid, 'body': json.dumps(item)})
            db.insert('grants', {'persona': owner['id'], 'component': nid})
            runs.put(db, grant_scope(owner, nid), True)
        f = json.loads(db.select('foundations', where=[('id', '=', 'research')]).fetchone()['body'])
        for kind in ('models', 'tools', 'skills'): f[kind] = [mapping.get(x, x) for x in f[kind]]
        f['native_bindings'] = {'approved': True, 'components': [{k: x[k] for k in ('id', 'version', 'source_revision')} for x in provider.items]}
        db.update('foundations', {'body': json.dumps(f)}, where=[('id', '=', 'research')])
    with TestClient(app, base_url=str(client.base_url), cookies=client.cookies, headers=client.headers) as c:
        response = c.post('/api/agents', json=data)
        assert response.status_code == 201, response.text
        definition = response.json()
    service, control, runtime = install(app.state.store, definition, tmp_path)
    app = create_app(repository=old_app.state.store, worker_enabled=False, catalog_provider=provider, foundation_jobs=service)
    app.state.hosted_auth.keys = old_app.state.hosted_auth.keys
    return app, provider, definition, service, control, runtime, data


def test_complete_binding_explicit_deploy_reaches_sdk(tmp_path, monkeypatch, payload, cloud):
    _, client, _ = cloud
    app, provider, d, service, control, runtime, data = connected(tmp_path, monkeypatch, payload, cloud)
    with TestClient(app, base_url=str(client.base_url), cookies=client.cookies, headers=client.headers) as c:
        ready = c.get('/api/agents/'+d['agent_id']).json()['definition']['readiness']
        assert ready['deployable'], ready
        assert not control.calls and not runtime.calls
        result = c.post('/api/agents/'+d['agent_id']+'/deploy', json={'version': 1, 'idempotency_key': 'explicit-native-deploy', 'execution_mode': 'live'})
        assert result.status_code == 202, result.text
        job = result.json()['job_id']
        app.state.step_job(job)
        assert control.calls[0][0] == 'CreateAgentRuntime', c.get('/api/jobs/'+job).json()
        app.state.step_job(job)
        assert c.get('/api/jobs/'+job).json()['stage'] == 'RUNNING'
        assert any(x[0] == 'GetAgentRuntimeEndpoint' for x in control.calls)
        assert not runtime.calls


@pytest.mark.parametrize('bad', ['grant', 'owner', 'workspace', 'version', 'artifact', 'model', 'execution', 'source', 'driver', 'role', 'network', 'endpoint', 'sdk-version', 'unverified'])
def test_invalid_binding_never_deploys(tmp_path, monkeypatch, payload, cloud, bad):
    _, client, _ = cloud
    app, provider, d, service, control, runtime, data = connected(tmp_path, monkeypatch, payload, cloud)
    with app.state.store.tx() as db:
        if bad == 'grant': db.delete('grants', where=[('persona', '=', d['owner'])])
        if bad == 'owner': db.update('agents', {'owner': 'synthetic-other'}, where=[('id', '=', d['agent_id'])])
        if bad == 'workspace': db.update('agents', {'workspace': 'operations'}, where=[('id', '=', d['agent_id'])])
        if bad == 'version': db.update('agents', {'current_version': 2}, where=[('id', '=', d['agent_id'])])
        if bad == 'artifact': db.delete('settings', where=[('key', '=', 'foundation-artifact:' + d['digest'])])
        if bad == 'unverified': provider.items[0]['execution_binding']['status'] = 'unverified'
        if bad == 'model': provider.items[0]['execution_ready'] = False
        if bad == 'execution': provider.items[1]['integration_ready'] = False
        if bad == 'source': provider.items[0]['source_revision'] = 'b'*64
        if bad == 'driver': service.enabled = False
        if bad == 'role':
            from dataclasses import replace
            service.deployment.adapter.policy = replace(service.deployment.adapter.policy, approved_roles=frozenset())
        if bad == 'network': service.deployment.network = {'networkMode': 'PUBLIC'}
    with TestClient(app, base_url=str(client.base_url), cookies=client.cookies, headers=client.headers) as c:
        response = c.post('/api/agents/'+d['agent_id']+'/deploy', json={'version': 1, 'idempotency_key': 'explicit-native-negative', 'execution_mode': 'live'})
        if bad in ('endpoint', 'sdk-version'):
            assert response.status_code == 202, response.text
            job = response.json()['job_id']
            app.state.step_job(job)
            if bad == 'endpoint':
                original = control.get_agent_runtime_endpoint
                control.get_agent_runtime_endpoint = lambda **kw: {**original(**kw), 'targetVersion': '999'}
            else:
                original = control.get_agent_runtime
                control.get_agent_runtime = lambda **kw: {**original(**kw), 'agentRuntimeVersion': '999'}
            app.state.step_job(job)
            assert c.get('/api/jobs/'+job).json()['stage'] == 'BLOCKED'
        else:
            assert response.status_code in (403, 404, 409, 503), response.text
            assert not control.calls
        assert not runtime.calls


def test_save_revision_no_adapter_and_client_cannot_supply_authority(tmp_path, monkeypatch, payload, cloud):
    _, client, _ = cloud
    app, provider, d, service, control, runtime, data = connected(tmp_path, monkeypatch, payload, cloud)
    with TestClient(app, base_url=str(client.base_url), cookies=client.cookies, headers=client.headers) as c:
        for field, value in [('role', 'synthetic-role'), ('endpoint', 'https://synthetic.invalid'), ('ready', True)]:
            assert c.post('/api/agents', json={**data, field: value}).status_code == 422
            assert c.post('/api/agents/'+d['agent_id']+'/deploy', json={'version': 1, 'idempotency_key': 'forged-authority', 'execution_mode': 'live', field: value}).status_code == 422
        # Even complete live bindings cannot enter the fixture runner.
        assert c.post('/api/agents/'+d['agent_id']+'/deploy-test', json={'version': 1, 'idempotency_key': 'no-fixture-fallback'}).status_code == 503
        revised = c.post('/api/agents/'+d['agent_id']+'/versions', json={**data, 'base_version': 1, 'prompt': 'Revised instructions with immutable prompt and evidence.'})
        assert revised.status_code == 201, revised.text
        assert revised.json()['version'] == 2
        assert not revised.json()['readiness']['deployable']
        assert not control.calls and not runtime.calls
        with app.state.store.tx() as db:
            assert db.select('jobs', count=True).fetchone()[0] == 0
        assert len(c.get('/api/agents/'+d['agent_id']).json()['versions']) == 2


def test_revoke_after_enqueue_blocks_worker(tmp_path, monkeypatch, payload, cloud):
    _, client, _ = cloud
    app, provider, d, service, control, runtime, data = connected(tmp_path, monkeypatch, payload, cloud)
    with TestClient(app, base_url=str(client.base_url), cookies=client.cookies, headers=client.headers) as c:
        response = c.post('/api/agents/'+d['agent_id']+'/deploy', json={'version': 1, 'idempotency_key': 'revoke-before-worker', 'execution_mode': 'live'})
        assert response.status_code == 202, response.text
        provider.items[0]['execution_ready'] = False
        app.state.step_job(response.json()['job_id'])
        assert c.get('/api/jobs/'+response.json()['job_id']).json()['stage'] == 'BLOCKED'
        assert not control.calls and not runtime.calls
