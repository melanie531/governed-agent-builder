"""Opt-in diagnostic consumer. No approval producer and no product authority.

Protected records are supplied by a separately reviewed operator workflow. Hashes
link those records; they are NOT signatures. The repository's write boundary is
the trust root, exactly as for foundation_runs. Never expose this module through
the browser API or accept an admission callback from a request.
"""
import copy
import json
import re
import time

from foundation_harness.config import canonical, digest, exact_endpoint
from foundation_harness.context import Denied
from foundation_harness.opus_messages import build_request
from .foundation_runs import get, put
from scripts.opus_capture_ticket import (
    SERVICES, _clock, _money, _sum_money, dispatch_capture, reserve_capture,
)

PREFIX = 'diagnostic-capture:'
PURPOSE = 'opus-response-identity-capture-v1'
# Additional costs of THIS standalone Runtime/store adapter, not the product
# exchange Lambda/API. Reviewed zero-priced services still need source evidence.
COST_SERVICES = SERVICES | {'network', 'state_store', 'artifact', 'authentication'}


def require(condition, code):
    if not condition:
        raise Denied(code)


def record(db, kind, ref):
    require(isinstance(ref, str) and re.fullmatch(r'[a-f0-9]{64}', ref),
            'CAPTURE_RECORD_REFERENCE_REQUIRED')
    value = get(db, PREFIX + kind + ':' + ref)
    require(isinstance(value, dict) and digest(value) == ref,
            'CAPTURE_PROTECTED_RECORD_REQUIRED')
    return value


def principal(db, subject, now):
    row = db.select('principals', where=[('id', '=', subject)]).fetchone()
    require(row is not None and _clock(row['expires']) > now, 'CAPTURE_MEMBERSHIP_EXPIRED')
    value = json.loads(row['body'])
    require(value.get('id') == subject, 'CAPTURE_PRINCIPAL_BINDING_DENIED')
    return value, row['expires']


def review(db, kind, ref, now):
    """Immutable reviewer receipt + matching audit, not an 'approved' boolean."""
    value = record(db, kind, ref)
    receipt = get(db, PREFIX + 'review:' + ref)
    require(isinstance(receipt, dict) and set(receipt) == {
        'subject_digest', 'purpose', 'reviewer', 'reviewed_at', 'expires_at'},
        'CAPTURE_INDEPENDENT_REVIEW_REQUIRED')
    require(receipt['subject_digest'] == ref and receipt['purpose'] == PURPOSE
            and _clock(receipt['reviewed_at']) <= now < _clock(receipt['expires_at']),
            'CAPTURE_REVIEW_EXPIRED')
    actor, membership_expiry = principal(db, receipt['reviewer'], now)
    require(actor.get('role') == 'admin' and actor.get('workspace') == 'platform',
            'CAPTURE_PLATFORM_REVIEW_REQUIRED')
    audit = db.select('audit', where=[('actor', '=', actor['id']),
        ('action', '=', 'diagnostic_capture_reviewed'), ('resource', '=', ref),
        ('detail', '=', digest(receipt))]).fetchone()
    require(audit is not None, 'CAPTURE_REVIEW_AUDIT_REQUIRED')
    require(get(db, PREFIX + 'revoked:' + ref) is None, 'CAPTURE_REVIEW_REVOKED')
    return value, min(receipt['expires_at'], membership_expiry), actor['id']


def workload_role(sts):
    # STS is called with the SAME workload session as the transport. Request
    # payload, Runtime context headers and environment role labels are ignored.
    identity = sts.get_caller_identity()
    arn = identity.get('Arn', '')
    match = re.fullmatch(r'arn:aws:sts::(\d{12}):assumed-role/([^/]+)/[^/]+', arn)
    require(match is not None and identity.get('Account') == match[1],
            'CAPTURE_AUTHENTICATED_WORKLOAD_REQUIRED')
    return f'arn:aws:iam::{match[1]}:role/{match[2]}'


