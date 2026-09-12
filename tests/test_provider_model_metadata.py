"""Synthetic offline tests. No AWS clients, credentials or inference."""
import copy
import time
from unittest.mock import Mock
import pytest
from fastapi import HTTPException
from backend.live_catalog import configured_catalog, LiveCatalog, Sources, projection, revision
from backend.provider_model_metadata import (NATIVE_MODEL, POLICY, EXECUTION, CARD, PROVENANCE,
    ProviderModelMetadata, normalize_foundation, record_id, source_revision)
from backend.builder_catalog import binding, choices, assess

ACCOUNT = '9988' '77665544'


def approve(source):
    digest = source_revision(source)
    source['exposure'] = {record_id(source): {**source['scope'], 'approved': True,
        'version': digest, 'approval_sha256': digest, 'digest_format': 'native-catalog-v2'}}


def sample():
    model = normalize_foundation({'modelId': NATIVE_MODEL['native_model_id'],
        'modelName': 'Claude Haiku 4.5', 'providerName': 'Anthropic',
        'modelLifecycle': {'status': 'ACTIVE'}, 'inputModalities': ['TEXT', 'IMAGE'],
        'outputModalities': ['TEXT'], 'responseStreamingSupported': True,
        'inferenceTypesSupported': ['INFERENCE_PROFILE']})
    source = {'schema_version': 3, 'source_id': 'haiku-documentation', 'approved': True,
        'region': 'us-west-2', 'policy': copy.deepcopy(POLICY),
        'execution_binding': dict(EXECUTION),
        'scope': {'workspaces': ['research'], 'requestable': False,
                  'owner': 'Platform owner', 'data_handling': 'Documentation only'},
        'snapshot': {'native_model': copy.deepcopy(NATIVE_MODEL), 'provenance': PROVENANCE,
            'control_plane_api': 'bedrock:GetFoundationModel', 'request_id': 'synthetic-request',
            'document_url': CARD, 'document_sha256': 'a'*64, 'foundation_sha256': revision(model),
            'retrieved_at': time.time()-5, 'expires_at': time.time()+300, 'foundation_model': model}}
    approve(source)
    return source


def provider(source):
    return ProviderModelMetadata(Mock(), source, ACCOUNT, 'us-west-2')


def test_documentation_has_native_identity_but_no_route_or_permissions():
    p = provider(sample()); row = p.records()[0]
    p.client.assert_not_called()
    assert not p.client.mock_calls
    assert row['native_model_id'] == NATIVE_MODEL['native_model_id']
    assert 'model_id' not in row and 'target_id' not in row
    assert row['supported_apis'] == ['Messages', 'Converse', 'Invoke']
    assert row['execution_binding'] == EXECUTION
    assert row['entitlement'] == 'unverified'
    assert not row['execution_ready'] and not row['integration_ready']
    foundation = {'native_bindings': {'approved': True, 'components': [
        {k: row[k] for k in ('id', 'version', 'source_revision')}]}}
    assert binding(foundation, row) is None  # Even exact owner pins do not create a route.
    db = Mock(); db.select.return_value.fetchone.return_value = None
    persona = {'id': 'test', 'workspace': 'research', 'external_allowed': False}
    public = projection(db, persona, row)
    assert public['documentation_only'] and not public['usable'] and not public['requestable']
    assert public['execution_binding']['status'] == 'NOT_CONFIGURED'
    assert 'snapshot' not in public and 'gateway_arn' not in public
    assert choices(db, persona, foundation, [row])['models'] == []
    assert projection(db, {**persona, 'workspace': 'operations'}, row) is None


@pytest.mark.parametrize('change', [
    lambda s: s.update(approved=False),
    lambda s: s['snapshot'].update(expires_at=time.time()-1),
    lambda s: s['snapshot'].update(expires_at=float('inf')),
    lambda s: s['snapshot']['native_model'].update(native_model_id='anthropic.claude-haiku-4-5'),
    lambda s: s['snapshot']['native_model'].update(supported_apis=['Responses']),
    lambda s: s['snapshot']['native_model'].update(supported_region='us-east-1'),
    lambda s: s['snapshot']['foundation_model'].update(providerName='Google'),
    lambda s: s['snapshot'].update(document_sha256='b'*64),
    lambda s: s['policy'].update(approved_provider_api='bedrock-mantle'),
    lambda s: s['policy'].update(allow_execution=True),
    lambda s: s['execution_binding'].update(status='verified'),
    lambda s: s.update(endpoint='https://bedrock-runtime.us-west-2.amazonaws.com'),
    lambda s: s['scope'].update(requestable=True),
])
def test_fail_closed(change):
    source = sample(); change(source)
    with pytest.raises(ValueError): provider(source).records()


