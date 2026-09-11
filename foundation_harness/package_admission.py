"""Immutable references, never credentials or invocation authority.

Identity is established by AWS_IAM and the backend run ledger. This configuration
only pins the expected endpoint, role and source; it cannot mint a run binding.
"""
import re
from .config import digest


def validate_admission(settings, raw):
    fields = {'endpoint', 'manifest_digest', 'foundation_digest', 'runtime_role', 'binding_ref'}
    if not isinstance(settings, dict) or set(settings) != fields:
        raise ValueError('EXACT_ADMISSION_CONFIG_REQUIRED')
    if (settings['manifest_digest'] != digest(raw)
            or settings['foundation_digest'] != raw['foundation']['digest']):
        raise ValueError('ADMISSION_MANIFEST_BINDING_REQUIRED')
    if not isinstance(settings['endpoint'], str) or not re.fullmatch(
            r'https://[a-z0-9]+\.execute-api\.us-west-2\.amazonaws\.com/internal/foundation/exchange', settings['endpoint']):
        raise ValueError('EXACT_EXCHANGE_ENDPOINT_REQUIRED')
    if not isinstance(settings['runtime_role'], str) or not re.fullmatch(
            r'arn:aws:iam::\d{12}:role/[A-Za-z0-9+=,.@_-]+', settings['runtime_role']):
        raise ValueError('EXACT_RUNTIME_ROLE_REQUIRED')
    expected = digest({k: v for k, v in settings.items() if k != 'binding_ref'})
    if settings['binding_ref'] != expected:
        raise ValueError('ADMISSION_REFERENCE_BINDING_REQUIRED')
    return settings


def admission_config(raw, endpoint, role):
    settings = {'endpoint': endpoint, 'runtime_role': role, 'manifest_digest': digest(raw),
                'foundation_digest': raw['foundation']['digest']}
    settings['binding_ref'] = digest(settings)
    return validate_admission(settings, raw)