class DiagnosticAdmission:
    """Concrete ticket callback; all identity is derived from protected records."""
    def __init__(self, capture_ref, role, clock=time.time):
        require(isinstance(capture_ref, str) and re.fullmatch(r'[a-f0-9]{64}', capture_ref),
                'ONLY_DIAGNOSTIC_REFERENCE_ALLOWED')
        self.capture_ref, self.role, self.clock = capture_ref, role, clock
        self.snapshot = None

    def resolve(self, db):
        now = _clock(self.clock())
        authority, review_expiry, reviewer = review(db, 'authority', self.capture_ref, now)
        require(set(authority) == {'purpose', 'agent_id', 'version', 'definition_digest',
            'request', 'request_digest', 'runtime_ref', 'pricing_ref', 'expires_at',
            'epoch', 'policy_digest'}, 'CAPTURE_AUTHORITY_SHAPE_DENIED')
        require(authority['purpose'] == PURPOSE, 'CAPTURE_PURPOSE_DENIED')
        agent = db.select('agents', where=[('id', '=', authority['agent_id'])]).fetchone()
        require(agent is not None and type(authority['version']) is int
                and agent['current_version'] == authority['version'], 'CAPTURE_CURRENT_VERSION_REQUIRED')
        owner, member_expiry = principal(db, agent['owner'], now)
        require(owner.get('role') == 'business' and owner.get('workspace') == agent['workspace']
                and reviewer != owner['id'], 'CAPTURE_OWNER_WORKSPACE_DENIED')
        version = db.select('versions', where=[('agent', '=', agent['id']),
            ('version', '=', authority['version'])]).fetchone()
        require(version is not None, 'CAPTURE_DEFINITION_REQUIRED')
        definition = json.loads(version['body'])
        require(version['digest'] == authority['definition_digest'] == definition.get('digest')
                == digest({k: v for k, v in definition.items() if k != 'digest'})
                and (definition.get('agent_id'), definition.get('version'), definition.get('owner'),
                     definition.get('workspace')) == (agent['id'], authority['version'], owner['id'], agent['workspace']),
                'CAPTURE_DEFINITION_BINDING_DENIED')
        require(authority['epoch'] == (get(db, 'foundation-epoch') or 0)
                and authority['policy_digest'] == digest(get(db, 'policy')),
                'CAPTURE_POLICY_CHANGED')
        request = authority['request']
        require(isinstance(request, dict) and set(request) == {'endpoint', 'system', 'prompt', 'max_tokens'}
                and digest(request) == authority['request_digest'], 'CAPTURE_REQUEST_BINDING_DENIED')
        exact_endpoint(request['endpoint'], '/bedrockrt/v1/messages')
        body = build_request('us.anthropic.claude-opus-5', request['system'], request['prompt'], request['max_tokens'])
        runtime, runtime_expiry, _ = review(db, 'runtime', authority['runtime_ref'], now)
        require(set(runtime) == {'role', 'runtime_id', 'runtime_arn', 'runtime_version',
            'endpoint_name', 'manifest_digest', 'definition_digest', 'model_endpoint',
            'readback', 'isolation_evidence'}, 'CAPTURE_RUNTIME_SHAPE_DENIED')
        require(runtime['role'] == self.role and runtime['definition_digest'] == version['digest']
                and runtime['model_endpoint'] == request['endpoint']
                and isinstance(runtime['runtime_version'], str) and runtime['runtime_version'].isdigit()
                and runtime['runtime_arn'] == f"arn:aws:bedrock-agentcore:us-west-2:{self.role.split(':')[4]}:runtime/{runtime['runtime_id']}",
                'CAPTURE_RUNTIME_BINDING_DENIED')
        # Role alone cannot attest a Runtime version. Require a reviewer-bound
        # readback of role isolation and exact-version invoke restrictions, AND
        # re-read the actual runtime/endpoint before reserving (below).
        isolation = runtime['isolation_evidence']
        require(isinstance(isolation, dict) and set(isolation) == {
            'source', 'sha256', 'role', 'runtime_arn', 'runtime_version', 'invoke_policy_sha256'},
            'CAPTURE_ROLE_ISOLATION_REQUIRED')
        isolation_source = record(db, 'isolation-source', isolation['sha256'])
        require(set(isolation_source) == {'source', 'role', 'runtime_arn', 'runtime_version',
                'trust_policy', 'invoke_policy', 'attached_runtime_versions'}
                and isolation_source['source'] == isolation['source']
                and all(isolation_source[k] == runtime[k] for k in ('role', 'runtime_arn', 'runtime_version'))
                and isolation_source['attached_runtime_versions'] == [{
                    'runtime_arn': runtime['runtime_arn'], 'runtime_version': runtime['runtime_version']}]
                and isinstance(isolation_source['trust_policy'], dict) and isolation_source['trust_policy']
                and isinstance(isolation_source['invoke_policy'], dict) and isolation_source['invoke_policy']
                and digest(isolation_source['invoke_policy']) == isolation['invoke_policy_sha256'],
                'CAPTURE_ISOLATION_SOURCE_REQUIRED')
        require(all(isolation[k] == runtime[k] for k in ('role', 'runtime_arn', 'runtime_version'))
                and isinstance(isolation['source'], str) and isolation['source'].strip()
                and all(isinstance(isolation[k], str) and re.fullmatch('[a-f0-9]{64}', isolation[k])
                        for k in ('sha256', 'invoke_policy_sha256')),
                'CAPTURE_ROLE_ISOLATION_REQUIRED')
        pricing, pricing_expiry, _ = review(db, 'pricing', authority['pricing_ref'], now)
        require(set(pricing) == {'request_digest', 'runtime_ref', 'reservation_usd', 'costs', 'evidence'}
                and pricing['request_digest'] == authority['request_digest']
                and pricing['runtime_ref'] == authority['runtime_ref'], 'CAPTURE_PRICING_BINDING_DENIED')
        costs, evidence = pricing['costs'], pricing['evidence']
        require(isinstance(costs, dict) and COST_SERVICES <= costs.keys() and len(costs) <= 64
                and isinstance(evidence, dict) and set(evidence) == set(costs), 'CAPTURE_PRICE_COVERAGE_REQUIRED')
        for service, item in costs.items():
            proof = evidence[service]
            require(isinstance(proof, dict) and set(proof) == {'source', 'sha256', 'usage_bound', 'retention_seconds'}
                    and isinstance(proof['source'], str) and proof['source'].startswith('https://')
                    and isinstance(proof['sha256'], str) and re.fullmatch('[a-f0-9]{64}', proof['sha256'])
                    and isinstance(proof['usage_bound'], str) and proof['usage_bound'].strip()
                    and type(proof['retention_seconds']) is int and 0 <= proof['retention_seconds'] <= 31536000
                    and item.get('basis') == digest(proof), 'CAPTURE_PRICE_EVIDENCE_REQUIRED')
            # Read actual protected evidence content and verify its hash, rather
            # than treating an arbitrary URL/hash or status boolean as evidence.
            source = record(db, 'price-source', proof['sha256'])
            require(set(source) == {'source', 'service', 'rate_usd', 'quantity_bound',
                    'usage_bound', 'retention_seconds', 'source_excerpt'}
                    and source['source'] == proof['source'] and source['service'] == service
                    and source['usage_bound'] == proof['usage_bound']
                    and source['retention_seconds'] == proof['retention_seconds']
                    and isinstance(source['source_excerpt'], str) and source['source_excerpt'].strip(),
                    'CAPTURE_PRICE_SOURCE_REQUIRED')
            from decimal import localcontext
            with localcontext() as ctx:
                ctx.prec = 64
                require(_money(source['rate_usd']) * _money(source['quantity_bound']) == _money(item['usd']),
                        'CAPTURE_PRICE_CALCULATION_DENIED')
        total, reserve = _sum_money([_money(v['usd']) for v in costs.values()]), _money(pricing['reservation_usd'])
        require(0 < total <= reserve <= 5, 'CAPTURE_APPROVED_RESERVATION_REQUIRED')
        expiry = min(_clock(authority['expires_at']), review_expiry, member_expiry, runtime_expiry, pricing_expiry)
        require(now < expiry <= now + 3600, 'CAPTURE_AUTHORITY_EXPIRED')
        resolved = {'user_id': owner['id'], 'agent_id': agent['id'], 'workspace': agent['workspace'],
            'role': self.role, 'request': request, 'request_digest': authority['request_digest'],
            'deadline': expiry, 'costs': costs, 'cap_usd': pricing['reservation_usd'], 'runtime': runtime,
            'wire_digest': digest(body), 'runtime_ref': authority['runtime_ref']}
        snapshot = digest(resolved)
        require(self.snapshot is None or snapshot == self.snapshot, 'CAPTURE_AUTHORITY_CHANGED')
        self.snapshot = snapshot
        return copy.deepcopy(resolved)

    def __call__(self, db, role, workspace, request_digest):
        resolved = self.resolve(db)
        require((role, workspace, request_digest) == (resolved['role'], resolved['workspace'], resolved['request_digest']),
                'CAPTURE_ADMISSION_BINDING_DENIED')
        return {'allowed': True, 'user_id': resolved['user_id'], 'agent_id': resolved['agent_id']}


