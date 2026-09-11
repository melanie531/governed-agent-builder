import io
import json
import subprocess
import sys
import time
import zipfile
import pytest
from fastapi.testclient import TestClient
from backend.app import create_app, digest
from backend.harness import run_case
from .conftest import ORIGIN, login, create, enqueue, finish

@pytest.mark.parametrize("scenario,expected", [('pass','PASS'),('fail','NEEDS_CHANGES'),('missing-citation','NEEDS_CHANGES'),('missing-judge','NEEDS_CHANGES'),('format','NEEDS_CHANGES'),('no-refusal','NEEDS_CHANGES')])
def test_measured_gates(app,client,payload,scenario,expected):
    login(client)
    if scenario=='fail':payload['dataset'][0]['required_terms']=['not-in-any-source']
    elif scenario=='missing-citation':payload['prompt']+=' No citations.'
    elif scenario=='missing-judge':payload['rubric']['profile']='llm-required'
    elif scenario=='format':payload['output_format']='json'
    elif scenario=='no-refusal':payload['prompt']+=' Do not refuse.'
    d=create(client,payload);j=enqueue(client,d);job=finish(app,client,j)
    assert job['stage']==expected
    assert job['result']['definition_digest']==d['digest']
    assert job['result']['dataset_ref']==d['dataset_ref']
    assert job['result']['production_ready'] is False
    assert job['result']['cases'][0]['output']
    assert job['result']['score']==sum(c['score'] for c in job['result']['cases'])/3
    if scenario=='missing-judge':assert job['result']['missing_required_judge'] is True
    assert any(e['stage']=='CASE_EVIDENCE' for e in job['events'])

def test_fixture_never_reads_expected_answer(payload):
    a=run_case(payload,'Aurora')
    payload['dataset'][0]['required_terms']=['fabricated truth']
    b=run_case(payload,'Aurora')
    assert a==b
    payload['prompt']+=' Uppercase.'
    assert 'OCTOBER' in run_case(payload,'Aurora')['output']

def test_json_success(app,client,payload):
    login(client);payload['output_format']='json'
    for case in payload['dataset']:case['expected_format']='json'
    d=create(client,payload);j=enqueue(client,d)
    assert finish(app,client,j)['stage']=='PASS'

def test_immutable_version_digest_and_stale_evidence(app,client,payload):
    login(client);d=create(client,payload);j=enqueue(client,d);finish(app,client,j)
    path=f"/api/agents/{d['agent_id']}"
    assert client.post(path+'/invoke',json={'version':1,'input':'Aurora'}).status_code==200
    revised={**payload,'base_version':1,'prompt':payload['prompt']+' Uppercase.'}
    r=client.post(path+'/versions',json=revised)
    assert r.status_code==201;d2=r.json();assert d2['version']==2 and d2['digest']!=d['digest']
    assert digest({k:v for k,v in d2.items() if k!='digest'})==d2['digest']
    assert client.post(path+'/versions',json=revised).status_code==409
    assert client.post(path+'/invoke',json={'version':1,'input':'Aurora'}).status_code==409
    assert client.post(path+'/invoke',json={'version':2,'input':'Aurora'}).status_code==409
    assert client.get('/api/jobs/'+j).json()['stale'] is True
    with app.state.store.tx() as db:assert json.loads(db.execute('SELECT body FROM versions WHERE agent=? AND version=1',(d['agent_id'],)).fetchone()[0])['prompt']==payload['prompt']
    j2=enqueue(client,d2,'revision-two-key');assert finish(app,client,j2)['stage']=='PASS'
    assert 'OCTOBER' in client.post(path+'/invoke',json={'version':2,'input':'Aurora'}).json()['output']

def test_idempotency_per_agent_and_version(app,client,payload):
    login(client);d=create(client,payload);j=enqueue(client,d)
    assert enqueue(client,d)==j
    path=f"/api/agents/{d['agent_id']}"
    assert client.post(path+'/deploy-test',json={'version':1,'idempotency_key':'another-key-xx'}).status_code==409
    finish(app,client,j)
    d2=client.post(path+'/versions',json={**payload,'base_version':1}).json()
    assert client.post(path+'/deploy-test',json={'version':2,'idempotency_key':'idempotency-test-1'}).status_code==409
    assert enqueue(client,d2,'different-key-xx')!=j

def test_policy_change_invalidates_evidence(app,client,payload):
    login(client);d=create(client,payload);j=enqueue(client,d);finish(app,client,j)
    login(client,'admin');assert client.post('/api/admin/policy',json={'minimum_score':1,'require_judge':True}).status_code==200
    login(client);path=f"/api/agents/{d['agent_id']}"
    assert client.post(path+'/invoke',json={'version':1,'input':'Aurora'}).status_code==409
    j2=enqueue(client,d,'new-policy-test');assert finish(app,client,j2)['result']['missing_required_judge']

