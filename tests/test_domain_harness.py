import copy
import hashlib
import json
from pathlib import Path
import socket
import pytest
from pydantic import ValidationError
from backend.domain_harness import AdmissionDenied, compile_plan, digest
from backend.domain_harness_schema import (
    AuthorizationContext, Catalogs, DomainHarnessDefinition, FoundationLibrary,
)
from backend.domain_harness_cli import main
from backend.native_harness_contract import validate_wire_shape

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / 'examples/domain_harness'


def load(tag='a'):
    return {key: json.loads((EXAMPLES / (name + '.json')).read_text()) for key, name in
            [('domain', 'domain-' + tag), ('authorization', 'authorization-' + tag),
             ('foundations', 'foundations'), ('catalogs', 'catalogs')]}


def compile_data(data):
    return compile_plan(DomainHarnessDefinition.model_validate(data['domain']),
                        foundations=FoundationLibrary.model_validate(data['foundations']),
                        authorization=AuthorizationContext.model_validate(data['authorization']),
                        catalogs=Catalogs.model_validate(data['catalogs']))


def reapprove_domain(data):
    data['authorization']['approved_domain_digest'] = digest(DomainHarnessDefinition.model_validate(data['domain']))


def regrant(data, kind, entry):
    # Simulate trusted administrator approval, NOT a business request override.
    for grant in data['authorization']['grants']:
        if grant['kind'] == kind and grant['ref'] == entry['ref']:
            grant['entry_digest'] = digest(entry)


def test_same_foundation_two_different_compositions_no_execution(monkeypatch):
    def forbidden(*a, **kw):
        raise AssertionError('No network allowed')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    a, b = compile_data(load()), compile_data(load('b'))
    assert a.foundation == b.foundation
    assert a.foundation.source_digest == hashlib.sha256((ROOT / 'backend/domain_harness.py').read_bytes()).hexdigest()
    assert a.version_digest != b.version_digest
    assert a.model_route.protocol == 'messages'
    assert b.model_route.provider == 'bedrock-openai'
    assert a.allowed_tools == ('@approved_tools/browser_read',)
    assert b.allowed_tools == ('@approved_tools/policy_read',)
    assert not any(t.capability == 'browser' for t in b.tool_bindings)
    assert a.skills != b.skills and a.domain.system_prompt != b.domain.system_prompt
    assert a.dataset != b.dataset and a.rubric != b.rubric
    for plan in (a, b):
        assert not plan.execution_ready
        assert 'BLOCKED_MODEL_GATEWAY_AUTH' in plan.readiness
        assert 'BLOCKED_NATIVE_SKILL_LOADING' in plan.readiness
        assert plan == compile_data(load('a' if plan == a else 'b'))
    assert 'BLOCKED_BROWSER_GATEWAY_ADAPTER' in a.readiness


def test_frozen_deep_immutable_and_hash_order():
    data = load()
    plan = compile_data(data)
    with pytest.raises(ValidationError):
        plan.domain.system_prompt = 'changed'
    with pytest.raises(ValidationError):
        plan.model_route.target_model = 'changed'
    data['domain']['system_prompt'] = 'changed'
    assert plan.domain.system_prompt != 'changed'
    assert digest({'a': 1, 'b': 2}) == digest({'b': 2, 'a': 1})


@pytest.mark.parametrize('field,value', [
    ('executionRoleArn', 'forged'), ('endpoint', 'https://invalid.example'),
    ('headers', {'Authorization': 'synthetic'}), ('secrets', 'synthetic'),
    ('allowedTools', ['*']), ('role', 'admin'), ('additionalParams', {}),
    ('native_config', {}), ('actorId', 'other'), ('runtimeSessionId', 'other'),
])
def test_deny_client_overrides(field, value):
    data = load(); data['domain'][field] = value
    with pytest.raises(ValidationError):
        compile_data(data)


@pytest.mark.parametrize('mutation', ['owner', 'workspace', 'epoch', 'grant', 'prompt', 'version', 'catalog_workspace', 'route_revoked', 'route_stale'])
def test_authority_fail_closed(mutation):
    data = load()
    if mutation in ('owner', 'workspace'):
        data['domain'][mutation] = 'other'
    elif mutation == 'epoch':
        data['authorization']['epoch'] = 2
    elif mutation == 'grant':
        data['authorization']['grants'] = []
    elif mutation == 'prompt':
        data['domain']['system_prompt'] += ' changed'
    elif mutation == 'version':
        data['domain']['model_route']['version'] = 2; reapprove_domain(data)
    elif mutation == 'catalog_workspace':
        data['catalogs']['model_routes'][0]['workspace'] = 'other'
    elif mutation == 'route_revoked':
        data['catalogs']['model_routes'][0]['approved'] = False
    elif mutation == 'route_stale':
        data['catalogs']['model_routes'][0]['target_model'] = 'changed'
    with pytest.raises(AdmissionDenied):
        compile_data(data)


@pytest.mark.parametrize('operation', ['*', 'browser_*', 'shell', 'file_operations', 'InvokeAgentRuntimeCommand', '@raw/browser', 'read/path'])
def test_no_tool_wildcards_or_command(operation):
    data = load(); data['catalogs']['tool_bindings'][0]['operation'] = operation
    with pytest.raises(ValidationError):
        compile_data(data)


