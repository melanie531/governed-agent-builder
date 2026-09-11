"""Read-only native discovery. Operator allowlists are not execution authority."""
from dataclasses import dataclass, field
import copy
import hashlib
import json
import os
import re
import time
from typing import Protocol
from urllib.parse import urlparse

from fastapi import HTTPException
from .aws_adapter import IntegrationNotConfigured


class CatalogProvider(Protocol):
    def records(self) -> list[dict]: ...


def revision(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def pages(method, result_key, **kwargs):
    """Bound discovery and reject partial snapshots/repeated pagination tokens."""
    seen = set()
    for _ in range(10):
        response = method(**kwargs)
        yield from response.get(result_key, [])
        token = response.get('nextToken')
        if not token:
            return
        if token in seen:
            break
        seen.add(token)
        kwargs['nextToken'] = token
    raise IntegrationNotConfigured('Catalog pagination limit exceeded')


def approved_metadata(record_id, version, kind, origin, policy, provider):
    entry = policy.get(record_id)
    if not entry or entry.get('approved') is not True or not version or entry.get('version') != str(version):
        return None
    return {'id': record_id, 'record_id': record_id, 'version': str(version),
            'kind': kind, 'provider': provider, 'name': '', 'description': '',
            'capabilities': [], 'data_handling': entry.get('data_handling', 'Not assessed'),
            'owner': entry.get('owner', 'Not declared'), 'external': False, 'approved': True,
            'discoverable': True, 'discoverable_workspaces': entry.get('workspaces', []),
            'requestable': entry.get('requestable') is True, 'integration_ready': False,
            'execution_ready': False, 'execution_binding': {'status': 'unverified', 'last_checked': None},
            'origin': origin, 'refreshed_at': time.time(), 'fixture': False,
            'protocol': 'metadata-only', 'supported': True}


def json_object(raw):
    if not isinstance(raw, str) or len(raw) > 262144:
        raise ValueError('Invalid descriptor')
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError('Invalid descriptor')
    return result


def safe_schema(schema):
    """Preserve structural JSON Schema, omit defaults/examples and external refs.

    Schemas are only exposed after an operator approves the exact descriptor hash.
    No dereferencing, descriptor source URLs, package content or credentials exported.
    """
    if isinstance(schema, bool):
        return schema
    if not isinstance(schema, dict):
        raise ValueError('Invalid schema')
    out = {}
    for k, v in schema.items():
        if k in ('type', 'required', 'enum', 'const', 'minimum', 'maximum', 'minLength', 'maxLength', 'minItems', 'maxItems', 'pattern', 'format'):
            out[k] = v
        elif k in ('properties', '$defs', 'definitions'):
            out[k] = {name: safe_schema(value) for name, value in v.items()}
        elif k in ('items', 'additionalProperties', 'not', 'if', 'then', 'else'):
            out[k] = safe_schema(v)
        elif k in ('anyOf', 'oneOf', 'allOf', 'prefixItems'):
            out[k] = [safe_schema(value) for value in v]
        elif k == '$ref' and isinstance(v, str) and v.startswith('#/'):
            out[k] = v
    return out


@dataclass
class RegistryCatalogProvider:
    client: object
    registry_id: str
    exposure: dict

    def normalize(self, detail):
        if detail.get('status') != 'APPROVED':
            return []
        record_id = detail['recordId']
        rid = f'registry:{self.registry_id}:{record_id}'
        version = detail.get('recordVersion')
        descriptors = detail.get('descriptors', {})
        record_type = detail.get('recordType')
        key = next(iter(descriptors), '') if len(descriptors) == 1 else ''
        valid = {'MCP': {'mcpServer', 'custom'}, 'AGENT': {'a2aAgentCard', 'mcpServer', 'custom'},
                 'SKILL': {'agentSkillsDefinition', 'custom'}, 'CUSTOM': {'custom'}}
        supported = key in valid.get(record_type, set())
        versions = {'mcpServer': {'2025-12-11', '2025-10-17', '2025-10-11', '2025-09-29', '2025-09-16', '2025-07-09'},
                    'a2aAgentCard': {'0.3'}, 'agentSkillsDefinition': {'0.1.0'}}
        if key in versions:
            descriptor = descriptors[key]
            # SKILL may contain markdown only; a structured definition needs a known schema.
            if descriptor.get('data') is not None or key != 'agentSkillsDefinition':
                supported = supported and descriptor.get('dataSchemaVersion') in versions[key]
        if key == 'mcpServer':
            tools = descriptors[key].get('additionalData', {}).get('tools', {})
            if tools and tools.get('dataSchemaVersion') not in {'2025-11-25', '2025-06-18', '2025-03-26', '2024-11-05'}:
                supported = False
        kind = {'MCP': 'mcp_server', 'AGENT': 'agent', 'SKILL': 'skill', 'CUSTOM': 'resource'}.get(record_type, 'resource')
        # A custom descriptor does not prove a native protocol or an executable tool.
        item = approved_metadata(rid, version, kind, 'AWS Agent Registry', self.exposure, 'AWS Agent Registry')
        if item is None:
            return []
        descriptor = descriptors.get(key, {})
        source_hash = revision(descriptors)
        reviewed = self.exposure[rid].get('descriptor_sha256') == source_hash
        item.update(name=detail.get('displayName') or detail.get('name') or record_id,
                    description=detail.get('description', ''), supported=supported,
                    protocol={'mcpServer': 'MCP', 'a2aAgentCard': 'A2A', 'agentSkillsDefinition': 'AgentSkills', 'custom': 'CUSTOM'}.get(key, 'unsupported'),
                    source_version=version, descriptor_version=descriptor.get('dataSchemaVersion'),
                    source_revision=source_hash, registry_record=record_id,
                    descriptor_reviewed=reviewed)
        if not supported:
            item.update(requestable=False, protocol='unsupported')
        if kind == 'skill':
            # Metadata/markdown and package version labels alone are not immutable artifacts.
            item['artifact_status'] = 'immutable approved artifact binding required'
        rows = [item]
        if key != 'mcpServer' or not supported or not reviewed:
            return rows
        server = json_object(descriptor['data'])
        tools = descriptor.get('additionalData', {}).get('tools', {})
        content = json_object(tools['data']) if tools.get('data') else {'tools': []}
        for tool in content.get('tools', []):
            name = tool.get('name', '')
            if not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', name) or not isinstance(tool.get('inputSchema'), dict):
                raise ValueError('Invalid MCP callable schema')
            child = approved_metadata(rid + ':tool:' + name, version, 'tool', 'AWS Agent Registry', self.exposure, 'AWS Agent Registry')
            if child:
                child.update(name=tool.get('title') or name, description=tool.get('description', ''),
                             operation=name, protocol='MCP', parent_id=rid, parent_name=item['name'],
                             server_version=server.get('version'), inputSchema=safe_schema(tool['inputSchema']),
                             source_version=version, source_revision=source_hash, registry_record=record_id,
                             descriptor_version=tools.get('dataSchemaVersion'), descriptor_reviewed=True)
                # Child cannot widen parent discoverability or disclosure scope.
                child['discoverable_workspaces'] = list(set(child['discoverable_workspaces']) & set(item['discoverable_workspaces']))
                if 'outputSchema' in tool:
                    child['outputSchema'] = safe_schema(tool['outputSchema'])
                rows.append(child)
        return rows

    def records(self):
        summaries = list(pages(self.client.list_discoverable_registry_records, 'registryRecords',
                               registryId=self.registry_id, maxResults=50))
        if len(summaries) > 200 or len({s['recordId'] for s in summaries}) != len(summaries):
            raise ValueError('Registry too large')
        # Batch only explicitly exposed versions. Never query the admin control plane.
        eligible = {s['recordId']: s for s in summaries if s.get('status') == 'APPROVED'
                    and approved_metadata(f"registry:{self.registry_id}:{s['recordId']}", s.get('recordVersion'),
                                          'resource', '', self.exposure, '')}
        rows = []
        ids = list(eligible)
        for start in range(0, len(ids), 20):
            batch = ids[start:start + 20]
            response = self.client.batch_get_discoverable_registry_record(entries=[{'registryId': self.registry_id, 'recordIds': batch}])
            details = response.get('registryRecords', [])
            if response.get('errors') or len(details) != len(batch) or {d['recordId'] for d in details} != set(batch):
                raise ValueError('Incomplete Registry snapshot')
            for detail in details:
                summary = eligible[detail['recordId']]
                if detail.get('registryArn', '').rsplit('/', 1)[-1] != self.registry_id:
                    raise ValueError('Unexpected registry')
                if any(detail.get(k) != summary.get(k) for k in ('recordVersion', 'status', 'recordType', 'registryArn')):
                    raise ValueError('Registry changed during discovery')
                rows.extend(self.normalize(detail))
        return rows

    def search(self, query):
        """Optional native search, always within this approved registry and exposure."""
        response = self.client.search_discoverable_registry_records(searchQuery=query, registryIds=[self.registry_id], maxResults=50)
        rows = []
        for detail in response.get('registryRecords', []):
            if detail.get('registryArn', '').rsplit('/', 1)[-1] != self.registry_id:
                raise ValueError('Unexpected registry')
            rows.extend(self.normalize(detail))
        return rows


@dataclass
class ModelGatewayCatalogProvider:
    client: object
    gateway_id: str
    region: str
    exposure: dict
    list_models: object = None
    target_ids: tuple = ()

    def records(self):
        if self.list_models is None:
            raise IntegrationNotConfigured('Model enumeration is not configured')
        # Native HTTP GET /inference/v1/models, not an invented boto3 ListModels API.
        enumerated = self.list_models()
        if enumerated.get('has_more') or enumerated.get('nextToken') or len(enumerated.get('data', [])) > 200:
            raise ValueError('Incomplete model enumeration')
        rows = []
        for target_id in self.target_ids:
            detail = self.client.get_gateway_target(gatewayIdentifier=self.gateway_id, targetId=target_id)
            if detail.get('status') != 'READY':
                raise ValueError('Model source unavailable')
            inference = detail.get('targetConfiguration', {}).get('inference', {})
            connector = inference.get('connector', {}).get('source', {}).get('connectorId')
            provider = inference.get('provider', {})
            endpoint = urlparse(provider.get('endpoint', ''))
            mantle = (endpoint.scheme == 'https' and endpoint.netloc == f'bedrock-mantle.{self.region}.api.aws'
                      and endpoint.path in ('', '/') and not endpoint.query and not endpoint.fragment)
            if connector != 'bedrock-mantle' and not mantle:
                continue
            version = revision(inference)
            for model in enumerated.get('data', []):
                qualified = model.get('id', '')
                prefix, separator, model_id = qualified.partition('/')
                if not separator or prefix != detail.get('name') or any(c in model_id for c in '*?[]/') or model.get('owned_by') != 'system':
                    continue
                family = model_id.removeprefix('global.').removeprefix('us.').removeprefix('eu.').removeprefix('apac.')
                if not family.startswith(('anthropic.claude-', 'openai.gpt-', 'claude-', 'gpt-')):
                    continue
                rid = f'model:{self.gateway_id}:{target_id}:{model_id}'
                item = approved_metadata(rid, version, 'model', 'AgentCore Model Gateway', self.exposure, 'Amazon Bedrock')
                if item:
                    item.update(name=qualified, description='Enumerated Bedrock inference route; execution not verified',
                                protocol='inference', model_id=qualified, target_id=target_id,
                                source_version=version, source_revision=revision(model), connector=connector or 'bedrock-mantle provider')
                    rows.append(item)
        return rows


@dataclass
class LiveCatalog:
    models: CatalogProvider
    registry: CatalogProvider
    ttl: float = 30
    _snapshot: list = field(default_factory=list, init=False)
    _expires: float = field(default=0, init=False)

    def records(self):
        if time.monotonic() < self._expires:
            return copy.deepcopy(self._snapshot)
        try:
            items = self.models.records() + self.registry.records()
            if len(items) > 200 or len({i['id'] for i in items}) != len(items):
                raise ValueError('Invalid snapshot')
            self._snapshot = copy.deepcopy(items)
            self._expires = time.monotonic() + min(60, max(0, self.ttl))
            return items
        except Exception:
            self._snapshot, self._expires = [], 0
            raise HTTPException(503, 'Live catalog unavailable; no fixture fallback. Contact the platform administrator.') from None


@dataclass
class Sources:
    providers: list

    def records(self):
        return [item for provider in self.providers for item in provider.records()]


def configured_catalog(config=None, client_factory=None, model_reader_factory=None):
    """Only server-owned explicit approval config. No scans, SSM, or legacy defaults.

    Client/transport factories allow offline tests without credential resolution.
    """
    if config is None:
        raw = os.getenv('NATIVE_CATALOG_CONFIG')
        if not raw:
            return None
        config = json.loads(raw)
    if config.get('approved') is not True:
        return None
    registries, models = config.get('registries', []), config.get('model_gateways', [])
    if not registries and not models:
        return None
    if len(registries) + len(models) > 10:
        raise ValueError('Too many catalog sources')
    if client_factory is None:
        import boto3
        from botocore.config import Config
        def client_factory(service, region):
            return boto3.client(service, region_name=region, config=Config(connect_timeout=3, read_timeout=5, retries={'max_attempts': 1}))
    rp, mp = [], []
    for source in registries:
        if source.get('approved') is not True:
            raise ValueError('Unapproved source')
        rp.append(RegistryCatalogProvider(client_factory('agent-registry', source['region']), source['registry_id'], source['exposure']))
    for source in models:
        if source.get('approved') is not True or not source.get('target_ids'):
            raise ValueError('Unapproved model source')
        reader = (model_reader_factory or native_model_reader)(source)
        mp.append(ModelGatewayCatalogProvider(client_factory('bedrock-agentcore-control', source['region']),
                  source['gateway_id'], source['region'], source['exposure'], reader, tuple(source['target_ids'])))
    return LiveCatalog(Sources(mp), Sources(rp), config.get('cache_seconds', 30))


def native_model_reader(source):
    """IAM-only read transport to an exact separately approved Gateway endpoint."""
    from botocore.awsrequest import AWSRequest
    from botocore.auth import SigV4Auth
    import boto3
    import urllib.request
    gateway, region = source['gateway_id'], source['region']
    if not re.fullmatch(r'[a-zA-Z0-9-]+', gateway) or not re.fullmatch(r'[a-z0-9-]+', region):
        raise ValueError('Invalid Gateway reference')
    expected = f'https://{gateway}.gateway.bedrock-agentcore.{region}.amazonaws.com/inference/v1/models'
    if source.get('list_models_url') != expected or source.get('auth') != 'AWS_IAM':
        raise ValueError('Explicit IAM model discovery endpoint approval required')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    def read_models():
        request = AWSRequest(method='GET', url=expected)
        credentials = boto3.Session().get_credentials()
        if credentials is None:
            raise IntegrationNotConfigured('IAM discovery unavailable')
        SigV4Auth(credentials.get_frozen_credentials(), 'bedrock-agentcore', region).add_auth(request)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(urllib.request.Request(expected, headers=dict(request.headers), method='GET'), timeout=5) as response:
            raw = response.read(1048577)
        if len(raw) > 1048576:
            raise ValueError('Model response too large')
        return json.loads(raw)
    return read_models


def visibility(component, persona):
    if not component.get('approved') or component.get('discoverable') is False:
        return False
    workspaces = component.get('discoverable_workspaces')
    return (workspaces is None or persona['workspace'] in workspaces) and (not component.get('external') or persona['external_allowed'])


def grant_scope(persona, component_id):
    raw = json.dumps([persona['id'], persona['workspace'], component_id], separators=(',', ':'))
    return 'catalog-grant:' + hashlib.sha256(raw.encode()).hexdigest()


def has_grant(db, persona, component):
    grant = db.select('grants', where=[('persona', '=', persona['id']), ('component', '=', component['id'])]).fetchone()
    if component.get('fixture', True):
        return bool(grant)
    return bool(grant and db.select('settings', where=[('key', '=', grant_scope(persona, component['id']))]).fetchone())


def projection(db, persona, component):
    if not visibility(component, persona):
        return None
    granted = has_grant(db, persona, component)
    ready = component.get('integration_ready', True) and component.get('supported', True)
    usable = granted and ready
    requestable = not granted and component.get('requestable', True) and component.get('supported', True)
    public_fields = ('id', 'name', 'version', 'kind', 'provider', 'description', 'capabilities', 'data_handling', 'origin', 'refreshed_at', 'fixture', 'owner', 'protocol', 'supported', 'source_version', 'source_revision', 'descriptor_version', 'registry_record', 'descriptor_reviewed', 'execution_ready', 'execution_binding', 'artifact_status', 'parent_id', 'parent_name', 'operation', 'server_version', 'inputSchema', 'outputSchema', 'model_id', 'target_id', 'connector')
    public = {k: component[k] for k in public_fields if k in component}
    public.update({'record_id': component['id'], 'approved': True, 'external': component.get('external', False),
                   'discoverable': True, 'usable': usable, 'granted': granted, 'requestable': requestable,
                   'status': 'available' if usable else 'requestable' if requestable else 'blocked',
                   'data_policy_allowed': True,
                   'policy_reason': 'Workspace grant permits use' if usable else 'Administrator grant required' if requestable else 'Granted; execution integration not verified' if granted else 'Access requests disabled',
                   'origin': component.get('origin', 'Local development fixture'),
                   'fixture': component.get('fixture', True),
                   'data_handling': component.get('data_handling', 'Synthetic development data only'),
                   'capabilities': component.get('capabilities', []),
                   'refreshed_at': component.get('refreshed_at')})
    return public
