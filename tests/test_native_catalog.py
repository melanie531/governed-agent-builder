"""Offline official wire/descriptor structures; synthetic IDs, no cloud clients."""
import copy
import hashlib
from datetime import datetime, timezone
import json
import pytest
from botocore.session import Session
from botocore.validate import validate_parameters
from fastapi import HTTPException
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.live_catalog import (RegistryCatalogProvider, ModelGatewayCatalogProvider,
    LiveCatalog, Sources, configured_catalog, revision, native_model_reader)
from .conftest import login, ORIGIN

REGISTRY = 'SyntheticReg0001'
ACCOUNT = '9988' + '77665544'
REGISTRY_ARN = f'arn:aws:agent-registry:us-west-2:{ACCOUNT}:registry/{REGISTRY}'

def record_id(name):
    return hashlib.sha256(name.encode()).hexdigest()[:12]
MODEL = Session().get_service_model('agent-registry')


def descriptor_record(kind='MCP', name='weather-server', status='APPROVED'):
    # AWS documented mcpServer.data and additionalData.tools.data, JSON strings.
    descriptors = {
        'MCP': {'mcpServer': {'data': json.dumps({'name': 'my-org/weather-server', 'description': 'Weather data', 'version': '1.0.0'}),
                            'dataSchemaVersion': '2025-12-11', 'additionalData': {'tools': {'dataSchemaVersion': '2025-11-25', 'data': json.dumps({'tools': [
                                {'name': 'get_weather', 'description': 'Get current weather', 'inputSchema': {'type': 'object', 'properties': {'location': {'type': 'string'}}, 'required': ['location']},
                                 'outputSchema': {'type': 'object', 'properties': {'temperature': {'type': 'number'}}}}]})}}}},
        'SKILL': {'agentSkillsDefinition': {'data': json.dumps({'websiteUrl': 'https://example.com/my-skill', 'repository': {'url': 'https://example.com/skill', 'source': 'github'}}),
                                          'dataSchemaVersion': '0.1.0', 'additionalData': {'skillMd': {'data': '---\nname: my-skill\ndescription: Private package metadata\n---'}}}},
        'AGENT': {'a2aAgentCard': {'dataSchemaVersion': '0.3', 'data': json.dumps({'name': 'My Agent', 'description': 'Brief description', 'version': '1.0.0', 'protocolVersion': '0.3.0', 'url': 'https://example.com/a2a', 'capabilities': {}, 'defaultInputModes': ['text/plain'], 'defaultOutputModes': ['text/plain'], 'skills': [{'id': 'default-skill', 'name': 'Default Skill', 'description': 'Description', 'tags': ['general']}]})}},
        'CUSTOM': {'custom': {'data': json.dumps({'type': 'lambda', 'url': 'https://example.com/private'})}},
    }
    return {'recordId': record_id(name), 'registryArn': REGISTRY_ARN,
            'recordArn': REGISTRY_ARN + '/record/' + record_id(name),
            'createdAt': datetime(2026, 1, 1, tzinfo=timezone.utc), 'updatedAt': datetime(2026, 1, 1, tzinfo=timezone.utc), 'recordVersion': '1.0.0', 'name': name,
            'displayName': name.replace('-', ' ').title(), 'description': 'Declared business capability',
            'recordType': kind, 'status': status, 'descriptors': descriptors[kind]}


def expose(records):
    policy = {}
    for record in records:
        rid = f"registry:{REGISTRY}:{record['recordId']}"
        entry = {'approved': True, 'version': record['recordVersion'], 'descriptor_sha256': revision(record['descriptors']),
                 'workspaces': ['research'], 'requestable': True, 'owner': 'Synthetic owner', 'data_handling': 'Reviewed'}
        policy[rid] = entry
        policy[rid + ':tool:get_weather'] = copy.deepcopy(entry)
    return policy


