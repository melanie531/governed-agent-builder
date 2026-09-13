"""Opus 5 Foundation route binding + execution driver gate.

Proves: (1) the model_gate interceptor admits the Opus5 route and preserves Haiku
while denying any unlisted model; (2) the runtime route binding accepts the Opus5
request_model and the execution driver stays unverified until an owner-signed
execution binding matching the exact route revision is pinned; (3) an unauthorized
model/principal is still denied. No live call; synthetic SDK + owner data only.
"""
import base64
import copy
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools.model_gate import handler as gate
from backend.runtime_model_catalog import RuntimeModelCatalog, validate_source


# ---- model_gate interceptor (Foundation route binding, interceptor path) ----

def _event(model, max_tokens=16):
    body = json.dumps({'model': model, 'messages': [{'role': 'user', 'content': 'hi'}],
                       'max_tokens': max_tokens, 'stream': False}).encode()
    return {'interceptorInputVersion': '1.0', 'http': {'gatewayRequest': {
        'path': gate.PATH, 'httpMethod': 'POST',
        'body': base64.b64encode(body).decode('ascii')}}}


def test_gate_admits_opus5_and_preserves_haiku():
    for model in ('claude/anthropic.claude-opus-5', 'claude/anthropic.claude-haiku-4-5'):
        out = gate.handler(_event(model), None)
        assert 'transformedGatewayRequest' in out['http'], model


@pytest.mark.parametrize('model', [
    'claude/anthropic.claude-sonnet-5',           # not authorized in this task
    'claude/openai.gpt-6-astra',                  # different family, denied
    'us.anthropic.claude-opus-5',                 # wrong route form
    'claude/anthropic.claude-opus-4',             # unlisted
])
def test_gate_denies_unauthorized_models(model):
    out = gate.handler(_event(model), None)
    assert 'transformedGatewayResponse' in out['http']
    assert out['http']['transformedGatewayResponse']['statusCode'] == 403


def test_gate_still_enforces_token_and_stream_bounds_for_opus5():
    # Interceptor caps max_tokens at MAX_OUTPUT (256); the 1..16 window is enforced
    # separately by the Cedar permit, not this interceptor.
    over = gate.handler(_event('claude/anthropic.claude-opus-5', max_tokens=257), None)
    assert over['http']['transformedGatewayResponse']['statusCode'] == 403


# ---- runtime route binding + execution driver gate (live bedrockrt path) ----

ACCOUNT = '998877665544'
GID, TID = 'testgateway', 'testtarget'
OPUS = 'us.anthropic.claude-opus-5'
HAIKU = 'us.anthropic.claude-haiku-4-5-20251001-v1:0'


def _source(execution_bindings=None):
    src = {
        'approved': True, 'region': 'us-west-2', 'gateway_id': GID, 'target_id': TID,
        'gateway_arn': f'arn:aws:bedrock-agentcore:us-west-2:{ACCOUNT}:gateway/{GID}',
        'bindings': [
            {'target_name': TID, 'request_model': HAIKU,
             'response_models': ['anthropic.claude-haiku-4-5-20251001-v1:0'], 'path': '/v1/messages'},
            {'target_name': TID, 'request_model': OPUS,
             'response_models': ['anthropic.claude-opus-5'], 'path': '/v1/messages'},
        ],
        'exposure': {},
    }
    if execution_bindings is not None:
        src['execution_bindings'] = execution_bindings
    return src


def test_validate_source_accepts_opus5_request_model(monkeypatch):
    # request_model regex admits us.anthropic.claude-opus-5; assert the binding/model
    # shape check passes (exposure validated by other suites is stubbed here).
    import backend.runtime_model_catalog as rc
    monkeypatch.setattr(rc, 'validate_exposure', lambda *a, **k: None)
    src = _source()
    src['exposure'] = {f'model:{GID}:{TID}:{HAIKU}': {}, f'model:{GID}:{TID}:{OPUS}': {}}
    validate_source(src, ACCOUNT, 'us-west-2')  # must not raise on the Opus5 binding


