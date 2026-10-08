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


DIGEST_FORMAT = 'native-catalog-v2'


def revision(value):
    """Versioned Python JSON canonicalization, not RFC 8785 (see native docs)."""
    raw = json.dumps({'format': DIGEST_FORMAT, 'value': value}, sort_keys=True,
                     separators=(',', ':'), ensure_ascii=True, allow_nan=False)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def record_revision(detail):
    return revision({key: detail.get(key) for key in (
        'registryArn', 'recordArn', 'recordId', 'recordType', 'recordVersion',
        'name', 'displayName', 'description', 'descriptors')})


def auth_metadata(detail):
    """Allowlist NONSECRET routing metadata; never return it in public rows."""
    result = []
    allowed = {'providerArn', 'scopes', 'grantType', 'defaultReturnUrl',
               'credentialParameterName', 'credentialPrefix', 'credentialLocation',
               'service', 'region'}
    for entry in detail.get('credentialProviderConfigurations', []):
        item = {'credentialProviderType': entry.get('credentialProviderType')}
        providers = entry.get('credentialProvider', {})
        item['credentialProvider'] = {kind: {
            **{k: v for k, v in config.items() if k in allowed},
            **({'customParameterNames': sorted(config['customParameters'])}
               if 'customParameters' in config else {})}
            for kind, config in providers.items()}
        result.append(item)
    return result


def target_revision(detail):
    return revision({'gatewayArn': detail.get('gatewayArn'), 'targetId': detail.get('targetId'),
                     'name': detail.get('name'),
                     'inference': detail.get('targetConfiguration', {}).get('inference'),
                     'auth_metadata': auth_metadata(detail)})


def model_revision(detail, model):
    return revision({'target_revision': target_revision(detail), 'model': model})


def valid_workspaces(value):
    return (isinstance(value, list) and bool(value)
            and all(isinstance(x, str) and bool(x.strip()) for x in value)
            and len(set(value)) == len(value))


def validate_exposure(exposure, prefix):
    if not isinstance(exposure, dict):
        raise ValueError('Explicit exposure map required')
    for rid, entry in exposure.items():
        if not isinstance(rid, str) or not rid.startswith(prefix) or not isinstance(entry, dict):
            raise ValueError('Invalid exposure identity')
        if (entry.get('approved') is not True or type(entry.get('requestable')) is not bool
                or not valid_workspaces(entry.get('workspaces'))
                or any(not isinstance(entry.get(k), str) or not entry[k].strip()
                       for k in ('version', 'owner', 'data_handling'))
                or entry.get('digest_format') != DIGEST_FORMAT
                or not re.fullmatch(r'[0-9a-f]{64}', entry.get('approval_sha256', ''))):
            raise ValueError('Invalid exposure approval; explicit v2 migration required')



def pages(method, result_key, **kwargs):
    """Bound discovery and reject partial snapshots/repeated pagination tokens."""
    seen = set()
    for _ in range(10):
        response = method(**kwargs)
        if not isinstance(response, dict) or not isinstance(response.get(result_key), list) or response.get('errors'):
            raise ValueError('Invalid discovery envelope')
        yield from response[result_key]
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
    if (not entry or entry.get('approved') is not True or not version
            or entry.get('version') != str(version) or not valid_workspaces(entry.get('workspaces'))):
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
    result = json.loads(raw, parse_constant=lambda x: (_ for _ in ()).throw(ValueError('Invalid JSON constant')))
    if not isinstance(result, dict):
        raise ValueError('Invalid descriptor')
    return result