def test_revise_during_job_fails_closed(app,client,payload):
    login(client);d=create(client,payload);j=enqueue(client,d)
    assert client.post(f"/api/agents/{d['agent_id']}/versions",json={**payload,'base_version':1}).status_code==201
    assert finish(app,client,j)['stage']=='NEEDS_CHANGES'

def test_timeout_is_durable(app,client,payload):
    login(client);d=create(client,payload);j=enqueue(client,d)
    with app.state.store.tx() as db:db.execute('UPDATE jobs SET deadline=0 WHERE id=?',(j,))
    job=finish(app,client,j)
    assert job['stage']=='NEEDS_CHANGES' and 'deadline' in job['result']['error']

def test_restart_runs_durable_queued_job(tmp_path,payload):
    path=str(tmp_path/'restart.sqlite')
    app1=create_app(path,demo_mode=True,worker_enabled=False)
    with TestClient(app1,base_url=ORIGIN) as c:
        login(c);d=create(c,payload);j=enqueue(c,d)
        app1.state.step_job(j)
    app2=create_app(path,demo_mode=True,worker_enabled=True)
    with TestClient(app2,base_url=ORIGIN) as c:
        login(c)
        deadline=time.monotonic()+5
        # Test-only bounded readiness wait; production worker is event-driven.
        while time.monotonic()<deadline:
            job=c.get('/api/jobs/'+j).json()
            if job['stage'] in ('PASS','NEEDS_CHANGES'):break
            time.sleep(.02)
        assert job['stage']=='PASS'
        assert any(e['stage']=='RECOVERED' for e in job['events'])
        assert job['attempts']==1
        assert enqueue(c,d)==j

def test_export_is_complete_reproducible_source(app,client,payload,tmp_path):
    login(client);d=create(client,payload);j=enqueue(client,d);expected=finish(app,client,j)['result']
    response=client.get(f"/api/agents/{d['agent_id']}/export")
    assert response.status_code==200
    z=zipfile.ZipFile(io.BytesIO(response.content))
    assert set(z.namelist())=={'definition.json','prompt.txt','dataset.json','rubric.json','foundation-manifest.json','config.json','harness.py','run.py','requirements.txt','uv.lock','README.md'}
    z.extractall(tmp_path/'export')
    run=subprocess.run([sys.executable,'run.py'],cwd=tmp_path/'export',capture_output=True,text=True,check=True)
    actual=json.loads(run.stdout)
    assert actual['score']==expected['score'] and actual['cases']==expected['cases']
    assert json.loads(z.read('definition.json'))['digest']==d['digest']
    assert 'credentials' not in json.loads(z.read('config.json'))

@pytest.mark.parametrize('bad', ['url','duplicate','too-many','huge-term','code'])
def test_safe_dataset_validation(client,payload,bad):
    login(client)
    if bad=='url':payload['source']='http://169.254.169.254/'
    elif bad=='duplicate':payload['dataset'].append(payload['dataset'][0])
    elif bad=='too-many':payload['dataset']=[{**payload['dataset'][0],'id':str(i)} for i in range(21)]
    elif bad=='huge-term':payload['dataset'][0]['required_terms']=['x'*101]
    elif bad=='code':payload['dataset'][0]['exec']='print(1)'
    assert client.post('/api/agents',json=payload).status_code==422

def test_capability_request_admin_approve_and_revoke(client):
    login(client)
    r=client.post('/api/requests',json={'component_id':'restricted-insights','reason':'Need synthetic strategy evidence'})
    assert r.status_code==201;request_id=r.json()['id']
    assert client.post('/api/requests',json={'component_id':'restricted-insights','reason':'Need it again'}).status_code==409
    assert client.post(f'/api/admin/requests/{request_id}/decision',json={'approve':True,'reason':'I approve myself'}).status_code==403
    login(client,'sam');assert client.get('/api/requests').json()==[]
    login(client,'admin')
    assert client.post(f'/api/admin/requests/{request_id}/decision',json={'approve':True,'reason':'Approved synthetic collection'}).status_code==200
    assert client.post(f'/api/admin/requests/{request_id}/decision',json={'approve':True,'reason':'Duplicate decision'}).status_code==409
    login(client)
    assert client.get('/api/requests').json()[0]['status']=='APPROVED'
    assert 'restricted-insights' in {t['id'] for t in client.get('/api/build-options?foundation_id=research').json()['choices']['tools']}
    assert 'restricted-insights' not in {t['id'] for t in client.get('/api/build-options?foundation_id=knowledge').json()['choices']['tools']}