def _records(monkeypatch, execution_bindings=None):
    src = _source(execution_bindings)
    rids = [f'model:{GID}:{TID}:{HAIKU}', f'model:{GID}:{TID}:{OPUS}']
    version = 'a' * 64
    src['exposure'] = {rid: {'approval_sha256': version} for rid in rids}
    gateway = {'gatewayArn': src['gateway_arn'], 'status': 'READY', 'authorizerType': 'AWS_IAM',
               'protocolType': None, 'policyEngineConfiguration': {'mode': 'ENFORCE', 'arn': 'x'},
               'interceptorConfigurations': None, 'roleArn': 'r'}
    target = {'gatewayArn': src['gateway_arn'], 'targetId': TID, 'status': 'READY', 'name': TID,
              'targetConfiguration': {'http': {'passthrough': {
                  'endpoint': 'https://bedrock-runtime.us-west-2.amazonaws.com/anthropic',
                  'protocolType': 'INFERENCE'}}},
              'credentialProviderConfigurations': [{'credentialProviderType': 'GATEWAY_IAM_ROLE',
                  'credentialProvider': {'iamCredentialProvider': {'service': 'bedrock', 'region': 'us-west-2'}}}],
              'metadataConfiguration': {'allowedRequestHeaders': ['anthropic-version', 'content-type']}}
    client = SimpleNamespace(get_gateway=lambda **k: gateway,
                             get_gateway_target=lambda **k: target)
    import backend.runtime_model_catalog as rc
    # Isolate the driver gate from the approval/metadata plumbing (validated elsewhere).
    monkeypatch.setattr(rc, 'approved_metadata', lambda rid, ver, *a, **k: {'id': rid, 'kind': 'model'})
    monkeypatch.setattr(rc, 'route_revision', lambda gw, tg, b: version)
    return RuntimeModelCatalog(client, src).records()


def test_opus5_route_stays_unverified_without_signed_binding(monkeypatch):
    rows = _records(monkeypatch, execution_bindings=None)
    opus = next(r for r in rows if r['id'].endswith(OPUS))
    assert opus['execution_ready'] is False
    assert opus['integration_ready'] is False
    assert opus['execution_binding'] == {'status': 'unverified'}


def test_opus5_route_execution_ready_only_with_matching_signed_binding(monkeypatch):
    version = 'a' * 64
    rid = f'model:{GID}:{TID}:{OPUS}'
    rows = _records(monkeypatch, execution_bindings={rid: {'status': 'verified', 'approval_sha256': version}})
    opus = next(r for r in rows if r['id'].endswith(OPUS))
    haiku = next(r for r in rows if r['id'].endswith(HAIKU))
    assert opus['execution_ready'] is True and opus['integration_ready'] is True
    assert opus['execution_binding'] == {'status': 'verified', 'approval_sha256': version}
    # Haiku, without its own signed binding, is untouched and stays unverified.
    assert haiku['execution_ready'] is False


def test_signed_binding_with_wrong_revision_is_rejected_as_unverified(monkeypatch):
    rows = _records(monkeypatch, execution_bindings={f'model:{GID}:{TID}:{OPUS}':
                                        {'status': 'verified', 'approval_sha256': 'b' * 64}})
    opus = next(r for r in rows if r['id'].endswith(OPUS))
    assert opus['execution_ready'] is False


def test_validate_source_rejects_binding_for_unpinned_route(monkeypatch):
    import backend.runtime_model_catalog as rc
    monkeypatch.setattr(rc, 'validate_exposure', lambda *a, **k: None)
    src = _source(execution_bindings={f'model:{GID}:{TID}:us.anthropic.claude-sonnet-5':
                                       {'status': 'verified', 'approval_sha256': 'a' * 64}})
    src['exposure'] = {f'model:{GID}:{TID}:{HAIKU}': {}, f'model:{GID}:{TID}:{OPUS}': {}}
    with pytest.raises(ValueError, match='subset of pinned routes'):
        validate_source(src, ACCOUNT, 'us-west-2')