class Discovery:
    def __init__(self, records):
        self.rows = records
        self.calls = []

    def list_discoverable_registry_records(self, **kw):
        validate_parameters(kw, MODEL.operation_model('ListDiscoverableRegistryRecords').input_shape)
        assert kw['registryId'] == REGISTRY and 'status' not in kw
        self.calls.append('list')
        response = {'registryRecords': [{k: v for k, v in row.items() if k != 'descriptors'} for row in self.rows]}
        validate_parameters(response, MODEL.operation_model('ListDiscoverableRegistryRecords').output_shape)
        return response

    def batch_get_discoverable_registry_record(self, **kw):
        validate_parameters(kw, MODEL.operation_model('BatchGetDiscoverableRegistryRecord').input_shape)
        self.calls.append('batch')
        ids = kw['entries'][0]['recordIds']
        result = {'registryRecords': [row for row in self.rows if row['recordId'] in ids], 'errors': []}
        validate_parameters(result, MODEL.operation_model('BatchGetDiscoverableRegistryRecord').output_shape)
        return result

    def search_discoverable_registry_records(self, **kw):
        validate_parameters(kw, MODEL.operation_model('SearchDiscoverableRegistryRecords').input_shape)
        assert kw['registryIds'] == [REGISTRY]
        return {'registryRecords': self.rows}


def test_native_service_shapes_and_tools_provenance():
    # Control plane exists but adapter never invokes its admin record methods.
    control = Session().get_service_model('agent-registry-control')
    assert 'filters' in control.operation_model('ListRegistryRecords').input_shape.members
    row = descriptor_record()
    provider = RegistryCatalogProvider(Discovery([row]), REGISTRY, expose([row]))
    server, tool = provider.records()
    assert server['name'] == row['displayName']
    assert tool['operation'] == 'get_weather' and tool['parent_id'] == server['id']
    assert tool['inputSchema']['required'] == ['location'] and 'outputSchema' in tool
    assert tool['descriptor_version'] == '2025-11-25'
    assert tool['source_revision'] == revision(row['descriptors'])
    assert tool['owner'] == 'Synthetic owner' and tool['execution_binding']['last_checked'] is None
    assert not tool['execution_ready'] and not tool['fixture']
    assert 'example.com' not in json.dumps(provider.search('weather'))


def test_audit_inventory_does_not_manufacture_tools():
    rows = [descriptor_record('CUSTOM', 'blueprint_mcp_tool_server' if i == 0 else f'custom-{i}') for i in range(10)]
    rows += [descriptor_record('SKILL', f'skill-{i}') for i in range(10)]
    rows += [descriptor_record('AGENT', f'agent-{i}', status='APPROVED' if i < 2 else 'PENDING_APPROVAL' if i == 2 else 'DRAFT') for i in range(4)]
    catalog = RegistryCatalogProvider(Discovery(rows), REGISTRY, expose(rows)).records()
    assert len(catalog) == 22
    assert sum(x['kind'] == 'resource' for x in catalog) == 10
    assert sum(x['kind'] == 'skill' for x in catalog) == 10
    assert sum(x['kind'] == 'agent' for x in catalog) == 2
    assert not any(x['kind'] in ('tool', 'mcp_server') for x in catalog)
    assert 'Private package metadata' not in json.dumps(catalog)
    assert 'example.com' not in json.dumps(catalog)


def test_descriptor_review_and_parent_exposure():
    row = descriptor_record(); policy = expose([row]); rid = f'registry:{REGISTRY}:{row["recordId"]}'
    policy[rid]['descriptor_sha256'] = 'outdated'
    assert len(RegistryCatalogProvider(Discovery([row]), REGISTRY, policy).records()) == 1
    policy[rid]['descriptor_sha256'] = revision(row['descriptors'])
    policy[rid + ':tool:get_weather']['workspaces'].append('operations')
    assert RegistryCatalogProvider(Discovery([row]), REGISTRY, policy).records()[1]['discoverable_workspaces'] == ['research']
    policy[rid]['version'] = 'stale'
    assert RegistryCatalogProvider(Discovery([row]), REGISTRY, policy).records() == []


