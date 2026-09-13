"""Diagnostic Runtime client: no repository access, one claimed Model send.

Uses the existing IAMTransport for both the authenticated state service and Model
send with the same workload session. Lost responses never authorize a retry.
"""
import re
import time

from .config import canonical, digest, exact_endpoint
from .context import Denied
from .opus_messages import build_request
from .transport import IAMTransport, capture_deadline


def require(condition, code):
    if not condition:
        raise Denied(code)


class DiagnosticExchange:
    def __init__(self, session, endpoint, manifest_digest):
        require(isinstance(endpoint, str) and re.fullmatch(
            r'https://[a-z0-9]+\.execute-api\.us-west-2\.amazonaws\.com/internal/diagnostic/capture', endpoint),
            'APPROVED_CAPTURE_EXCHANGE_REQUIRED')
        require(isinstance(manifest_digest, str) and re.fullmatch('[a-f0-9]{64}', manifest_digest),
                'CAPTURE_MANIFEST_REQUIRED')
        self.endpoint, self.manifest_digest = endpoint, manifest_digest
        self.transport = IAMTransport(session)

    def request(self, capture_ref, operation, role, binding_digest=None, **extra):
        body = {'capture_ref': capture_ref, 'operation': operation,
                'manifest_digest': self.manifest_digest, **extra}
        if binding_digest is not None:
            body['binding_digest'] = binding_digest
        value, _ = self.transport.send(self.endpoint, canonical(body),
            {'Accept': 'application/json'}, 5, 'execute-api')
        require(isinstance(value, dict) and all(value.get(k) == v for k, v in {
            'capture_ref': capture_ref, 'operation': operation, 'role': role,
            'manifest_digest': self.manifest_digest}.items())
            and isinstance(value.get('binding_digest'), str)
            and re.fullmatch('[a-f0-9]{64}', value['binding_digest'])
            and (binding_digest is None or value['binding_digest'] == binding_digest),
            'CAPTURE_EXCHANGE_RESPONSE_DENIED')
        return value


def capture(exchange, payload, *, sts, clock=time.time):
    require(isinstance(payload, dict) and set(payload) == {'capture_ref'}
            and isinstance(payload['capture_ref'], str) and re.fullmatch('[a-f0-9]{64}', payload['capture_ref']),
            'ONLY_DIAGNOSTIC_REFERENCE_ALLOWED')
    identity = sts.get_caller_identity()
    match = re.fullmatch(r'arn:aws:sts::(\d{12}):assumed-role/([^/]+)/[^/]+', identity.get('Arn', ''))
    require(match is not None and identity.get('Account') == match[1], 'CAPTURE_AUTHENTICATED_WORKLOAD_REQUIRED')
    role, ref = f'arn:aws:iam::{match[1]}:role/{match[2]}', payload['capture_ref']
    reserved = exchange.request(ref, 'reserve', role)
    binding = reserved['binding_digest']
    request = reserved['request']
    require(isinstance(request, dict) and set(request) == {'endpoint', 'system', 'prompt', 'max_tokens'},
            'CAPTURE_REQUEST_INVALID')
    exact_endpoint(request['endpoint'], '/bedrockrt/v1/messages')
    body = build_request('us.anthropic.claude-opus-5', request['system'], request['prompt'], request['max_tokens'])
    # The service transaction must COMMIT and a bound response arrive before send.
    claimed = exchange.request(ref, 'claim', role, binding)
    require(claimed['deadline'] == reserved['deadline'], 'CAPTURE_DEADLINE_BINDING_DENIED')
    try:
        remaining = claimed['deadline'] - clock()
        require(remaining > 0, 'CAPTURE_EXPIRED')
        now = clock()
        with capture_deadline(min(claimed['deadline'], now + min(60, remaining)), clock):
            value, _ = exchange.transport.post(request['endpoint'], body,
                {'Accept': 'application/json', 'anthropic-version': '2023-06-01'}, min(60, remaining))
    except Exception:
        # A failed completion leaves CLAIMED, not a reusable reservation.
        exchange.request(ref, 'complete', role, binding, outcome='UNKNOWN')
        raise
    model = value.get('model') if isinstance(value, dict) else None
    model = model if isinstance(model, str) and len(model) <= 200 else None
    result = exchange.request(ref, 'complete', role, binding, outcome='CAPTURED',
        observation={'response_digest': digest(value), 'observed_model': model})
    require(result.get('outcome') == 'CAPTURED', 'CAPTURE_COMPLETION_DENIED')
    return result['receipt']
