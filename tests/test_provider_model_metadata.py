"""Synthetic tests only; no AWS calls or fixture fallback in production."""
import copy
import time
from unittest.mock import Mock
import pytest
from fastapi import HTTPException
from backend.live_catalog import configured_catalog, target_revision, LiveCatalog, Sources, projection
from backend.provider_model_metadata import (MAPPING, CARD, PROVENANCE, ProviderModelMetadata,
    normalize_foundation, record_id, source_revision, validate_source)
from backend.builder_catalog import binding, choices

ACCOUNT = '9988' '77665544'

def sample():
    target = {'gatewayArn': f'arn:aws:bedrock-agentcore:us-west-2:{ACCOUNT}:gateway/test',
              'targetId': 'target', 'name': 'claude', 'status': 'READY',
              'targetConfiguration': {'inference': {'provider': {
                  'endpoint': 'https://bedrock-mantle.us-west-2.api.aws',
                  'modelMapping': {'providerPrefix': {'strip': True, 'separator': '.'}},
                  'operations': [{'path': '/v1/messages', 'providerPath': '/anthropic/v1/messages',
                                  'models': [{'model': MAPPING['mantle_model_id']}]}]}}}}
    scope = {'workspaces': ['research'], 'requestable': False, 'owner': 'Platform owner', 'data_handling': 'Metadata only'}
    source = {'approved': True, 'region': 'us-west-2', 'gateway_id': 'test', 'gateway_arn': target['gatewayArn'],
              'target_id': 'target', 'target_revision': target_revision(target), 'scope': scope,
              'snapshot': {'mapping': copy.deepcopy(MAPPING), 'provenance': PROVENANCE,
                  'control_plane_api': 'bedrock:GetFoundationModel', 'request_id': 'synthetic-request',
                  'document_url': CARD, 'document_sha256': 'a'*64, 'retrieved_at': time.time()-5,
                  'expires_at': time.time()+300, 'foundation_model': {
                      'modelId': MAPPING['bedrock_model_id'], 'modelName': 'Claude Haiku 4.5',
                      'providerName': 'Anthropic', 'lifecycle': 'ACTIVE',
                      'inputModalities': ['TEXT', 'IMAGE'], 'outputModalities': ['TEXT']}}}
    digest = source_revision(source)
    source['exposure'] = {record_id(source): {**scope, 'approved': True, 'version': digest,
        'approval_sha256': digest, 'digest_format': 'native-catalog-v2'}}
    return target, source

def provider(target, source):
    return ProviderModelMetadata(Mock(get_gateway_target=Mock(return_value=target)), source, ACCOUNT, 'us-west-2')

def test_normalization_and_foundation_requires_exact_binding():
    target, source = sample(); row = provider(target, source).records()[0]
    assert row['provenance'] == PROVENANCE and row['gateway_enumeration'] == 'NotConnected'
    assert row['entitlement'] == 'unverified' and not row['execution_ready'] and not row['integration_ready']
    assert row['model_id'] == 'claude/anthropic.claude-haiku-4-5'
    assert binding({'native_bindings': {'approved': True, 'components': [{'id': 'bedrock-claude'}]}}, row) == 'binding_missing'
    foundation = {'native_bindings': {'approved': True, 'components': [{k: row[k] for k in ('id','version','source_revision')}]}}
    assert binding(foundation, row) is None
    db = Mock();db.select.return_value.fetchone.return_value = None
    persona = {'id': 'test', 'workspace': 'research', 'external_allowed': False}
    public = projection(db, persona, row)
    assert public['provenance'] == PROVENANCE and not public['usable'] and not public['requestable']
    assert 'gateway_arn' not in public and 'snapshot' not in public
    assert choices(db, persona, foundation, [row])['models'][0]['deployable'] is False
    assert projection(db, {**persona, 'workspace': 'operations'}, row) is None

@pytest.mark.parametrize('change', [
    lambda s: s.update(approved=False),
    lambda s: s['snapshot'].update(expires_at=time.time()-1),
    lambda s: s['snapshot'].update(expires_at=float('inf')),
    lambda s: s['snapshot']['mapping'].update(mantle_model_id='openai.gpt-fake'),
    lambda s: s['snapshot']['mapping'].update(api='Responses'),
    lambda s: s['snapshot']['mapping'].update(region='us-east-1'),
    lambda s: s['snapshot']['foundation_model'].update(providerName='Google'),
    lambda s: s['snapshot'].update(document_sha256='b'*64),
    lambda s: s['exposure'][record_id(s)].update(workspaces=['operations']),
    lambda s: s['exposure'].update({'provider-model:test:target:arbitrary': {}}),
])
def test_fail_closed(change):
    target, source = sample();change(source)
    with pytest.raises(ValueError):provider(target, source).records()