def test_unknown_descriptor_is_nonselectable():
    row = descriptor_record('CUSTOM'); row['descriptors'] = {'http': {'source': {'fromUrl': {'url': 'https://example.com/private'}}}}
    result = RegistryCatalogProvider(Discovery([row]), REGISTRY, expose([row])).records()[0]
    assert result['kind'] == 'resource' and not result['supported'] and not result['requestable']
    assert 'example.com' not in json.dumps(result)


def test_partial_batch_and_expired_cache_fail_closed():
    row = descriptor_record()
    class Broken(Discovery):
        def batch_get_discoverable_registry_record(self, **kw):
            return {'registryRecords': [], 'errors': [{'errorCode': 'AccessDenied'}]}
    provider = RegistryCatalogProvider(Discovery([row]), REGISTRY, expose([row]))
    live = LiveCatalog(Sources([]), provider, ttl=0)
    assert live.records()
    provider.client = Broken([row])
    with pytest.raises(HTTPException): live.records()
    assert not live._snapshot


def test_pagination_repeat_fails_closed():
    class Client:
        def list_discoverable_registry_records(self, **kw): return {'registryRecords': [], 'nextToken': 'same'}
    with pytest.raises(HTTPException): LiveCatalog(Sources([]), RegistryCatalogProvider(Client(), REGISTRY, {})).records()


@pytest.mark.parametrize('connector', [True, False])
def test_mantle_connector_and_provider_need_real_enumeration(connector):
    inference = {'connector': {'source': {'connectorId': 'bedrock-mantle'}}} if connector else {'provider': {'endpoint': 'https://bedrock-mantle.us-west-2.api.aws', 'operations': [{'path': '/v1/messages', 'models': [{'model': 'anthropic.claude-*'}]}]}}
    class Client:
        def get_gateway_target(self, **kw):
            validate_parameters(kw, Session().get_service_model('bedrock-agentcore-control').operation_model('GetGatewayTarget').input_shape)
            assert kw == {'gatewayIdentifier': 'synthetic-gateway', 'targetId': 'synthetic-target'}
            return {'status': 'READY', 'name': 'bedrock-mantle', 'targetConfiguration': {'inference': inference}}
    names = ['anthropic.claude-test', 'openai.gpt-test', 'google.gemini-test', 'anthropic.claude-*']
    policy = {f'model:synthetic-gateway:synthetic-target:{name}': {'approved': True, 'version': revision(inference), 'workspaces': ['research']} for name in names}
    reader = lambda: {'data': [{'id': 'bedrock-mantle/' + name, 'owned_by': 'system'} for name in names]}
    provider = ModelGatewayCatalogProvider(Client(), 'synthetic-gateway', 'us-west-2', policy, reader, ('synthetic-target',))
    rows = provider.records()
    assert len(rows) == 2 and all(not r['execution_ready'] for r in rows)
    assert all(r['model_id'].startswith('bedrock-mantle/') for r in rows)
    provider.list_models = lambda: {'data': []}
    assert provider.records() == []  # Wildcard mappings never seed a model.
    provider.list_models = None
    with pytest.raises(Exception): provider.records()


def test_factory_no_default_sources_or_sdk_construction(monkeypatch):
    monkeypatch.delenv('NATIVE_CATALOG_CONFIG', raising=False)
    def forbidden(*a, **kw): raise AssertionError('No client permitted')
    assert configured_catalog(client_factory=forbidden) is None
    assert configured_catalog({'approved': True, 'registries': []}, forbidden) is None
    row = descriptor_record('SKILL')
    calls = []
    def client(service, region):
        calls.append((service, region)); return Discovery([row])
    config = {'approved': True, 'registries': [{'approved': True, 'registry_id': REGISTRY, 'region': 'us-west-2', 'exposure': expose([row])}]}
    assert configured_catalog(config, client).records()[0]['kind'] == 'skill'
    assert calls == [('agent-registry', 'us-west-2')]


def test_model_transport_rejects_unapproved_url_without_credentials():
    with pytest.raises(ValueError): native_model_reader({'gateway_id': 'synthetic', 'region': 'us-west-2', 'auth': 'AWS_IAM', 'list_models_url': 'https://example.com/models'})


