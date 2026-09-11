"""Offline synthetic workflow tests, never evidence of human/cloud approval."""
import copy
import pytest
from fastapi import HTTPException
from backend.foundation_approval import RegisterFoundation, ApproveFoundation, register, compile_approval
from backend import foundation_runs as runs
from tests.conftest import login, create
from scripts.foundation_probe import example_config
from foundation_harness.config import digest
from tests.test_serverless import cloud, sign_in

PLATFORM = {'endpoint':'https://synthetic.execute-api.us-west-2.amazonaws.com/internal/foundation/exchange',
            'role':'arn:aws:iam::'+'9988'+'77665544'+':role/synthetic'}
ADMIN = {'id':'reviewer', 'role':'admin', 'workspace':'platform'}
OWNER = {'id':'alex','role':'business','workspace':'research','external_allowed':True}


def inputs(definition):
    raw = example_config()
    raw['model']['id'] = definition['model_id']
    raw['skills'][0]['id'] = definition['skills'][0]
    raw['limits']['maxModelCalls'] = 1
    source = RegisterFoundation(foundation_id='research',config=raw,tool_ids=definition['tools'],
        reason='Review synthetic registered source',expected_revision=0)
    review = ApproveFoundation(agent_id=definition['agent_id'],version=1,definition_digest=definition['digest'],
        source_revision=1,expected_revision=0,policy_version=1,epoch=0,request_id='synthetic-request',reason='Review exact immutable synthetic definition')
    return source, review


def test_real_handler_workflow_offline_only(cloud, payload, monkeypatch):
    app, client, _ = cloud
    sign_in(cloud)
    definition = create(client, payload)
    source, review = inputs(definition)
    monkeypatch.setattr('backend.app.platform_metadata', lambda *args: PLATFORM)
    assert client.post('/api/admin/foundation-sources',json=source.model_dump()).status_code == 403
    sign_in(cloud,subject='synthetic-reviewer',group='studio-admin')
    assert client.post('/api/admin/foundation-sources',json=source.model_dump()).status_code == 200
    response = client.post('/api/admin/foundation-approvals',json=review.model_dump())
    assert response.status_code == 200, response.text
    record = response.json()
    assert record['receipt']['approver'] == 'synthetic-reviewer'
    assert record['receipt']['request_id'] == review.request_id
    assert record['receipt']['version'] == 1 and record['receipt']['reviewed_at'] > 0
    assert record['manifest_digest'] == digest(record['config'])
    assert record['admission']['manifest_digest'] == record['manifest_digest']
    assert 'runtime' not in record and 'package_digest' not in record
    with app.state.store.tx() as db:
        assert runs.get(db,'foundation-approved:'+definition['digest']) == record
    assert client.post('/api/admin/foundation-approvals',json=review.model_dump()).status_code == 409
    assert client.post('/api/admin/foundation-approvals',json={**review.model_dump(),'role':'admin'}).status_code == 422


@pytest.mark.parametrize('bad', ['owner','workspace','digest','grant','catalog','source','platform','revision','policy','epoch'])
def test_stale_or_unauthorized_rejected(app, client, payload, bad):
    login(client)
    definition = create(client,payload)
    source, review = inputs(definition)
    with app.state.store.tx() as db:
        register(db,ADMIN,source,PLATFORM)
    with app.state.store.tx() as db:
        actor, owner, platform = copy.deepcopy(ADMIN), copy.deepcopy(OWNER), copy.deepcopy(PLATFORM)
        if bad == 'owner': actor['id'] = owner['id']
        if bad == 'workspace': owner['workspace'] = 'operations'
        if bad == 'digest': review = review.model_copy(update={'definition_digest':'f'*64})
        if bad == 'grant': db.delete('grants',where=[('persona','=','alex')])
        if bad == 'catalog':
            row = runs.get(db,'foundation-source:research'); row['catalog'] = {}; runs.put(db,'foundation-source:research',row)
        if bad == 'source': review = review.model_copy(update={'source_revision':2})
        if bad == 'platform': platform['role'] += '-other'
        if bad == 'policy': runs.put(db,'policy',{'version':2})
        if bad == 'epoch': runs.put(db,'foundation-epoch',1)
        if bad == 'revision': review = review.model_copy(update={'expected_revision':1})
        with pytest.raises(HTTPException): compile_approval(db,actor,review,owner,platform)
        assert runs.get(db,'foundation-approved:'+definition['digest']) is None


def test_source_digest_and_unauthorized_registration(app,client,payload):
    login(client); source, _ = inputs(create(client,payload))
    with app.state.store.tx() as db:
        with pytest.raises(HTTPException): register(db,OWNER,source,PLATFORM)
        source.config['foundation']['digest'] = 'f'*64
        with pytest.raises(HTTPException): register(db,ADMIN,source,PLATFORM)


def test_local_demo_admin_cannot_mint_real_approval(client, payload):
    login(client); source, review = inputs(create(client,payload)); login(client,'admin')
    assert client.post('/api/admin/foundation-sources',json=source.model_dump()).status_code == 403
    assert client.post('/api/admin/foundation-approvals',json=review.model_dump()).status_code == 403


def test_dynamo_cas_race_preserves_single_review(cloud,payload):
    from backend.dynamo_store import DynamoUnit
    app,client,_ = cloud; sign_in(cloud); definition = create(client,payload)
    source,review = inputs(definition)
    owner = {**OWNER,'id':definition['owner']}
    with app.state.store.tx() as db: register(db,ADMIN,source,PLATFORM)
    first, second = DynamoUnit(app.state.store.table), DynamoUnit(app.state.store.table)
    compile_approval(first,ADMIN,review,owner,PLATFORM)
    compile_approval(second,ADMIN,review,owner,PLATFORM)
    first.commit()
    with pytest.raises(HTTPException) as e: second.commit()
    assert e.value.status_code == 409