def verify_runtime(control, runtime):
    actual = control.get_agent_runtime(agentRuntimeId=runtime['runtime_id'],
                                       agentRuntimeVersion=runtime['runtime_version'])
    endpoint = control.get_agent_runtime_endpoint(agentRuntimeId=runtime['runtime_id'],
                                                  endpointName=runtime['endpoint_name'])
    # Exact reviewed control-plane fields, not a caller 'ready' flag. Environment
    # digests alone do not attest code; artifact/role/network must also match.
    expected = runtime['readback']
    require(isinstance(expected, dict) and set(expected) == {
        'roleArn', 'agentRuntimeArtifact', 'networkConfiguration', 'environmentVariables'},
        'CAPTURE_RUNTIME_READBACK_REQUIRED')
    require(actual.get('status') == 'READY' and actual.get('agentRuntimeArn') == runtime['runtime_arn']
            and actual.get('agentRuntimeVersion') == runtime['runtime_version']
            and expected['roleArn'] == runtime['role']
            and expected['networkConfiguration'].get('networkMode') == 'VPC'
            and expected['environmentVariables'].get('DEFINITION_DIGEST') == runtime['definition_digest']
            and expected['environmentVariables'].get('MANIFEST_DIGEST') == runtime['manifest_digest']
            and all(actual.get(k) == v for k, v in expected.items()), 'CAPTURE_RUNTIME_READBACK_DENIED')
    artifact = expected['agentRuntimeArtifact'].get('codeConfiguration', {}).get('code', {}).get('s3', {})
    require(bool(artifact.get('bucket')) and bool(artifact.get('prefix'))
            and artifact.get('versionId') not in (None, '', 'null'), 'CAPTURE_IMMUTABLE_ARTIFACT_REQUIRED')
    require(endpoint.get('status') == 'READY' and endpoint.get('name') == runtime['endpoint_name']
            and endpoint.get('agentRuntimeArn') == runtime['runtime_arn']
            and endpoint.get('liveVersion') == endpoint.get('targetVersion') == runtime['runtime_version'],
            'CAPTURE_RUNTIME_ENDPOINT_DENIED')


