"""Offline Studio registration/admission tests; requires the peer Opus harness contract.

Synthetic response identifiers are never live identity evidence.
"""
import copy
import json
import pytest
from fastapi import HTTPException
from foundation_harness.config import Model, digest, load_config
from tests.test_foundation_executor import config
from tests.test_runtime_pinned_catalog import fixture, Client, ACCOUNT, REGION
from backend.runtime_model_catalog import route_revision, validate_source, foundation_model
from backend.foundation_approval import runtime_platform_model, registered_model

REQUEST = 'us.anthropic.claude-opus-5'
RESPONSE = 'synthetic-opus-response'


def opus_config(verified=True):
    raw = config()
    raw['model'].update(protocol='messages-passthrough', requestModel=REQUEST,
        responseModelAllowlist=[RESPONSE] if verified else [], route=REQUEST,
        endpoint=raw['model']['endpoint'].replace('/inference/', '/bedrockrt/'))
    raw.update(tools=[], allowedTools=[], skills=[])
    raw['limits'].update(maxIterations=1, maxModelCalls=1, maxToolCalls=0,
                         maxOutputTokens=256, timeoutSeconds=60)
    return raw


def route_fixture(verified=True):
    gateway, target, source = fixture()
    gateway['gatewayUrl'] = f"https://{source['gateway_id']}.gateway.bedrock-agentcore.{REGION}.amazonaws.com"
    binding = source['bindings'][0]
    binding.update(request_model=REQUEST, response_models=[RESPONSE] if verified else [],
                   request_contract='opus5-text-v1',
                   response_identity_evidence=digest('synthetic offline response fixture') if verified else None)
    rid = f"model:{source['gateway_id']}:{source['target_id']}:{REQUEST}"
    exposure = next(iter(source['exposure'].values()))
    version = route_revision(gateway, target, binding)
    exposure.update(version=version, approval_sha256=version)
    source['exposure'] = {rid: exposure}
    raw = opus_config(verified)['model']
    raw.update(id=rid, version=version, targetDigest=version,
               endpoint=gateway['gatewayUrl'] + '/bedrockrt/v1/messages')
    return gateway, target, source, Model.model_validate(raw)


def test_exact_config_to_registration_path_without_list_targets(monkeypatch):
    verified = True
    g, t, s, model = route_fixture(verified)
    validate_source(s, ACCOUNT, REGION)
    client = Client(g, t)  # deliberately no list_gateway_targets method
    config = {'approved': True, 'binding': {'expected_account': ACCOUNT, 'region': REGION},
              'runtime_model_routes': [s]}
    monkeypatch.setenv('NATIVE_CATALOG_CONFIG', json.dumps(config))
    assert runtime_platform_model(client, ACCOUNT, model) == model.model_dump(mode='json')
    assert len(client.calls) == 2
    source = {'config': opus_config(verified), 'platform': {'model': model.model_dump(mode='json')}}
    source['config']['model'] = model.model_dump(mode='json')
    if verified:
        registered_model(source, model.id)
    else:
        with pytest.raises(HTTPException, match='UNVERIFIED_RESPONSE_IDENTITY'):
            registered_model(source, model.id)
    with pytest.raises(HTTPException, match='REGISTERED_MODEL_REQUIRED'):
        registered_model(source, 'dropdown-other-model')


@pytest.mark.parametrize('mutation', [
    lambda g,t,s: g['policyEngineConfiguration'].update(mode='LOG_ONLY'),
    lambda g,t,s: t['metadataConfiguration']['allowedRequestHeaders'].append('new-header'),
    lambda g,t,s: t['targetConfiguration']['http']['passthrough']['schema']['source'].update(inlinePayload='drift'),
    lambda g,t,s: s['bindings'][0].update(response_models=['other-response']),
    lambda g,t,s: s['bindings'][0].update(response_identity_evidence='f'*64),
    lambda g,t,s: t.update(name='other'),
])
def test_runtime_registration_drift_fail_closed(mutation):
    g,t,s,model = route_fixture(); mutation(g,t,s)
    with pytest.raises(ValueError):
        foundation_model(Client(g,t),s,model)