@pytest.mark.parametrize('group,kind', [('skills','skill'), ('datasets','dataset'), ('rubrics','rubric')])
def test_mutable_content_denied(group, kind):
    data = load(); entry = data['catalogs'][group][0]
    entry['source_ref'] = 's3://example/skills/latest/'
    entry['immutable_approved'] = False; regrant(data,kind,entry)
    with pytest.raises(AdmissionDenied, match='MUTABLE_CONTENT_NOT_APPROVED'):
        compile_data(data)


def test_skill_digest_change_requires_regrant():
    data = load(); data['catalogs']['skills'][0]['content_digest'] = '0' * 64
    with pytest.raises(AdmissionDenied, match='STALE_GRANT'):
        compile_data(data)


def test_missing_browser_binding_stays_blocked():
    data=load(); entry=data['catalogs']['tool_bindings'][0]
    entry['adapter']=None; entry['adapter_verified']=True; regrant(data,'tool',entry)
    assert 'BLOCKED_BROWSER_GATEWAY_ADAPTER' in compile_data(data).readiness


def test_verified_model_adapter_only_clears_auth_gate():
    data=load(); entry=data['catalogs']['model_routes'][0]
    entry.update(adapter={'id':'approved-adapter','version':1}, gateway_auth='native_iam',auth_evidence_digest='a'*64)
    regrant(data,'model',entry)
    plan=compile_data(data)
    assert 'BLOCKED_MODEL_GATEWAY_AUTH' not in plan.readiness
    assert not plan.execution_ready and 'BLOCKED_NATIVE_INTEGRATION' in plan.readiness


def test_limits_and_duplicate_selections():
    data=load(); data['domain']['limits']['max_iterations']=13; reapprove_domain(data)
    with pytest.raises(AdmissionDenied,match='LIMIT_EXCEEDED'): compile_data(data)
    data=load(); data['domain']['tool_bindings'] *= 2; reapprove_domain(data)
    with pytest.raises(AdmissionDenied,match='DUPLICATE_SELECTION'): compile_data(data)


def test_b_cannot_select_browser_without_grant():
    data=load('b'); data['domain']['tool_bindings'].append({'id':'browser-read','version':1}); reapprove_domain(data)
    with pytest.raises(AdmissionDenied,match='MISSING_OR_STALE_GRANT'): compile_data(data)


def test_empty_tools_explicitly_allow_nothing():
    data=load('b'); data['domain']['tool_bindings']=[]; reapprove_domain(data)
    assert compile_data(data).allowed_tools == ()


def test_safe_cli_preview(capsys):
    args=[]
    for k,n in [('domain','domain-a'),('authorization','authorization-a'),('catalogs','catalogs'),('foundations','foundations')]:
        args += ['--'+k,str(EXAMPLES/(n+'.json'))]
    assert main(args)==0
    text=capsys.readouterr().out
    assert not json.loads(text)['execution_ready']
    for secret_field in ('system_prompt','source_ref','target_model','native_config','example-owner'):
        assert secret_field not in text


def test_published_schema_matches_model():
    assert json.loads((ROOT/'docs/domain-harness.schema.json').read_text()) == DomainHarnessDefinition.model_json_schema()


# These are synthetic wire-shape specimens, not emitted plans or approved routes.
# Direct Bedrock is intentionally wire-valid but NOT gateway-compliant.
def wire(operation):
    common={'model':{'bedrockModelConfig':{'modelId':'synthetic-model'}},'allowedTools':[]}
    if operation=='create':
        return dict(common,harnessName='SyntheticHarness',executionRoleArn='synthetic-role-reference')
    if operation=='update':
        return dict(common,harnessId='synthetic-harness',memory={'optionalValue':{'disabled':{}}})
    return dict(common,harnessArn='synthetic-harness-reference',runtimeSessionId='s'*33,
                qualifier='PinnedVersion',messages=[{'role':'user','content':[{'text':'Synthetic input'}]}])


@pytest.mark.parametrize('operation',['create','update','invoke'])
def test_offline_native_distinct_shapes(operation):
    result=validate_wire_shape(operation,wire(operation))
    assert result['wire_shape_valid'] and not result['execution_ready']
    payload=wire(operation); payload.pop('model')
    with pytest.raises(ValueError,match='EXPLICIT_MODEL'): validate_wire_shape(operation,payload)


@pytest.mark.parametrize('field,value',[('memory',{'disabled':{}}),('executionRoleArn','forged'),('environment',{})])
def test_invoke_rejects_control_fields(field,value):
    payload=wire('invoke');payload[field]=value
    with pytest.raises(ValueError,match='WIRE_SHAPE'): validate_wire_shape('invoke',payload)


def test_update_requires_optional_wrapper():
    payload=wire('update');payload['memory']={'disabled':{}}
    with pytest.raises(ValueError,match='WIRE_SHAPE'): validate_wire_shape('update',payload)


def test_invoke_endpoint_not_numeric_and_session_bounded():
    payload=wire('invoke');payload['qualifier']='1'
    with pytest.raises(ValueError,match='PINNED_ENDPOINT'): validate_wire_shape('invoke',payload)
    payload=wire('invoke');payload['runtimeSessionId']='short'
    with pytest.raises(ValueError): validate_wire_shape('invoke',payload)


def test_native_model_union_and_wildcard_rejected():
    payload=wire('create')
    payload['model']['liteLlmModelConfig']={'modelId':'synthetic-other'}
    with pytest.raises(ValueError,match='EXACTLY_ONE_MODEL'): validate_wire_shape('create',payload)
    payload=wire('create');payload['allowedTools']=['@approved_tools/*']
    with pytest.raises(ValueError,match='FORBIDDEN_TOOL'): validate_wire_shape('create',payload)
