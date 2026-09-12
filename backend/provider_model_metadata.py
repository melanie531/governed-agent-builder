"""Owner-reviewed provider metadata snapshots, never Gateway enumeration/inference."""
import math
import re
import time
from dataclasses import dataclass

from .live_catalog import approved_metadata, revision, target_revision, validate_exposure

PROVENANCE = 'AWS provider metadata + configured Gateway route; execution unverified'
CARD = 'https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-haiku-4-5.html'
# Explicit official model-card mapping. No suffix stripping or family aliases.
MAPPING = {'bedrock_model_id': 'anthropic.claude-haiku-4-5-20251001-v1:0',
           'mantle_model_id': 'anthropic.claude-haiku-4-5', 'region': 'us-west-2',
           'api': 'Messages', 'gateway_path': '/v1/messages',
           'provider_path': '/anthropic/v1/messages'}


def normalize_foundation(detail):
    """Stable nonsecret control-plane projection, excluding ARN and datetime fields."""
    return {**{k: detail.get(k) for k in ('modelId', 'modelName', 'providerName',
            'inputModalities', 'outputModalities', 'responseStreamingSupported',
            'inferenceTypesSupported')}, 'lifecycle': detail.get('modelLifecycle', {}).get('status')}


def source_revision(source):
    return revision({k: source[k] for k in ('gateway_arn', 'gateway_id', 'target_id',
                     'target_revision', 'snapshot', 'scope')})


def record_id(source):
    return f"provider-model:{source['gateway_id']}:{source['target_id']}:{MAPPING['mantle_model_id']}"


def validate_source(source, account, region, now=None):
    now = time.time() if now is None else now
    if source.get('approved') is not True or source.get('region') != region or region != MAPPING['region']:
        raise ValueError('Unapproved provider metadata source/region')
    gid, tid = source.get('gateway_id'), source.get('target_id')
    if (not isinstance(gid, str) or not re.fullmatch(r'[A-Za-z0-9-]+', gid)
            or not isinstance(tid, str) or not re.fullmatch(r'[A-Za-z0-9-]+', tid)
            or source.get('gateway_arn') != f'arn:aws:bedrock-agentcore:{region}:{account}:gateway/{gid}'
            or not re.fullmatch(r'[0-9a-f]{64}', source.get('target_revision', ''))):
        raise ValueError('Exact provider target identity/digest required')
    snap = source.get('snapshot', {})
    if snap.get('mapping') != MAPPING or snap.get('provenance') != PROVENANCE:
        raise ValueError('Unsupported exact model/region/API mapping')
    if (snap.get('control_plane_api') != 'bedrock:GetFoundationModel'
            or not isinstance(snap.get('request_id'), str) or not snap['request_id']
            or snap.get('document_url') != CARD
            or not re.fullmatch(r'[0-9a-f]{64}', snap.get('document_sha256', ''))):
        raise ValueError('Provider evidence required')
    start, end = snap.get('retrieved_at'), snap.get('expires_at')
    if (any(type(x) not in (int, float) or not math.isfinite(x) for x in (start, end))
            or not start <= now < end or end - start > 86400):
        raise ValueError('Provider metadata expired or invalid (maximum 24 hours)')
    model = snap.get('foundation_model', {})
    if (model.get('modelId') != MAPPING['bedrock_model_id'] or model.get('providerName') != 'Anthropic'
            or model.get('modelName') != 'Claude Haiku 4.5' or model.get('lifecycle') != 'ACTIVE'
            or model.get('inputModalities') != ['TEXT', 'IMAGE'] or model.get('outputModalities') != ['TEXT']):
        raise ValueError('Provider model identity/metadata mismatch')
    rid = record_id(source)
    validate_exposure(source.get('exposure'), f'provider-model:{gid}:')
    if set(source['exposure']) != {rid}:
        raise ValueError('Exactly one documented model required')
    entry = source['exposure'][rid]
    if source.get('scope') != {k: entry[k] for k in ('workspaces', 'requestable', 'owner', 'data_handling')}:
        raise ValueError('Provider exposure scope drift')
    digest = source_revision(source)
    if entry['version'] != digest or entry['approval_sha256'] != digest:
        raise ValueError('Provider snapshot approval drift')


@dataclass
class ProviderModelMetadata:
    client: object
    source: dict
    account: str
    region: str

    def records(self):
        s = self.source
        validate_source(s, self.account, self.region)
        target = self.client.get_gateway_target(gatewayIdentifier=s['gateway_id'], targetId=s['target_id'])
        if (target.get('gatewayArn') != s['gateway_arn'] or target.get('targetId') != s['target_id']
                or target.get('status') != 'READY' or target_revision(target) != s['target_revision']):
            raise ValueError('Provider target drift/unavailable')
        provider = target.get('targetConfiguration', {}).get('inference', {}).get('provider', {})
        if (provider.get('endpoint') != f'https://bedrock-mantle.{self.region}.api.aws'
                or provider.get('modelMapping') != {'providerPrefix': {'strip': True, 'separator': '.'}}
                or provider.get('operations') != [{'path': MAPPING['gateway_path'],
                    'providerPath': MAPPING['provider_path'], 'models': [{'model': MAPPING['mantle_model_id']}]}]
                or not re.fullmatch(r'[A-Za-z0-9-]+', target.get('name', ''))):
            raise ValueError('Unsupported configured provider operation')
        # A slow control-plane response must not admit a snapshot that expired in flight.
        validate_source(s, self.account, self.region)
        digest = source_revision(s)
        item = approved_metadata(record_id(s), digest, 'model', 'AWS provider metadata', s['exposure'], 'Amazon Bedrock')
        item.update(name='Claude Haiku 4.5 · provider metadata', description=PROVENANCE,
                    provenance=PROVENANCE, source_type='provider_metadata', source_revision=digest,
                    source_version=digest, metadata_expires_at=s['snapshot']['expires_at'],
                    model_id=target['name']+'/'+MAPPING['mantle_model_id'], region=self.region,
                    api='Messages', gateway_enumeration='NotConnected', entitlement='unverified')
        return [item]
