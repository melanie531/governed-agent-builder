"""Independent review counterexamples: offline synthetic identities only."""
import copy
import json
import pytest
from fastapi import HTTPException
from backend.live_catalog import (RegistryCatalogProvider, ModelGatewayCatalogProvider,
    LiveCatalog, Sources, configured_catalog, record_revision, model_revision, target_revision,
    visibility, auth_metadata)
from .test_native_catalog import (descriptor_record, expose, Discovery, REGISTRY, REGISTRY_ARN,
    GATEWAY_ARN, ACCOUNT, VerifiedSession, bound, target, model_policy)


def registry(row, policy=None):
    return RegistryCatalogProvider(Discovery([row]), REGISTRY, expose([row]) if policy is None else policy, REGISTRY_ARN)


def config(row):
    return bound({'approved': True, 'registries': [{'approved': True, 'region': 'us-west-2',
        'registry_id': REGISTRY, 'registry_arn': REGISTRY_ARN, 'exposure': expose([row])}]})


def forbidden(*a, **kw):
    raise AssertionError('SDK construction/read forbidden')


@pytest.mark.parametrize('value', [None, '', 'research', [], [None], [''], ['research', 'research']])
def test_f1_invalid_workspace_denied_before_sdk(value):
    c = config(descriptor_record())
    for entry in c['registries'][0]['exposure'].values(): entry['workspaces'] = value
    with pytest.raises(ValueError): configured_catalog(c, forbidden, session=forbidden)
    assert not visibility({'approved': True, 'fixture': False, 'discoverable_workspaces': value},
                          {'workspace': 'unrelated', 'external_allowed': True})


@pytest.mark.parametrize('field,value', [('approved', 'true'), ('version', ''), ('requestable', 1),
    ('owner', None), ('data_handling', ''), ('approval_sha256', 'legacy'), ('digest_format', 'v1')])
def test_f1_bad_exposure_preflight(field, value):
    c = config(descriptor_record())
    next(iter(c['registries'][0]['exposure'].values()))[field] = value
    with pytest.raises(ValueError): configured_catalog(c, forbidden, session=forbidden)


@pytest.mark.parametrize('query', ['', 'x'*257, None, 1])
def test_f2_search_bounds(query):
    p = registry(descriptor_record()); p.client = None
    with pytest.raises(ValueError): p.search(query)


def test_f2_search_exact_limit():
    p = registry(descriptor_record())
    assert p.search('x'*256)


@pytest.mark.parametrize('kind,mutate', [
    ('SKILL', lambda d: d.update(dataSchemaVersion='2099-01-01', data=None)),
    ('SKILL', lambda d: (d.pop('data'), d.update(dataSchemaVersion='2099-01-01'))),
    ('SKILL', lambda d: d.update(data='not-json')),
    ('SKILL', lambda d: d.update(data='{"repository":42}')),
    ('SKILL', lambda d: d.update(data='{"schemaVersion":"2099"}')),
    ('AGENT', lambda d: d.update(data='{"protocolVersion":"0.2.0"}')),
    ('CUSTOM', lambda d: d.update(data='not-json')),
    ('MCP', lambda d: d.update(data='{"name":7,"version":false}')),
])
def test_f3_malformed_supported_descriptor_never_requestable(kind, mutate):
    row = descriptor_record(kind); mutate(next(iter(row['descriptors'].values())))
    result = registry(row).normalize(row)
    assert len(result) == 1 and not result[0]['supported'] and not result[0]['requestable']


def test_f3_markdown_only_and_private_extensions():
    row = descriptor_record('SKILL'); d = row['descriptors']['agentSkillsDefinition']
    d.pop('data'); d.pop('dataSchemaVersion')
    assert registry(row).records()[0]['supported']
    d.update(dataSchemaVersion='0.1.0', data=json.dumps({'extension': {'private': 'do-not-export'}}))
    assert registry(row).records()[0]['supported']
    assert 'do-not-export' not in json.dumps(registry(row).records())
    assert json.loads(d['data'])['extension']['private'] == 'do-not-export'


@pytest.mark.parametrize('field,value', [('displayName', 'Unreviewed'), ('name', 'changed'),
    ('description', 'Unreviewed detail'), ('recordType', 'AGENT')])
def test_f4_same_version_display_mutation_invalidates_all(field, value):
    row = descriptor_record(); policy = expose([row]); row[field] = value
    assert registry(row, policy).records() == []


def test_f4_child_same_parent_digest_and_determinism():
    row = descriptor_record(); policy = expose([row])
    assert record_revision(row) == record_revision(dict(reversed(list(row.items()))))
    child = next(k for k in policy if ':tool:' in k)
    policy[child]['approval_sha256'] = '0'*64
    assert len(registry(row, policy).records()) == 1
    row['descriptors']['mcpServer']['data'] += ' '
    assert registry(row, policy).records() == []


def models():
    detail = target({'connector': {'source': {'connectorId': 'bedrock-mantle'}}})
    model = {'id': 'bedrock-mantle/openai.gpt-test', 'owned_by': 'system'}
    class Client:
        def get_gateway_target(self, **kw): return detail
    p = ModelGatewayCatalogProvider(Client(), 'synthetic-gateway', 'us-west-2',
        model_policy(detail, ['openai.gpt-test']), lambda: {'data': [model]},
        ('synthetic-target',), GATEWAY_ARN)
    return p, detail, model