def test_platform_metadata_uses_owned_target_not_legacy_cfn_model_gateway(monkeypatch):
    from backend.foundation_approval import platform_metadata
    g, t, s, model = route_fixture()
    control = Client(g, t)
    class CloudFormation:
        def describe_stacks(self, StackName):
            # No ModelGateway or ToolsGateway output; a no-tools runtime source
            # must not need either legacy gateway or a ListGatewayTargets grant.
            values = ({'ApiEndpoint': 'https://synthetic.execute-api.us-west-2.amazonaws.com'}
                      if StackName.endswith('serverless-app') else
                      {'FoundationRole': f'arn:aws:iam::{ACCOUNT}:role/synthetic'})
            return {'Stacks': [{'Outputs': [{'OutputKey': k, 'OutputValue': v} for k,v in values.items()]}]}
    class Target:
        account = ACCOUNT
        def __init__(self, session):
            self.verified = False
        def verify(self):
            self.verified = True
        def client(self, name):
            assert self.verified
            return control if name == 'bedrock-agentcore-control' else CloudFormation()
    monkeypatch.setattr('scripts.foundation_target.StudioTarget', Target)
    monkeypatch.setattr('boto3.Session', lambda **kw: None)
    monkeypatch.setenv('NATIVE_CATALOG_CONFIG', json.dumps({'approved': True,
        'binding': {'expected_account': ACCOUNT, 'region': REGION}, 'runtime_model_routes': [s]}))
    raw = opus_config(); raw['model'] = model.model_dump(mode='json')
    result = platform_metadata(raw)
    assert result['model'] == raw['model']
    assert result['role'].endswith(':role/synthetic') and len(control.calls) == 2


def test_saved_immutable_version_uses_registered_source_not_selected_alias(app, client, payload):
    verified = True
    from backend import foundation_runs as runs
    from backend.foundation_approval import RegisterFoundation, register
    from backend.self_service_admission import approve_policy
    from tests.test_foundation_approval import ADMIN, PLATFORM
    from tests.test_self_service_admission import policy_input
    from tests.conftest import login, create
    from scripts.package_foundation import source_digest
    login(client)
    payload = copy.deepcopy(payload)
    payload.update(tools=[], skills=[])
    payload['component_versions'] = {payload['model_id']: payload['component_versions'][payload['model_id']]}
    definition = create(client, payload)
    raw = opus_config(verified)
    raw['foundation']['digest'] = source_digest()
    raw['model'].update(id=definition['model_id'], version=definition['component_versions'][definition['model_id']])
    platform = {**PLATFORM, 'model': load_config(raw, digest(raw)).model.model_dump(mode='json')}
    source = RegisterFoundation(foundation_id='research', config=raw, tool_ids=[],
        reason='Synthetic offline exact Opus source review', expected_revision=0)
    with app.state.store.tx() as db:
        register(db, ADMIN, source, platform)
        approve_policy(db, ADMIN, policy_input(definition))
    result = client.post(f"/api/agents/{definition['agent_id']}/admission", json={'version': 1})
    assert result.status_code == (200 if verified else 409), result.text
    with app.state.store.tx() as db:
        record = runs.get(db, 'foundation-approved:' + definition['digest'])
        if not verified:
            assert result.json()['detail'] == 'UNVERIFIED_RESPONSE_IDENTITY' and record is None
        else:
            assert record['config']['model'] == raw['model']
            assert record['receipt']['provenance'] == 'policy-admission'
            assert record['manifest_digest'] == digest(record['config'])
            assert record['config']['limits']['maxOutputTokens'] == 256
            assert record['admission']['manifest_digest'] == record['manifest_digest']
        assert list(db.select('jobs')) == []


def test_unverified_catalog_binding_cannot_become_a_registered_model():
    g, t, s, model = route_fixture()
    binding = s['bindings'][0]
    binding.update(response_models=[], response_identity_evidence=None)
    version = route_revision(g, t, binding)
    entry = next(iter(s['exposure'].values()))
    entry.update(version=version, approval_sha256=version)
    validate_source(s, ACCOUNT, REGION)  # discoverable configuration, not admission
    with pytest.raises(ValueError, match='VERIFIED_OPUS_RESPONSE_IDENTITY_REQUIRED'):
        foundation_model(Client(g,t), s, model)


def test_product_rejects_smaller_codec_budget():
    raw = opus_config()
    raw['limits']['maxOutputTokens'] = 16
    model = load_config(raw, digest(raw)).model
    with pytest.raises(HTTPException, match='OPUS5_ONE_CALL_TEXT_LIMITS_REQUIRED'):
        registered_model({'config': raw, 'platform': {'model': model.model_dump(mode='json')}}, model.id)
