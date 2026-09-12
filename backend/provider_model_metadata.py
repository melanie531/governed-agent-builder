"""Owner-reviewed native model documentation; never an inference route."""
import math
import re
import time
from dataclasses import dataclass

from .live_catalog import approved_metadata, revision, validate_exposure

PROVENANCE = 'AWS provider model documentation only; Gateway Runtime binding NOT_CONFIGURED'
CARD = 'https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-haiku-4-5.html'
NATIVE_MODEL = {'provider_api': 'bedrock-runtime',
                'native_model_id': 'anthropic.claude-haiku-4-5-20251001-v1:0',
                'supported_region': 'us-west-2',
                'supported_apis': ['Messages', 'Converse', 'Invoke'],
                'regional_inference': ['GEO', 'GLOBAL']}
POLICY = {'approved_provider_api': 'bedrock-runtime', 'allow_model_documentation': True,
          'allow_execution': False}
EXECUTION = {'status': 'NOT_CONFIGURED', 'reason': 'gateway_runtime_binding_unverified'}


def normalize_foundation(detail):
    """Preserve native GetFoundationModel evidence, not an entitlement assertion."""
    return {**{k: detail.get(k) for k in ('modelId', 'modelName', 'providerName',
            'inputModalities', 'outputModalities', 'responseStreamingSupported',
            'inferenceTypesSupported')}, 'lifecycle': detail.get('modelLifecycle', {}).get('status')}


def source_revision(source):
    return revision({k: source[k] for k in ('schema_version', 'source_id', 'region',
                     'policy', 'snapshot', 'scope', 'execution_binding')})


def record_id(source):
    return f"provider-model:{source['source_id']}:{NATIVE_MODEL['native_model_id']}"


def validate_source(source, account, region, now=None):
    now = time.time() if now is None else now
    if (not isinstance(source, dict) or source.get('schema_version') != 3
            or 'mapping' in source.get('snapshot', {})
            or any(k in source for k in ('gateway_arn', 'gateway_id', 'target_id', 'target_revision'))):
        raise ValueError('Legacy route snapshot rejected; no automatic migration or approval')
    # This schema accepts documentation, never an endpoint, credential, or route claim.
    if set(source) != {'schema_version', 'source_id', 'approved', 'region', 'policy',
                       'snapshot', 'scope', 'execution_binding', 'exposure'}:
        raise ValueError('Documentation source fields only; execution configuration forbidden')
    if (source.get('approved') is not True or source.get('region') != region
            or region != NATIVE_MODEL['supported_region'] or source.get('policy') != POLICY
            or source.get('execution_binding') != EXECUTION
            or not re.fullmatch(r'[A-Za-z0-9-]+', source.get('source_id', ''))):
        raise ValueError('Explicit Runtime-only documentation approval required; execution NOT_CONFIGURED')
    snap = source.get('snapshot', {})
    if (set(snap) != {'native_model', 'provenance', 'control_plane_api', 'request_id',
                     'document_url', 'document_sha256', 'foundation_sha256',
                     'retrieved_at', 'expires_at', 'foundation_model'}
            or snap.get('native_model') != NATIVE_MODEL or snap.get('provenance') != PROVENANCE):
        raise ValueError('Exact native model/region/API documentation required')
    if (snap.get('control_plane_api') != 'bedrock:GetFoundationModel'
            or not isinstance(snap.get('request_id'), str) or not snap['request_id']
            or snap.get('document_url') != CARD
            or not re.fullmatch(r'[0-9a-f]{64}', snap.get('document_sha256', ''))
            or snap.get('foundation_sha256') != revision(snap.get('foundation_model'))):
        raise ValueError('Provider evidence and source hashes required')
    start, end = snap.get('retrieved_at'), snap.get('expires_at')
    if (any(type(x) not in (int, float) or not math.isfinite(x) for x in (start, end))
            or not start <= now < end or end - start > 86400):
        raise ValueError('Provider metadata expired or invalid (maximum 24 hours)')
    model = snap.get('foundation_model', {})
    if (model.get('modelId') != NATIVE_MODEL['native_model_id'] or model.get('providerName') != 'Anthropic'
            or model.get('modelName') != 'Claude Haiku 4.5' or model.get('lifecycle') != 'ACTIVE'
            or model.get('inputModalities') != ['TEXT', 'IMAGE'] or model.get('outputModalities') != ['TEXT']
            or model.get('responseStreamingSupported') is not True
            or model.get('inferenceTypesSupported') != ['INFERENCE_PROFILE']):
        raise ValueError('Provider model identity/metadata mismatch')
    rid = record_id(source)
    validate_exposure(source.get('exposure'), f"provider-model:{source['source_id']}:")
    if set(source['exposure']) != {rid}:
        raise ValueError('Exactly one documented model required')
    entry = source['exposure'][rid]
    if (entry['requestable'] is not False
            or source.get('scope') != {k: entry[k] for k in ('workspaces', 'requestable', 'owner', 'data_handling')}):
        raise ValueError('Provider documentation exposure scope drift')
    digest = source_revision(source)
    if entry['version'] != digest or entry['approval_sha256'] != digest:
        raise ValueError('Provider snapshot approval drift')


@dataclass
class ProviderModelMetadata:
    client: object  # Retained constructor compatibility; documentation never uses a client.
    source: dict
    account: str
    region: str

    def records(self):
        s = self.source
        validate_source(s, self.account, self.region)
        digest = source_revision(s)
        item = approved_metadata(record_id(s), digest, 'model', 'AWS provider metadata', s['exposure'], 'Amazon Bedrock')
        item.update(name='Claude Haiku 4.5 · model documentation', description=PROVENANCE,
                    provenance=PROVENANCE, source_type='provider_metadata', source_revision=digest,
                    source_version=digest, metadata_expires_at=s['snapshot']['expires_at'],
                    native_model_id=NATIVE_MODEL['native_model_id'], region=self.region,
                    approved_provider_api=POLICY['approved_provider_api'],
                    supported_apis=list(NATIVE_MODEL['supported_apis']),
                    documentation_only=True, execution_binding=dict(EXECUTION),
                    execution_ready=False, integration_ready=False,
                    gateway_enumeration='NotConnected', entitlement='unverified')
        # Deliberately no model_id: a native documentation ID is not a Gateway route.
        return [item]