@pytest.mark.parametrize('field,value', [('name', 'renamed'), ('gatewayArn', GATEWAY_ARN+'-wrong'), ('targetId', 'wrong')])
def test_f5_returned_route_identity(field, value):
    p, detail, model = models(); detail[field] = value
    if field == 'name':
        model['id'] = 'renamed/openai.gpt-test'
        assert p.records() == []
    else:
        with pytest.raises(ValueError): p.records()


def test_f5_auth_metadata_and_enumeration_pins_are_private():
    p, detail, model = models()
    old = target_revision(detail)
    detail['credentialProviderConfigurations'] = [{'credentialProviderType': 'OAUTH',
        'credentialProvider': {'oauthCredentialProvider': {'providerArn': 'synthetic-provider',
          'scopes': ['read'], 'customParameters': {'private': 'synthetic-secret'}, 'accessToken': 'synthetic-secret'}}}]
    assert target_revision(detail) != old
    assert 'synthetic-secret' not in json.dumps(auth_metadata(detail))
    assert p.records() == []
    p.exposure = model_policy(detail, ['openai.gpt-test'])
    assert p.records() and 'synthetic-secret' not in json.dumps(p.records())
    model['created'] = 1
    assert p.records() == []


@pytest.mark.parametrize('response', [{}, {'error': 'Denied'}, {'data': None}, {'data': {}},
    {'data': [], 'errors': []}, {'data': [], 'has_more': True}, {'data': [], 'next_page': 'cursor'},
    {'data': [{}]}, {'data': [{'id': 'target/model'}]},
    {'data': [{'id': 'target/*', 'owned_by': 'system'}]}])
def test_f6_malformed_model_envelope_not_connected(response):
    p, _, _ = models(); p.list_models = lambda: response
    live = LiveCatalog(p, Sources([]), ttl=0)
    with pytest.raises(HTTPException) as error: live.records()
    assert error.value.status_code == 503 and not live._snapshot


def test_f6_empty_data_is_valid_discovery_only():
    p, _, _ = models(); p.list_models = lambda: {'data': []}
    assert p.records() == []


def test_f7_account_region_and_full_arn_binding():
    row = descriptor_record(); c = config(row)
    class WrongAccount(VerifiedSession):
        def get_caller_identity(self): return {'Account': '0000'+'00000000'}
    with pytest.raises(ValueError): configured_catalog(c, forbidden, session=WrongAccount())
    class WrongRegion(VerifiedSession): region_name = 'us-east-1'
    with pytest.raises(ValueError): configured_catalog(c, forbidden, session=WrongRegion())
    c['registries'][0]['registry_arn'] = REGISTRY_ARN.replace(ACCOUNT, '0000'+'00000000')
    with pytest.raises(ValueError): configured_catalog(c, forbidden, session=forbidden)
    row['registryArn'] = REGISTRY_ARN.replace(ACCOUNT, '0000'+'00000000')
    with pytest.raises(ValueError): registry(row).records()


def test_f7_shared_session_sts_first():
    row = descriptor_record(); calls = []
    class Session(VerifiedSession):
        def client(self, service, **kw):
            calls.append(service)
            return self if service == 'sts' else Discovery([row])
        def get_caller_identity(self):
            calls.append('identity'); return {'Account': ACCOUNT}
    assert configured_catalog(config(row), session=Session()).records()
    assert calls == ['sts', 'identity', 'agent-registry']


def test_missing_workspace_and_unknown_skill_fail_closed():
    row = descriptor_record('SKILL'); c = config(row)
    next(iter(c['registries'][0]['exposure'].values())).pop('workspaces')
    with pytest.raises(ValueError): configured_catalog(c, forbidden, session=forbidden)
    row['descriptors'] = {'unknownSkill': {'data': '{}'}}
    item = registry(row).normalize(row)[0]
    assert item['kind'] == 'skill' and not item['supported'] and not item['requestable']


def test_cached_native_snapshot_rechecks_workspace_routes(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.app import create_app
    from .conftest import login, ORIGIN
    monkeypatch.setenv('CATALOG_MODE', 'live')  # Test-local injected provider; no cloud session.
    row = descriptor_record('SKILL'); p = registry(row)
    live = LiveCatalog(Sources([]), p, ttl=60)
    app = create_app(str(tmp_path/'cache.sqlite'), demo_mode=True, worker_enabled=False, catalog_provider=live)
    with TestClient(app, base_url=ORIGIN) as client:
        login(client)
        rid = live.records()[0]['id']
        # Both identities share the exact cached source snapshot, never its projection.
        assert client.get('/api/catalog').json()['count'] == 1
        calls = len(p.client.calls)
        login(client, 'sam')
        assert client.get('/api/catalog').json()['count'] == 0
        assert client.get('/api/catalog?q=Declared').json()['count'] == 0
        assert client.get('/api/catalog/'+rid).status_code == 404
        assert client.get('/api/catalog/'+rid+'/versions/1.0.0').status_code == 404
        assert client.post('/api/requests', json={'component_id': rid, 'reason': 'Hidden request attempt'}).status_code == 404
        assert len(p.client.calls) == calls