@pytest.mark.parametrize('legacy_field,value', [
    ('gateway_id', 'test'), ('target_id', 'target'),
    ('target_revision', 'a'*64), ('gateway_arn', 'synthetic-arn')])
def test_old_route_not_accepted_even_after_rehash(legacy_field, value):
    source = sample(); source[legacy_field] = value; approve(source)
    with pytest.raises(ValueError, match='Legacy route snapshot rejected'):
        provider(source).records()


def test_old_mantle_mapping_rejected_even_with_new_schema_and_approval():
    source = sample()
    source['snapshot']['mapping'] = {'mantle_model_id': 'anthropic.claude-haiku-4-5',
                                    'provider_path': '/anthropic/v1/messages'}
    approve(source)
    with pytest.raises(ValueError, match='Legacy route snapshot rejected'):
        provider(source).records()


@pytest.mark.parametrize('change', [
    lambda s: s['policy'].update(approved_provider_api='bedrock-mantle'),
    lambda s: s['policy'].update(allow_execution=True),
    lambda s: s['execution_binding'].update(status='verified'),
    lambda s: s['snapshot']['native_model'].update(native_model_id='anthropic.claude-haiku-4-5'),
    lambda s: s['snapshot']['native_model'].update(regional_inference=['IN_REGION']),
])
def test_reapproval_cannot_create_execution_or_change_native_contract(change):
    source = sample(); change(source); approve(source)
    with pytest.raises(ValueError): provider(source).records()


def test_configured_documentation_constructs_no_provider_client():
    source = sample()
    config = {'schema_version': 2, 'approved': True, 'binding': {
        'expected_account': ACCOUNT, 'region': 'us-west-2', 'owner_approval': 'synthetic'},
        'provider_metadata': [source]}
    session = Mock(region_name='us-west-2')
    session.client.return_value.get_caller_identity.return_value = {'Account': ACCOUNT}
    factory = Mock()
    catalog = configured_catalog(config, session=session, client_factory=factory)
    assert catalog.records()[0]['documentation_only']
    factory.assert_not_called()
    assert session.client.call_args.args == ('sts',)


def test_expiry_not_hidden_by_catalog_cache():
    source = sample(); catalog = LiveCatalog(Sources([]), Sources([]), 60, Sources([provider(source)]))
    assert catalog.records()
    source['snapshot']['expires_at'] = time.time()-1
    with pytest.raises(HTTPException): catalog.records()
    assert catalog._snapshot == []


def test_legacy_configuration_rejected_before_any_sdk_access():
    source = sample(); source.pop('schema_version')
    config = {'schema_version': 2, 'approved': True, 'binding': {
        'expected_account': ACCOUNT, 'region': 'us-west-2', 'owner_approval': 'synthetic'},
        'provider_metadata': [source]}
    session, factory = Mock(), Mock()
    with pytest.raises(ValueError, match='Legacy route snapshot rejected'):
        configured_catalog(config, session=session, client_factory=factory)
    assert not session.mock_calls and not factory.mock_calls


def test_documentation_cannot_be_selected_even_with_exact_foundation_binding(payload):
    row = provider(sample()).records()[0]
    db = Mock(); db.select.return_value.fetchone.return_value = None
    persona = {'id': 'test', 'workspace': 'research'}
    foundation = {'approved': True, 'version': payload['foundation_version'],
        'native_bindings': {'approved': True, 'components': [
            {k: row[k] for k in ('id', 'version', 'source_revision')}]}}
    payload.update(model_id=row['id'], tools=[], skills=[], component_versions={row['id']: row['version']})
    with pytest.raises(HTTPException, match='not a selectable execution route'):
        assess(db, persona, payload, foundation, [row])
    result = assess(db, persona, payload, foundation, [row], previous=payload, deployment_issues=[])
    assert not result['deployable']
    assert {'model_documentation_only', 'execution_not_ready'} <= {x['code'] for x in result['issues']}