@pytest.mark.parametrize('field,value', [('name','other'),('status','FAILED'),('targetId','other'),('gatewayArn','other')])
def test_target_drift(field,value):
    target, source = sample();target[field]=value
    with pytest.raises(ValueError):provider(target,source).records()

def test_unsupported_route_even_if_reapproved():
    target, source = sample();target['targetConfiguration']['inference']['provider']['operations'][0]['models'][0]['model']='*'
    source['target_revision']=target_revision(target);digest=source_revision(source)
    source['exposure'][record_id(source)].update(version=digest,approval_sha256=digest)
    with pytest.raises(ValueError):provider(target, source).records()

def test_expiry_and_drift_not_hidden_by_catalog_cache():
    target, source = sample();p=provider(target,source);catalog=LiveCatalog(Sources([]),Sources([]),60,Sources([p]))
    assert catalog.records() and catalog.source_status()['ModelGateway']['connection_state']=='NotConnected'
    source['snapshot']['expires_at']=time.time()-1
    with pytest.raises(HTTPException):catalog.records()
    assert catalog._snapshot == []

def test_config_prevalidates_before_clients_and_sts_account_check():
    target, source=sample()
    config={'schema_version':2,'approved':True,'binding':{'expected_account':ACCOUNT,'region':'us-west-2','owner_approval':'test'},'provider_metadata':[source]}
    session=Mock(region_name='us-west-2');session.client.return_value.get_caller_identity.return_value={'Account':ACCOUNT}
    factory=Mock(return_value=Mock(get_gateway_target=Mock(return_value=target)))
    catalog=configured_catalog(config,session=session,client_factory=factory)
    assert catalog.records()[0]['provenance']==PROVENANCE
    assert factory.call_args.args[0]=='bedrock-agentcore-control'
    bad=copy.deepcopy(config);bad['provider_metadata'][0]['snapshot']['mapping']['api']='Responses'
    session.reset_mock()
    with pytest.raises(ValueError):configured_catalog(bad,session=session,client_factory=factory)
    session.client.assert_not_called()
    session.client.return_value.get_caller_identity.return_value={'Account':'different'}
    with pytest.raises(ValueError,match='account mismatch'):configured_catalog(config,session=session,client_factory=factory)

def test_http_catalog_to_bound_unready_draft(tmp_path, monkeypatch, payload):
    import json
    from fastapi.testclient import TestClient
    from backend.app import create_app
    from .conftest import login, ORIGIN
    monkeypatch.setenv('CATALOG_MODE', 'live')
    target, source = sample();p=provider(target,source)
    catalog=LiveCatalog(Sources([]),Sources([]),0,Sources([p]))
    row=p.records()[0]
    app=create_app(str(tmp_path/'state.sqlite'),demo_mode=True,worker_enabled=False,catalog_provider=catalog)
    with app.state.store.tx() as db:
        f=json.loads(db.select('foundations',columns=['body'],where=[('id','=','research')]).fetchone()[0])
        f['native_bindings']={'approved':True,'components':[{k:row[k] for k in ('id','version','source_revision')}]}
        db.update('foundations',{'body':json.dumps(f)},where=[('id','=','research')])
    with TestClient(app,base_url=ORIGIN) as client:
        login(client)
        result=client.get('/api/catalog').json()
        assert result['sources']['ModelGateway']['connection_state']=='NotConnected'
        assert result['items'][0]['provenance']==PROVENANCE
        options=client.get('/api/build-options',params={'foundation_id':'research'}).json()
        assert options['choices']['models'][0]['draft_selectable']
        payload.update(model_id=row['id'],tools=[],skills=[],component_versions={row['id']:row['version']})
        saved=client.post('/api/agents',json=payload)
        assert saved.status_code==201,saved.text
        assert not saved.json()['readiness']['deployable']
        assert 'execution_not_ready' in {x['code'] for x in saved.json()['readiness']['issues']}
        with app.state.store.tx() as db:
            assert db.select('jobs',count=True).fetchone()[0]==0
            assert db.select('grants',where=[('component','=',row['id'])],count=True).fetchone()[0]==0
