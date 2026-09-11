"""Live metadata adapters. No invocation, onboarding, credential export, or mock fallback.

The SDK is the wire contract. Exposure policy is operator-owned, never supplied
by a browser or inferred from provider prose. Metadata is not execution authority.
"""
from dataclasses import dataclass
import hashlib
import json
import time
from typing import Protocol
from urllib.parse import urlparse

from fastapi import HTTPException
from .aws_adapter import IntegrationNotConfigured


class CatalogProvider(Protocol):
    def records(self) -> list[dict]: ...


def pages(method, result_key, **kwargs):
    """Bound discovery, reject partial snapshots and repeated pagination tokens."""
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
    raise IntegrationNotConfigured('Catalog pagination limit exceeded; no partial catalog published')


def approved_metadata(record_id, version, kind, origin, policy, provider):
    """Publish only an explicitly approved safe metadata projection, not descriptors."""
    entry = policy.get(record_id)
    if not entry or not entry.get('approved') or not version:
        return None
    return {'id': record_id, 'record_id': record_id, 'version': str(version),
            'kind': kind, 'provider': provider, 'name': entry['name'],
            'description': entry['description'], 'capabilities': entry.get('capabilities', []),
            'data_handling': entry['data_handling'], 'external': False,
            'approved': True, 'discoverable_workspaces': entry.get('workspaces', []),
            'requestable': entry.get('requestable', False),
            'integration_ready': False, 'origin': origin, 'refreshed_at': time.time(),
            'protocol': 'metadata-only; execution integration pending', 'fixture': False}


@dataclass
class RegistryCatalogProvider:
    client: object
    registry_id: str
    exposure: dict

    def records(self):
        records = []
        for summary in pages(self.client.list_registry_records, 'registryRecords',
                             registryId=self.registry_id, status='APPROVED', maxResults=50):
            if summary.get('status') != 'APPROVED':
                continue
            # CUSTOM has no assumed tool/agent schema. No invented classification.
            kind = {'MCP': 'mcp_server', 'AGENT_SKILLS': 'skill', 'A2A': 'agent'}.get(summary['descriptorType'])
            if not kind:
                continue
            rid = 'registry:' + summary['recordId']
            item = approved_metadata(rid, summary.get('recordVersion'), kind,
                                     'AWS Agent Registry', self.exposure, 'AWS Agent Registry')
            if item:
                records.append(item)
            # MCP tools require the actual inline MCP descriptor, not its title.
            if kind == 'mcp_server' and any(k.startswith(rid + ':tool:') for k in self.exposure):
                detail = self.client.get_registry_record(registryId=self.registry_id, recordId=summary['recordId'])
                if detail.get('status') != 'APPROVED' or detail.get('recordVersion') != summary.get('recordVersion'):
                    raise IntegrationNotConfigured('Registry changed during discovery')
                inline = detail.get('descriptors', {}).get('mcp', {}).get('tools', {}).get('inlineContent')
                if not inline:
                    continue
                content = json.loads(inline)
                for tool in content.get('tools', []):
                    item = approved_metadata(rid + ':tool:' + tool['name'], summary.get('recordVersion'),
                                             'tool', 'AWS Agent Registry', self.exposure, 'AWS Agent Registry')
                    if item:
                        records.append(item)
        return records


@dataclass
class ModelGatewayCatalogProvider:
    client: object
    gateway_id: str
    region: str
    exposure: dict

    def records(self):
        records = []
        for summary in pages(self.client.list_gateway_targets, 'items',
                             gatewayIdentifier=self.gateway_id, maxResults=50):
            if summary.get('status') != 'READY':
                continue
            detail = self.client.get_gateway_target(gatewayIdentifier=self.gateway_id, targetId=summary['targetId'])
            if detail.get('status') != 'READY':
                raise IntegrationNotConfigured('Model Gateway changed during discovery')
            provider = detail.get('targetConfiguration', {}).get('inference', {}).get('provider')
            # Connector model enumeration is not guessed. Only explicit configured models.
            if not provider:
                continue
            endpoint = urlparse(provider.get('endpoint', ''))
            if endpoint.scheme != 'https' or endpoint.netloc != f'bedrock-runtime.{self.region}.amazonaws.com' or endpoint.path not in ('', '/') or endpoint.query or endpoint.fragment:
                continue
            version = hashlib.sha256(json.dumps(provider, sort_keys=True).encode()).hexdigest()
            for operation in provider.get('operations', []):
                for model in operation.get('models', []):
                    model_id = model['model']
                    family = model_id.removeprefix('global.').removeprefix('us.').removeprefix('eu.').removeprefix('apac.')
                    if not family.startswith(('anthropic.claude-', 'openai.gpt-')):
                        continue
                    rid = f"model:{summary['targetId']}:{model_id}"
                    item = approved_metadata(rid, version, 'model', 'AgentCore Model Gateway',
                                             self.exposure, 'Amazon Bedrock')
                    if item:
                        records.append(item)
        return list({r['id']: r for r in records}.values())


@dataclass
class LiveCatalog:
    models: CatalogProvider
    registry: CatalogProvider

    def records(self):
        try:
            items = self.models.records() + self.registry.records()
            if len(items) > 200 or len({i['id'] for i in items}) != len(items):
                raise ValueError('Invalid snapshot')
            return items
        except Exception:
            # Provider exception messages can contain URLs, account IDs or credentials.
            raise HTTPException(503, 'Live catalog unavailable; no fixture fallback. Contact the platform administrator.') from None


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
    ready = component.get('integration_ready', True)
    usable = granted and ready
    requestable = not granted and component.get('requestable', True)
    public_fields = ('id', 'name', 'version', 'kind', 'provider', 'description', 'capabilities', 'data_handling', 'origin', 'refreshed_at', 'fixture')
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