def test_rejection_reason_and_external_data_policy(client):
    # Tightened catalog contract: forbidden metadata/request IDs are not discoverable.
    login(client,'sam')
    assert client.post('/api/requests',json={'component_id':'external-gemini','reason':'Try external model route'}).status_code == 404
    assert not any(c['id']=='external-gemini' for c in client.get('/api/capabilities').json())
    r=client.post('/api/requests',json={'component_id':'restricted-insights','reason':'Review strategy insights'}).json()
    login(client,'admin')
    path='/api/admin/requests/'+r['id']+'/decision'
    assert client.post('/api/admin/grants',json={'persona_id':'sam','component_id':'external-gemini','enabled':True}).status_code in (403,404)
    assert client.post(path,json={'approve':False,'reason':'Workspace forbids this capability'}).status_code==200
    login(client,'sam');assert 'forbids' in client.get('/api/requests').json()[0]['decision']

def test_catalog_versioning_and_policy_minimum(app,client,payload):
    login(client);d=create(client,payload)
    login(client,'admin')
    r=client.post('/api/admin/catalog/components/bedrock-claude',json={'approved':False});assert r.json()['version']=='2'
    r=client.post('/api/admin/catalog/components/bedrock-claude',json={'approved':True});assert r.json()['version']=='3'
    assert len(client.get('/api/admin/catalog').json()['history'])==2
    login(client)
    assert client.post(f"/api/agents/{d['agent_id']}/deploy-test",json={'version':1,'idempotency_key':'old-version-test'}).status_code==409

def test_admin_cannot_read_private_data_in_audit(client,payload):
    login(client);d=create(client,payload)
    login(client,'admin');text=client.get('/api/admin/audit').text
    assert payload['prompt'] not in text and 'payroll' not in text
    assert 'definition_created' in text

@pytest.mark.parametrize('variable,value', [('DEMO_MODE','0'),('EXECUTION_MODE','aws'),('HOST','0.0.0.0')])
def test_fail_closed_startup(tmp_path,monkeypatch,variable,value):
    monkeypatch.setenv('DEMO_MODE','1');monkeypatch.setenv(variable,value)
    with pytest.raises(RuntimeError):create_app(str(tmp_path/'blocked.sqlite'))

def test_nonlocal_public_url_refused(tmp_path):
    with pytest.raises(RuntimeError):create_app(str(tmp_path/'blocked.sqlite'),demo_mode=True,public_url='https://example.com')

def test_foundation_revoke_prevents_deployment(client,payload):
    login(client);d=create(client,payload)
    login(client,'admin')
    response=client.post('/api/admin/catalog/foundations/research',json={'approved':False})
    assert response.status_code==200 and response.json()['version']=='1.0.1'
    login(client)
    assert 'research' not in {f['id'] for f in client.get('/api/build-options').json()['foundations']}
    assert client.post(f"/api/agents/{d['agent_id']}/deploy-test",json={'version':1,'idempotency_key':'revoked-foundation'}).status_code==403

def test_queue_cap_and_distinct_agents(client,payload):
    login(client)
    for i in range(8):
        d=create(client,{**payload,'name':f'Queued agent {i}'})
        enqueue(client,d,f'queue-budget-{i}')
    d=create(client,payload)
    assert client.post(f"/api/agents/{d['agent_id']}/deploy-test",json={'version':1,'idempotency_key':'queue-overflow-1'}).status_code==429

def test_grants_do_not_reseed_on_restart(tmp_path):
    path=str(tmp_path/'grants.sqlite')
    with TestClient(create_app(path,demo_mode=True,worker_enabled=False),base_url=ORIGIN) as c:
        login(c,'admin');c.post('/api/admin/grants',json={'persona_id':'alex','component_id':'synthetic-search','enabled':False})
    with TestClient(create_app(path,demo_mode=True,worker_enabled=False),base_url=ORIGIN) as c:
        login(c)
        assert c.get('/api/build-options?foundation_id=research').json()['choices']['tools']==[]

def test_two_async_agents_execute_and_remain_isolated(tmp_path,payload):
    app=create_app(str(tmp_path/'two-agents.sqlite'),demo_mode=True,worker_enabled=True)
    with TestClient(app,base_url=ORIGIN) as c:
        login(c)
        d1=create(c,payload);j1=enqueue(c,d1)
        failed={**payload,'name':'Independent failing agent','prompt':payload['prompt']+' No citations.'}
        d2=create(c,failed);j2=enqueue(c,d2)
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            jobs=[c.get('/api/jobs/'+j).json() for j in (j1,j2)]
            if all(j['stage'] in ('PASS','NEEDS_CHANGES') for j in jobs):break
            time.sleep(.02)
        assert [j['stage'] for j in jobs]==['PASS','NEEDS_CHANGES']
        assert jobs[0]['result']['definition_digest']==d1['digest']
        assert jobs[1]['result']['definition_digest']==d2['digest']