def validate_schema(schema):
    """Minimal JSON Schema shape checks, never remote resolution or execution."""
    if isinstance(schema, bool):
        return
    if not isinstance(schema, dict):
        raise ValueError('Invalid JSON schema')
    if 'type' in schema:
        types = schema['type'] if isinstance(schema['type'], list) else [schema['type']]
        if not types or any(not isinstance(x, str) or x not in {'object', 'array', 'string', 'number', 'integer', 'boolean', 'null'} for x in types):
            raise ValueError('Invalid schema type')
    if 'required' in schema and (not isinstance(schema['required'], list) or any(not isinstance(x, str) for x in schema['required'])):
        raise ValueError('Invalid required properties')
    if 'enum' in schema and (not isinstance(schema['enum'], list) or not schema['enum']):
        raise ValueError('Invalid schema enum')
    for field in ('$ref', 'pattern', 'format'):
        if field in schema and not isinstance(schema[field], str):
            raise ValueError('Invalid schema string')
    for field in ('minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum', 'multipleOf'):
        if field in schema and type(schema[field]) not in (int, float):
            raise ValueError('Invalid schema number')
    for field in ('minLength', 'maxLength', 'minItems', 'maxItems', 'minProperties', 'maxProperties'):
        if field in schema and (type(schema[field]) is not int or schema[field] < 0):
            raise ValueError('Invalid schema bound')
    for field in ('properties', '$defs', 'definitions', 'patternProperties'):
        if field in schema:
            if not isinstance(schema[field], dict):
                raise ValueError('Invalid schema mapping')
            for child in schema[field].values(): validate_schema(child)
    for field in ('items', 'additionalProperties', 'not', 'if', 'then', 'else', 'contains'):
        if field in schema: validate_schema(schema[field])
    for field in ('anyOf', 'oneOf', 'allOf', 'prefixItems'):
        if field in schema:
            if not isinstance(schema[field], list) or not schema[field]:
                raise ValueError('Invalid schema alternatives')
            for child in schema[field]: validate_schema(child)