def test_app_factory_binding_and_hidden_records(tmp_path, monkeypatch):
    rows = [descriptor_record('SKILL'), descriptor_record('SKILL', 'hidden'), descriptor_record('AGENT', 'pending', 'PENDING_APPROVAL')]
    policy = expose(rows); policy[f'registry:{REGISTRY}:{record_id("hidden")}']['workspaces'] = ['operations']
    live = LiveCatalog(Sources([]), RegistryCatalogProvider(Discovery(rows), REGISTRY, policy))
    monkeypatch.setenv('CATALOG_MODE', 'live')
    monkeypatch.setattr('backend.app.configured_catalog', lambda: live)
    app = create_app(str(tmp_path/'native.sqlite'), demo_mode=True, worker_enabled=False)
    with TestClient(app, base_url=ORIGIN) as client:
        login(client)
        result = client.get('/api/catalog').json()
        assert result['connection_state'] == 'connected' and result['count'] == 1
        assert result['items'][0]['requestable'] and not result['items'][0]['usable']
        assert 'pending' not in json.dumps(result) and f'registry:{REGISTRY}:{record_id("hidden")}' not in json.dumps(result)
        assert client.get(f'/api/catalog/registry:{REGISTRY}:{record_id("hidden")}').status_code == 404
        assert client.post('/api/requests', json={'component_id': f'registry:{REGISTRY}:{record_id("hidden")}', 'reason': 'Request hidden data'}).status_code == 404


def test_unknown_schema_version_nonselectable():
    row = descriptor_record()
    row['descriptors']['mcpServer']['dataSchemaVersion'] = '2099-01-01'
    rows = RegistryCatalogProvider(Discovery([row]), REGISTRY, expose([row])).records()
    assert len(rows) == 1 and not rows[0]['supported'] and not rows[0]['requestable']


def test_cross_registry_batch_rejected():
    row = descriptor_record()
    class WrongRegistry(Discovery):
        def batch_get_discoverable_registry_record(self, **kw):
            result = super().batch_get_discoverable_registry_record(**kw)
            result['registryRecords'] = copy.deepcopy(result['registryRecords'])
            result['registryRecords'][0]['registryArn'] = REGISTRY_ARN + '-different'
            return result
    with pytest.raises(ValueError): RegistryCatalogProvider(WrongRegistry([row]), REGISTRY, expose([row])).records()


def test_safe_schema_does_not_export_source_urls():
    row = descriptor_record()
    descriptor = row['descriptors']['mcpServer']
    descriptor['source'] = {'fromUrl': {'url': 'https://example.com/private'}}
    payload = json.loads(descriptor['additionalData']['tools']['data'])
    payload['tools'][0]['inputSchema'].update({'$ref': 'https://example.com/private', 'examples': ['private sample']})
    descriptor['additionalData']['tools']['data'] = json.dumps(payload)
    result = RegistryCatalogProvider(Discovery([row]), REGISTRY, expose([row])).records()
    assert 'example.com' not in json.dumps(result) and 'private sample' not in json.dumps(result)


def test_model_source_factory_includes_connector_target():
    inference = {'connector': {'source': {'connectorId': 'bedrock-mantle'}}}
    class Control:
        def get_gateway_target(self, **kw):
            assert kw['targetId'] == 'synthetic-target'
            return {'status': 'READY', 'name': 'bedrock-mantle', 'targetConfiguration': {'inference': inference}}
    calls = []
    def client(service, region):
        calls.append(service); return Control()
    name = 'bedrock-mantle/openai.gpt-test'
    source = {'approved': True, 'region': 'us-west-2', 'gateway_id': 'synthetic-gateway', 'target_ids': ['synthetic-target'],
              'exposure': {'model:synthetic-gateway:synthetic-target:openai.gpt-test': {'approved': True, 'version': revision(inference), 'workspaces': ['research']}}}
    provider = configured_catalog({'approved': True, 'model_gateways': [source]}, client,
                                  lambda s: lambda: {'data': [{'id': name, 'owned_by': 'system'}]})
    assert provider.records()[0]['connector'] == 'bedrock-mantle'
    assert calls == ['bedrock-agentcore-control']