def capture(store, payload, *, sts, control, transport, clock=time.time):
    """Server composition seam; clients/transport are never deserialized from input."""
    require(isinstance(payload, dict) and set(payload) == {'capture_ref'}, 'ONLY_DIAGNOSTIC_REFERENCE_ALLOWED')
    admission = DiagnosticAdmission(payload['capture_ref'], workload_role(sts), clock)
    with store.tx() as db:
        resolved = admission.resolve(db)
    verify_runtime(control, resolved['runtime'])
    ticket = payload['capture_ref']  # One authority -> one ticket, including retries/crashes.
    reserve_capture(store, ticket, role=resolved['role'], workspace=resolved['workspace'],
        request_digest=resolved['request_digest'], deadline=resolved['deadline'], now=clock(),
        costs=resolved['costs'], cap_usd=resolved['cap_usd'], admission_check=admission,
        budget_scopes=('account', 'workspace:' + resolved['workspace']))
    result = dispatch_capture(store, ticket, role=resolved['role'], workspace=resolved['workspace'],
        request=resolved['request'], transport=transport, admission_check=admission, clock=clock)
    # Unknown identity is evidence only. Never return model text, raw headers,
    # credentials, runtime records, or a product/Ready approval to callers.
    value, metadata = result
    model = value.get('model') if isinstance(value, dict) else None
    model = model if isinstance(model, str) and len(model) <= 200 else None
    receipt = {'capture_ref': ticket, 'outcome': 'CAPTURED', 'purpose': PURPOSE,
        'response_digest': digest(value), 'observed_model': model,
        'request_digest': resolved['request_digest'], 'runtime_ref': resolved['runtime_ref'],
        'held_usd': resolved['cap_usd']}
    require(len(canonical(receipt)) <= 4096, 'CAPTURE_RECEIPT_BOUND')
    with store.tx() as db:
        put(db, PREFIX + 'evidence:' + ticket, receipt)
    return receipt