def safe_schema(schema):
    """Redacted DISPLAY ONLY, never an execution validation schema.

    Preserve structural JSON Schema, omit defaults/examples and external refs.

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


def validate_descriptor(key, descriptor):
    versions = {'mcpServer': {'2025-12-11', '2025-10-17', '2025-10-11', '2025-09-29', '2025-09-16', '2025-07-09'},
                'a2aAgentCard': {'0.3'}, 'agentSkillsDefinition': {'0.1.0'}}
    if not isinstance(descriptor, dict):
        raise ValueError('Invalid descriptor')
    version = descriptor.get('dataSchemaVersion')
    if key in versions and (('dataSchemaVersion' in descriptor or 'data' in descriptor
                             or key != 'agentSkillsDefinition') and version not in versions[key]):
        raise ValueError('Unsupported descriptor version')
    if key == 'custom':
        # CUSTOM has no standard schema version, but does require valid JSON.
        json.loads(descriptor['data'], parse_constant=lambda x: (_ for _ in ()).throw(ValueError('Invalid JSON')))
        return None, []
    extra = descriptor.get('additionalData', {})
    if not isinstance(extra, dict):
        raise ValueError('Invalid additional data')
    if 'data' not in descriptor:
        if key != 'agentSkillsDefinition' or not isinstance(extra.get('skillMd', {}).get('data'), str):
            raise ValueError('Missing descriptor data')
        return None, []
    data = json_object(descriptor['data'])
    for field in ('dataSchemaVersion', 'schemaVersion'):
        if field in data and data[field] != version:
            raise ValueError('Contradictory schema version')
    if key == 'a2aAgentCard':
        if data.get('protocolVersion') != '0.3.0':
            raise ValueError('Unsupported A2A protocol')
        for field in ('name', 'description', 'version', 'url'):
            if not isinstance(data.get(field), str) or not data[field]:
                raise ValueError('Invalid A2A card')
        if not isinstance(data.get('capabilities'), dict):
            raise ValueError('Invalid A2A capabilities')
        for field in ('defaultInputModes', 'defaultOutputModes', 'skills'):
            if not isinstance(data.get(field), list):
                raise ValueError('Invalid A2A list')
        if any(not isinstance(x, str) for field in ('defaultInputModes', 'defaultOutputModes') for x in data[field]):
            raise ValueError('Invalid A2A modes')
        for skill in data['skills']:
            if (not isinstance(skill, dict) or any(not isinstance(skill.get(k), str) for k in ('id', 'name', 'description'))
                    or not isinstance(skill.get('tags'), list) or any(not isinstance(x, str) for x in skill['tags'])):
                raise ValueError('Invalid A2A skill')
    if key == 'agentSkillsDefinition':
        # AWS permits unknown extension fields; retain original privately, not in projection.
        if 'websiteUrl' in data and not isinstance(data['websiteUrl'], str):
            raise ValueError('Invalid skill website')
        if 'repository' in data:
            repo = data['repository']
            if not isinstance(repo, dict) or any(k in repo and not isinstance(repo[k], str) for k in ('url', 'source')):
                raise ValueError('Invalid skill repository')
        if 'protocolVersion' in data and data['protocolVersion'] != '0.1.0':
            raise ValueError('Contradictory skill protocol')
    tools = []
    if key == 'mcpServer':
        if any(not isinstance(data.get(k), str) or not data[k] for k in ('name', 'version')):
            raise ValueError('Invalid MCP server')
        if '$schema' in data and (not isinstance(data['$schema'], str) or not data['$schema'].endswith('/' + version + '/server.schema.json')):
            raise ValueError('Contradictory MCP server schema')
        if 'protocolVersion' in data and data['protocolVersion'] != version:
            raise ValueError('Contradictory MCP server protocol')
        if 'description' in data and not isinstance(data['description'], str):
            raise ValueError('Invalid MCP description')
        if 'tools' in extra:
            envelope = extra['tools']
            if envelope.get('dataSchemaVersion') not in {'2025-11-25', '2025-06-18', '2025-03-26', '2024-11-05'}:
                raise ValueError('Unsupported MCP tools version')
            content = json_object(envelope['data'])
            if not isinstance(content.get('tools'), list):
                raise ValueError('Invalid MCP tools')
            if 'protocolVersion' in content and content['protocolVersion'] != envelope['dataSchemaVersion']:
                raise ValueError('Contradictory MCP tools protocol')
            tools = content['tools']
            seen = set()
            for tool in tools:
                name = tool.get('name', '')
                if not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', name) or name in seen:
                    raise ValueError('Invalid MCP tool name')
                seen.add(name)
                if not isinstance(tool.get('inputSchema'), dict) or tool['inputSchema'].get('type') != 'object':
                    raise ValueError('Invalid MCP callable schema')
                for field in ('description', 'title'):
                    if field in tool and not isinstance(tool[field], str):
                        raise ValueError('Invalid MCP tool text')
                validate_schema(tool['inputSchema'])
                safe_schema(tool['inputSchema'])
                if 'outputSchema' in tool:
                    if not isinstance(tool['outputSchema'], dict) or tool['outputSchema'].get('type') != 'object':
                        raise ValueError('Invalid MCP output schema')
                    validate_schema(tool['outputSchema'])
                    safe_schema(tool['outputSchema'])
    return data, tools


@dataclass
class RegistryCatalogProvider:
    client: object
    registry_id: str
    exposure: dict
    registry_arn: str = ""

    def normalize(self, detail):
        if detail.get('status') != 'APPROVED':
            return []
        record_id = detail['recordId']
        rid = f'registry:{self.registry_id}:{record_id}'
        version = detail.get('recordVersion')
        if (not self.registry_arn or detail.get('registryArn') != self.registry_arn
                or detail.get('recordArn') != self.registry_arn + '/record/' + record_id):
            raise ValueError('Unexpected registry identity')
        descriptors = detail.get('descriptors', {})
        if not isinstance(descriptors, dict):
            descriptors = {}
        record_type = detail.get('recordType')
        key = next(iter(descriptors), '') if len(descriptors) == 1 else ''
        valid = {'MCP': {'mcpServer', 'custom'}, 'AGENT': {'a2aAgentCard', 'mcpServer', 'custom'},
                 'SKILL': {'agentSkillsDefinition', 'custom'}, 'CUSTOM': {'custom'}}
        supported = key in valid.get(record_type, set())
        server, tool_data = None, []
        if supported:
            try:
                server, tool_data = validate_descriptor(key, descriptors[key])
            except (ValueError, KeyError, TypeError, AttributeError):
                supported = False
        kind = {'MCP': 'mcp_server', 'AGENT': 'agent', 'SKILL': 'skill', 'CUSTOM': 'resource'}.get(record_type, 'resource')
        # A custom descriptor does not prove a native protocol or an executable tool.
        item = approved_metadata(rid, version, kind, 'AWS Agent Registry', self.exposure, 'AWS Agent Registry')
        if item is None:
            return []
        descriptor = descriptors.get(key, {})
        source_hash = record_revision(detail)
        reviewed = (self.exposure[rid].get('digest_format') == DIGEST_FORMAT
                    and self.exposure[rid].get('approval_sha256') == source_hash)
        if not reviewed:
            return []
        item.update(name=detail.get('displayName') or detail.get('name') or record_id,
                    description=detail.get('description', ''), supported=supported,
                    protocol={'mcpServer': 'MCP', 'a2aAgentCard': 'A2A', 'agentSkillsDefinition': 'AgentSkills', 'custom': 'CUSTOM'}.get(key, 'unsupported'),
                    source_version=version, descriptor_version=descriptor.get('dataSchemaVersion') if isinstance(descriptor, dict) else None,
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
        tools = descriptor.get('additionalData', {}).get('tools', {})
        for tool in tool_data:
            name = tool['name']
            child = approved_metadata(rid + ':tool:' + name, version, 'tool', 'AWS Agent Registry', self.exposure, 'AWS Agent Registry')
            child_policy = self.exposure.get(rid + ':tool:' + name, {})
            if child and child_policy.get('digest_format') == DIGEST_FORMAT and child_policy.get('approval_sha256') == source_hash:
                child.update(name=tool.get('title') or name, description=tool.get('description', ''),
                             operation=name, protocol='MCP', parent_id=rid, parent_name=item['name'],
                             server_version=server.get('version'), inputSchema=safe_schema(tool['inputSchema']),
                             source_version=version, source_revision=source_hash, registry_record=record_id,
                             descriptor_version=tools.get('dataSchemaVersion'), descriptor_reviewed=True,
                             schema_purpose='redacted display-only; not execution validation')
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
            if not isinstance(response, dict) or not isinstance(response.get('registryRecords'), list) or not isinstance(response.get('errors'), list):
                raise ValueError('Invalid Registry batch envelope')
            details = response['registryRecords']
            if response.get('errors') or len(details) != len(batch) or {d['recordId'] for d in details} != set(batch):
                raise ValueError('Incomplete Registry snapshot')
            for detail in details:
                summary = eligible[detail['recordId']]
                if detail.get('registryArn') != self.registry_arn:
                    raise ValueError('Unexpected registry')
                if any(detail.get(k) != summary.get(k) for k in ('recordVersion', 'status', 'recordType', 'registryArn')):
                    raise ValueError('Registry changed during discovery')
                rows.extend(self.normalize(detail))
        return rows

    def search(self, query):
        """Optional native search, always within this approved registry and exposure."""
        if not isinstance(query, str) or not 1 <= len(query) <= 256:
            raise ValueError('Search query must contain 1..256 characters')
        response = self.client.search_discoverable_registry_records(searchQuery=query, registryIds=[self.registry_id], maxResults=20)
        if not isinstance(response, dict) or not isinstance(response.get('registryRecords'), list) or response.get('errors'):
            raise ValueError('Invalid search envelope')
        rows = []
        for detail in response.get('registryRecords', []):
            if detail.get('registryArn') != self.registry_arn:
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
    gateway_arn: str = ""

    def records(self):
        if self.list_models is None:
            raise IntegrationNotConfigured('Model enumeration is not configured')
        # Native HTTP GET /inference/v1/models, not an invented boto3 ListModels API.
        enumerated = self.list_models()
        if (not isinstance(enumerated, dict) or not isinstance(enumerated.get('data'), list)
                or any(k in enumerated for k in ('error', 'errors'))
                or any(enumerated.get(k) for k in ('has_more', 'nextToken', 'next_token', 'next', 'next_page', 'nextPageToken', 'continuationToken'))
                or len(enumerated['data']) > 200):
            raise ValueError('Incomplete model enumeration')
        seen = set()
        for model in enumerated['data']:
            if (not isinstance(model, dict) or not isinstance(model.get('id'), str)
                    or not re.fullmatch(r'[^/\s*?\[\]]+/[^/\s*?\[\]]+', model['id'])
                    or not isinstance(model.get('owned_by'), str) or not model['owned_by']
                    or model['id'] in seen):
                raise ValueError('Invalid enumerated model')
            seen.add(model['id'])
        rows = []
        for target_id in self.target_ids:
            detail = self.client.get_gateway_target(gatewayIdentifier=self.gateway_id, targetId=target_id)
            if (not self.gateway_arn or detail.get('gatewayArn') != self.gateway_arn
                    or detail.get('targetId') != target_id or not isinstance(detail.get('name'), str)
                    or not detail['name'] or detail.get('status') != 'READY'):
                raise ValueError('Model source unavailable')
            inference = detail.get('targetConfiguration', {}).get('inference', {})
            connector = inference.get('connector', {}).get('source', {}).get('connectorId')
            provider = inference.get('provider', {})
            endpoint = urlparse(provider.get('endpoint', ''))
            mantle = (endpoint.scheme == 'https' and endpoint.netloc == f'bedrock-mantle.{self.region}.api.aws'
                      and endpoint.path in ('', '/') and not endpoint.query and not endpoint.fragment)
            if connector != 'bedrock-mantle' and not mantle:
                continue
            version = target_revision(detail)
            for model in enumerated.get('data', []):
                qualified = model.get('id', '')
                prefix, separator, model_id = qualified.partition('/')
                if not separator or prefix != detail.get('name') or any(c in model_id for c in '*?[]/') or model.get('owned_by') != 'system':
                    continue
                family = model_id.removeprefix('global.').removeprefix('us.').removeprefix('eu.').removeprefix('apac.').removeprefix('au.')
                if not family.startswith(('anthropic.claude-', 'openai.gpt-', 'claude-', 'gpt-')):
                    continue
                rid = f'model:{self.gateway_id}:{target_id}:{model_id}'
                item = approved_metadata(rid, version, 'model', 'AgentCore Model Gateway', self.exposure, 'Amazon Bedrock')
                approval = self.exposure.get(rid, {})
                if (item and approval.get('digest_format') == DIGEST_FORMAT
                        and approval.get('qualified_model_id') == qualified
                        and approval.get('approval_sha256') == model_revision(detail, model)):
                    item.update(name=qualified, description='Enumerated Bedrock inference route; execution not verified',
                                protocol='inference', model_id=qualified, target_id=target_id,
                                source_version=version, source_revision=model_revision(detail, model), connector=connector or 'bedrock-mantle provider')
                    rows.append(item)
        return rows


@dataclass
class LiveCatalog:
    models: CatalogProvider
    registry: CatalogProvider
    ttl: float = 30
    provider_metadata: CatalogProvider = None
    discovery: CatalogProvider = None
    _snapshot: list = field(default_factory=list, init=False)
    _expires: float = field(default=0, init=False)

    def source_status(self):
        # Registry-only connection is intentional: no model-list invocation grant.
        registry_connected = bool(getattr(self.registry, 'providers', [self.registry]))
        models_connected = bool(getattr(self.models, 'providers', [self.models]))
        if self.discovery is not None:
            providers = getattr(self.discovery, 'providers', [self.discovery])
            statuses = [p.status() for p in providers if hasattr(p, 'status')]
            discovery_status = {
                'connection_state': 'connected' if statuses and all(
                    s.get('connection_state') == 'connected' for s in statuses) else 'NotConnected',
                'official_data_status': 'pending' if any(
                    s.get('official_data_status') != 'complete' for s in statuses) else 'complete',
                'discovered': sum(s.get('discovered', 0) for s in statuses),
                'excluded': sum(s.get('excluded', 0) for s in statuses),
                'reason': '; '.join(s['reason'] for s in statuses if s.get('reason'))}
        else:
            discovery_status = {'connection_state': 'NotConnected',
                                'reason': 'No owner-approved discovery cache source configured'}
        return {
            'Registry': {'connection_state': 'connected' if registry_connected else 'NotConnected'},
            'ModelGateway': {'connection_state': 'connected' if models_connected else 'NotConnected',
                             'reason': '' if models_connected else 'Production model-list permission not approved; BedrockClaude and OpenAI execution routes unchanged'},
            'ProviderMetadata': {'connection_state': 'connected' if self.provider_metadata else 'NotConnected',
                                 'reason': 'Provider metadata only; not entitlement, Gateway enumeration or execution'},
            'Discovery': discovery_status,
            'FoundationLibrary': {'connection_state': 'platform-owned', 'authority': 'separate'}
        }

    def records(self):
        if (self.provider_metadata is None and self.discovery is None
                and time.monotonic() < self._expires):
            return copy.deepcopy(self._snapshot)
        try:
            items = self.models.records() + self.registry.records()
            if self.provider_metadata is not None:
                items += self.provider_metadata.records()
            if self.discovery is not None:
                items += self.discovery.records()
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


def catalog_config():
    """One server-owned configuration source for catalog and Foundation registration."""
    raw = os.getenv('NATIVE_CATALOG_CONFIG')
    if not raw and os.getenv('NATIVE_CATALOG_PACKAGED_CONFIG') == '1':
        from pathlib import Path
        raw = Path(__file__).with_name('native_catalog_source.json').read_text()
    return json.loads(raw) if raw else None


def configured_catalog(config=None, client_factory=None, model_reader_factory=None, session=None):
    """Validate ALL approval input before SDK construction; bind one session via STS."""
    if config is None:
        config = catalog_config()
        if config is None:
            return None
    if not isinstance(config, dict):
        raise ValueError('Invalid catalog configuration')
    if config.get('approved') is not True:
        return None
    registries, models = config.get('registries', []), config.get('model_gateways', [])
    if not isinstance(registries, list) or not isinstance(models, list):
        raise ValueError('Invalid catalog sources')
    metadata = config.get('provider_metadata', [])
    if not isinstance(metadata, list):
        raise ValueError('Invalid provider metadata sources')
    runtime_sources = config.get('runtime_model_routes', [])
    if not isinstance(runtime_sources, list):
        raise ValueError('Invalid Runtime route sources')
    discovery = config.get('discovery_sources', [])
    if not isinstance(discovery, list):
        raise ValueError('Invalid discovery sources')
    if not registries and not models and not metadata and not discovery and not runtime_sources:
        return None
    binding = config.get('binding', {})
    account, region = binding.get('expected_account'), binding.get('region')
    if (config.get('schema_version') != 2 or not isinstance(account, str)
            or not re.fullmatch(r'[0-9]{12}', account) or not isinstance(region, str)
            or not re.fullmatch(r'[a-z]{2}(?:-[a-z]+)+-[0-9]+', region)
            or not isinstance(binding.get('owner_approval'), str) or not binding['owner_approval'].strip()):
        raise ValueError('Owner-approved v2 source identity binding required')
    ttl = config.get('cache_seconds', 30)
    if type(ttl) not in (int, float) or not 0 <= ttl <= 60:
        raise ValueError('Invalid cache interval')
    if len(registries) + len(models) + len(metadata) + len(discovery) + len(runtime_sources) > 10:
        raise ValueError('Too many catalog sources')
    for sources, service, key in ((registries, 'agent-registry', 'registry'), (models, 'bedrock-agentcore', 'gateway')):
        for source in sources:
            if not isinstance(source, dict) or source.get('approved') is not True or source.get('region') != region:
                raise ValueError('Unapproved source region')
            identifier, arn = source.get(key + '_id'), source.get(key + '_arn')
            if (not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9-]+', identifier)
                    or not isinstance(arn, str) or not re.fullmatch(
                        rf'arn:(aws|aws-us-gov|aws-cn):{service}:{re.escape(region)}:{account}:{key}/{re.escape(identifier)}', arn)):
                raise ValueError('Exact approved source ARN required')
            validate_exposure(source.get('exposure'), f'{"registry" if key == "registry" else "model"}:{identifier}:')
            if key == 'gateway':
                targets = source.get('target_ids')
                if (not isinstance(targets, list) or not targets
                        or any(not isinstance(x, str) or not re.fullmatch(r'[A-Za-z0-9-]+', x) for x in targets)
                        or len(set(targets)) != len(targets)):
                    raise ValueError('Explicit target IDs required')
                validate_model_endpoint(source)
                for rid, approval in source['exposure'].items():
                    parts = rid.split(':')
                    qualified = approval.get('qualified_model_id')
                    if (len(parts) != 4 or parts[2] not in targets or not isinstance(qualified, str)
                            or qualified.count('/') != 1 or qualified.split('/')[1] != parts[3]):
                        raise ValueError('Exact qualified model approval required')
    from .runtime_model_catalog import validate_source as validate_runtime_source, RuntimeModelCatalog
    for source in runtime_sources:
        validate_runtime_source(source, account, region)
    if metadata:
        from .provider_model_metadata import validate_source, ProviderModelMetadata
        for source in metadata:
            validate_source(source, account, region)
    from .discovery_catalog_source import DiscoveryCatalogSource, load_discovery_cache
    for source in discovery:
        if (not isinstance(source, dict) or source.get('approved') is not True
                or not isinstance(source.get('source_id'), str)
                or not re.fullmatch(r'[A-Za-z0-9-]+', source['source_id'])
                or not isinstance(source.get('cache_path'), str) or not source['cache_path']):
            raise ValueError('Explicitly approved discovery source with source_id and cache_path required')
        # Fail loud at construction when the required cache is absent/corrupt:
        # an enabled discovery source with missing data is a specific error,
        # never a silently empty optional feature.
        load_discovery_cache(source['cache_path'])
    from botocore.config import Config
    sdk_config = Config(connect_timeout=3, read_timeout=5, retries={'max_attempts': 1})
    if session is None:
        import boto3
        session = boto3.Session(region_name=region)
    if session.region_name != region:
        raise ValueError('SDK session region mismatch')
    identity = session.client('sts', region_name=region, config=sdk_config).get_caller_identity()
    if identity.get('Account') != account:
        raise ValueError('SDK account mismatch')
    if client_factory is None:
        def client_factory(service, region):
            return session.client(service, region_name=region, config=sdk_config)
    rp, mp = [], []
    for source in registries:
        rp.append(RegistryCatalogProvider(client_factory('agent-registry', region), source['registry_id'],
                                         source['exposure'], source['registry_arn']))
    for source in models:
        reader = model_reader_factory(source) if model_reader_factory else native_model_reader(source, session)
        mp.append(ModelGatewayCatalogProvider(client_factory('bedrock-agentcore-control', region),
                  source['gateway_id'], region, source['exposure'], reader, tuple(source['target_ids']), source['gateway_arn']))
    for source in runtime_sources:
        mp.append(RuntimeModelCatalog(client_factory('bedrock-agentcore-control', region), source))
    pp = []
    if metadata:
        from .provider_model_metadata import ProviderModelMetadata
        pp = [ProviderModelMetadata(None, copy.deepcopy(source), account, region)
              for source in metadata]
    dp = [DiscoveryCatalogSource(source['source_id'], source['cache_path'],
                                 copy.deepcopy(source.get('scope', {})), today=source.get('today'))
          for source in discovery]
    return LiveCatalog(Sources(mp), Sources(rp), ttl, Sources(pp) if pp else None,
                       Sources(dp) if dp else None)


def validate_model_endpoint(source):
    gateway, region = source['gateway_id'], source['region']
    if not re.fullmatch(r'[a-zA-Z0-9-]+', gateway) or not re.fullmatch(r'[a-z0-9-]+', region):
        raise ValueError('Invalid Gateway reference')
    expected = f'https://{gateway}.gateway.bedrock-agentcore.{region}.amazonaws.com/inference/v1/models'
    if source.get('list_models_url') != expected or source.get('auth') != 'AWS_IAM':
        raise ValueError('Explicit IAM model discovery endpoint approval required')
    return expected


def native_model_reader(source, session=None):
    """IAM-only read transport to an exact separately approved Gateway endpoint."""
    from botocore.awsrequest import AWSRequest
    from botocore.auth import SigV4Auth
    import urllib.request
    expected = validate_model_endpoint(source)
    region = source['region']
    if session is None:
        raise ValueError('Verified shared SDK session required')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    def read_models():
        request = AWSRequest(method='GET', url=expected)
        credentials = session.get_credentials()
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


def data_policy_allows(component, persona):
    return (not component.get('external') or persona['external_allowed']
            or component.get('catalog') == 'journey' and persona['workspace'] in component.get('approved_external_workspaces', []))


def visibility(component, persona):
    if not component.get('approved') or component.get('discoverable') is False:
        return False
    workspaces = component.get('discoverable_workspaces')
    if component.get('fixture') is False and not valid_workspaces(workspaces):
        return False
    return (workspaces is None or persona['workspace'] in workspaces) and data_policy_allows(component, persona)


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
    public_fields = ('id', 'name', 'version', 'kind', 'provider', 'description', 'capabilities', 'data_handling', 'origin', 'refreshed_at', 'fixture', 'owner', 'protocol', 'supported', 'source_version', 'source_revision', 'descriptor_version', 'registry_record', 'descriptor_reviewed', 'execution_ready', 'execution_binding', 'artifact_status', 'parent_id', 'parent_name', 'operation', 'server_version', 'inputSchema', 'outputSchema', 'schema_purpose', 'model_id', 'target_id', 'connector', 'provenance', 'source_type', 'metadata_expires_at', 'region', 'api', 'gateway_enumeration', 'entitlement', 'native_model_id', 'approved_provider_api', 'supported_apis', 'documentation_only', 'discovery_only', 'release_date', 'source_url', 'review', 'official_data_status', 'region_availability', 'runtime_protocol', 'category', 'lifecycle', 'streaming', 'inference_types', 'input_modalities', 'output_modalities', 'recency', 'launch_date', 'launch_date_source', 'launch_date_sha256', 'pending_reason', 'recency_reason')
    public = {k: component[k] for k in (*public_fields, 'catalog', 'default_tool_ids') if k in component}
    if component.get('catalog') == 'journey' and component.get('kind') == 'model':
        public['model_id'] = component.get('binding', {}).get('model_id')
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
