"""Hosted exact-owner submission + independent admin review; no Runtime writes.

Candidate evidence is untrusted until admin review, not a cloud read here.
Only review publishes authority, under the repository serializable transaction.
"""
import copy
import hashlib
import json
import re
import time
from types import SimpleNamespace

from fastapi import HTTPException
from pydantic import Field

from foundation_harness.config import canonical, digest, exact_endpoint
from foundation_harness.context import Denied
from foundation_harness.opus_messages import build_request
from scripts.opus_capture_ticket import _clock, _money
from .schemas import Strict
from .foundation_runs import get
from .diagnostic_capture import (DiagnosticAdmission, PREFIX, PURPOSE, COST_SERVICES,
                                 principal, require, stored_definition_digest, verify_runtime)


class SubmitDiagnostic(Strict):
    authority: dict
    runtime: dict
    pricing: dict
    isolation_source: dict
    price_sources: dict[str, dict]
    manifest: dict
    runtime_readback: dict
    endpoint_readback: dict
    budget: dict
    reason: str = Field(min_length=10, max_length=1000)


class ReviewDiagnostic(Strict):
    candidate_ref: str = Field(pattern=r'^[a-f0-9]{64}$')
    reason: str = Field(min_length=10, max_length=1000)


def current_admin(db, actor, now):
    # actor is provided ONLY by the hosted authentication middleware.
    value, expiry = principal(db, actor['id'], now)
    require(value['role'] == actor.get('role') == 'admin'
            and value['workspace'] == actor.get('workspace') == 'platform',
            'CAPTURE_CURRENT_ADMIN_REQUIRED')
    return value, expiry


def insert_once(db, key, value):
    require(get(db, key) is None, 'CAPTURE_IMMUTABLE_RECORD_EXISTS')
    db.insert('settings', {'key': key, 'body': json.dumps(value)})


class _Preview:
    """Read-only candidate overlay. Validation never seeds protected state."""
    def __init__(self, db, records, audits):
        self.db, self.records, self.audits = db, records, audits

    def select(self, table, **kwargs):
        from .repository import Result, matches
        if table == 'settings':
            rows = [dict(r) for r in self.db.select(table)]
            rows = [r for r in rows if r['key'] not in self.records]
            rows += [{'key': k, 'body': json.dumps(v)} for k, v in self.records.items()]
        elif table == 'audit':
            rows = [dict(r) for r in self.db.select(table)] + self.audits
        else:
            return self.db.select(table, **kwargs)
        return Result(r for r in rows if matches(r, kwargs.get('where', ())))


def source_files():
    from pathlib import Path
    from scripts.package_foundation import SOURCES
    root = Path(__file__).resolve().parents[1]
    names = tuple(n for n in SOURCES if n.startswith('foundation_harness/')) + (
        'foundation_harness/diagnostic_exchange.py', 'runtime/__init__.py',
        'runtime/diagnostic_capture.py')
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in names}


def source_digest():
    return digest(source_files())


def validate_manifest(manifest, authority, runtime):
    """Validate the retained v1 build, never a request-specific replacement.

    Release/dependency pins identify the retained build inputs. verify_runtime
    checks the final immutable S3 version and environment manifest binding;
    readbacks remain reviewed attestations, not cloud/ZIP reads by this API.
    """
    expected = {
        'schema': 'gab-diagnostic-build-manifest-v1', 'purpose': PURPOSE,
        'release_sha': 'ca333d048343b13d67ba4e47debba2e27f9ace4c',
        'region': 'us-west-2', 'entrypoint': 'runtime.diagnostic_capture:create_app',
        'exchange_endpoint': manifest.get('exchange_endpoint'),
        'source_files': source_files(),
        'dependency_lock_sha256': '07fe5cbfc8acb410c3bf4eac02f87a50f45db9704e8ed8a836652c7c5fb7b5dd',
        'dependency_source_zip_sha256': 'f7a877e93567867d5546b56cab7f4047c27f43538d7698f9550f4d92abbacfbe',
        'definition_digest': authority['definition_digest'],
        'agent_id': authority['agent_id'], 'agent_version': authority['version'],
        'requested_model': 'us.anthropic.claude-opus-5', 'max_output_tokens': 256,
        'stream': False, 'thinking': 'disabled', 'tools': [], 'transport_retries': 0,
        'activation': 'disabled-pending-authenticated-authority', 'production_admission': False,
    }
    # Canonical equality also rejects bool/int and float/int substitutions.
    require(canonical(manifest) == canonical(expected), 'CAPTURE_EXECUTABLE_SOURCE_REQUIRED')
    require(isinstance(manifest['exchange_endpoint'], str) and re.fullmatch(
        r'https://[a-z0-9]+\.execute-api\.us-west-2\.amazonaws\.com/internal/diagnostic/capture',
        manifest['exchange_endpoint']) is not None, 'CAPTURE_EXCHANGE_ENDPOINT_DENIED')
    require(type(manifest['agent_version']) is int and manifest['agent_version'] > 0,
            'CAPTURE_MANIFEST_AGENT_VERSION_DENIED')
    require(runtime['manifest_digest'] == digest(manifest), 'CAPTURE_MANIFEST_BINDING_DENIED')
    code = runtime['readback']['agentRuntimeArtifact']['codeConfiguration']
    require(set(code) == {'code', 'runtime', 'entryPoint'} and code['runtime'] == 'PYTHON_3_13'
            and code['entryPoint'] == ['main.py'] and set(code['code']) == {'s3'}
            and set(code['code']['s3']) == {'bucket', 'prefix', 'versionId'}
            and all(isinstance(v, str) and v.strip() and v != 'null'
                    for v in code['code']['s3'].values()), 'CAPTURE_BUILD_ARTIFACT_REQUIRED')


def validate_evidence(data, now, membership_expiry):
    """Bound the submitted envelope without reading any protected evidence."""
    require(len(canonical(data.model_dump())) <= 128000 and len(data.reason.strip()) >= 10,
            'CAPTURE_EVIDENCE_BOUND_REQUIRED')
    a, runtime, pricing = data.authority, data.runtime, data.pricing
    require(type(a['epoch']) is int and a['epoch'] >= 0, 'CAPTURE_EXACT_EPOCH_REQUIRED')
    expiry = _clock(a['expires_at'])
    require(isinstance(runtime['role'], str) and re.fullmatch(
        r'arn:aws:iam::\d{12}:role/[A-Za-z0-9_+=,.@-]+', runtime['role']) is not None,
        'CAPTURE_EXACT_WORKLOAD_ROLE_REQUIRED')
    require(now < expiry <= min(now + 3600, membership_expiry), 'CAPTURE_REVIEW_EXPIRY_DENIED')
    validate_manifest(data.manifest, a, runtime)
    require(a['runtime_ref'] == digest(runtime) and a['pricing_ref'] == digest(pricing),
            'CAPTURE_CANDIDATE_DIGEST_DENIED')
    require(set(pricing['costs']) == set(pricing['evidence']) == set(data.price_sources) == COST_SERVICES,
            'CAPTURE_PRICE_COVERAGE_REQUIRED')
    require(all(set(item) == {'usd', 'basis'} for item in pricing['costs'].values()),
            'CAPTURE_COST_SHAPE_DENIED')
    budget = data.budget
    require(set(budget) == {'source', 'sha256', 'source_excerpt', 'total_usd',
            'capture_usd', 'studio_usd'} and isinstance(budget['source'], str)
            and bool(budget['source'].strip()) and isinstance(budget['source_excerpt'], str)
            and bool(budget['source_excerpt'].strip())
            and hashlib.sha256(budget['source_excerpt'].encode()).hexdigest() == budget['sha256'],
            'CAPTURE_BUDGET_EVIDENCE_REQUIRED')
    require(_money(budget['total_usd']) == 1 and _money(budget['studio_usd']) == _money('0.50')
            and _money(budget['capture_usd']) == _money(pricing['reservation_usd']) == _money('0.50'),
            'CAPTURE_ONE_DOLLAR_ENVELOPE_REQUIRED')
    return expiry


def validate_submission(db, actor, data, now):
    """Authorize only the current exact owner; never preview admin receipts.

    Reads are limited to the owner's membership/agent/version, current policy
    and catalog grants. Runtime/pricing/isolation authority is reviewed only by
    the actual admin through the unchanged consumer below.
    """
    from .app import validate_definition
    owner, owner_expiry = principal(db, actor['id'], now)
    require(owner.get('role') == actor.get('role') == 'business'
            and owner.get('workspace') == actor.get('workspace'),
            'CAPTURE_CURRENT_BUSINESS_OWNER_REQUIRED')
    a = data.authority
    require(set(a) == {'purpose', 'agent_id', 'version', 'definition_digest',
        'request', 'request_digest', 'runtime_ref', 'pricing_ref', 'expires_at',
        'epoch', 'policy_digest'} and a['purpose'] == PURPOSE, 'CAPTURE_AUTHORITY_SHAPE_DENIED')
    agent = db.select('agents', where=[('id', '=', a['agent_id']),
        ('owner', '=', owner['id']), ('workspace', '=', owner['workspace'])]).fetchone()
    require(agent is not None, 'CAPTURE_EXACT_OWNER_REQUIRED')
    require(type(a['version']) is int and a['version'] > 0
            and a['version'] == agent['current_version'], 'CAPTURE_CURRENT_VERSION_REQUIRED')
    version = db.select('versions', where=[('agent', '=', agent['id']),
                                         ('version', '=', a['version'])]).fetchone()
    require(version is not None, 'CAPTURE_DEFINITION_REQUIRED')
    definition = json.loads(version['body'])
    require(version['digest'] == a['definition_digest'] == definition.get('digest')
            == stored_definition_digest(definition)
            and (definition.get('agent_id'), definition.get('version'), definition.get('owner'),
                 definition.get('workspace')) == (agent['id'], a['version'], owner['id'], agent['workspace']),
            'CAPTURE_DEFINITION_BINDING_DENIED')
    require(type(a['epoch']) is int and a['epoch'] >= 0
            and a['epoch'] == (get(db, 'foundation-epoch') or 0)
            and a['policy_digest'] == digest(get(db, 'policy')), 'CAPTURE_POLICY_CHANGED')
    request = a['request']
    require(isinstance(request, dict) and set(request) == {'endpoint', 'system', 'prompt', 'max_tokens'}
            and digest(request) == a['request_digest'], 'CAPTURE_REQUEST_BINDING_DENIED')
    exact_endpoint(request['endpoint'], '/bedrockrt/v1/messages')
    build_request('us.anthropic.claude-opus-5', request['system'], request['prompt'], request['max_tokens'])
    validate_evidence(data, now, owner_expiry)
    validate_definition(db, owner, definition)


def validate(db, actor, data, now):
    """Use the actual consumer on prospective server-derived admin receipts."""
    from .app import validate_definition
    _, admin_expiry = current_admin(db, actor, now)
    expiry = validate_evidence(data, now, admin_expiry)
    a, runtime, pricing = data.authority, data.runtime, data.pricing
    records, audits = {}, []
    for kind, value in [('isolation-source', data.isolation_source),
                         *[('price-source', v) for v in data.price_sources.values()]]:
        records[PREFIX + kind + ':' + digest(value)] = value
    for kind, value in [('authority', a), ('runtime', runtime), ('pricing', pricing)]:
        ref = digest(value)
        receipt = {'subject_digest': ref, 'purpose': PURPOSE, 'reviewer': actor['id'],
                   'reviewed_at': now, 'expires_at': expiry}
        records[PREFIX + kind + ':' + ref] = value
        records[PREFIX + 'review:' + ref] = receipt
        audits.append({'actor': actor['id'], 'action': 'diagnostic_capture_reviewed',
                       'resource': ref, 'detail': digest(receipt), 'created': now})
    resolved = DiagnosticAdmission(digest(a), runtime['role'], clock=lambda: now).resolve(
        _Preview(db, records, audits))
    require(actor['id'] != resolved['user_id'], 'CAPTURE_INDEPENDENT_OWNER_REQUIRED')
    owner, _ = principal(db, resolved['user_id'], now)
    version = db.select('versions', where=[('agent', '=', a['agent_id']),
                                          ('version', '=', a['version'])]).fetchone()
    validate_definition(db, owner, json.loads(version['body']))
    verify_runtime(SimpleNamespace(get_agent_runtime=lambda **_: data.runtime_readback,
                                   get_agent_runtime_endpoint=lambda **_: data.endpoint_readback), runtime)
    require(data.runtime_readback.get('lifecycleConfiguration') == {
        'idleRuntimeSessionTimeout': 60, 'maxLifetime': 60}, 'CAPTURE_LIFECYCLE_BOUND_REQUIRED')
    return records, audits


def submit(db, actor, data, now):
    validate_submission(db, actor, data, now)
    candidate = {'evidence': data.model_dump(), 'submitter': actor['id'], 'submitted_at': now}
    ref = digest(candidate)
    insert_once(db, PREFIX + 'candidate:' + ref, candidate)
    db.insert('audit', {'actor': actor['id'], 'action': 'diagnostic_capture_submitted',
                       'resource': ref, 'detail': digest(candidate), 'created': now})
    return {'candidate_ref': ref, 'status': 'SUBMITTED_NOT_AUTHORITY'}


def review(db, actor, data, now):
    current_admin(db, actor, now)
    require(len(data.reason.strip()) >= 10, 'CAPTURE_REVIEW_REASON_REQUIRED')
    candidate = get(db, PREFIX + 'candidate:' + data.candidate_ref)
    require(isinstance(candidate, dict) and digest(candidate) == data.candidate_ref,
            'CAPTURE_CANDIDATE_REQUIRED')
    submitter, _ = principal(db, candidate['submitter'], now)
    require(actor['id'] != submitter['id'], 'CAPTURE_INDEPENDENT_REVIEWER_REQUIRED')
    require(set(candidate) == {'evidence', 'submitter', 'submitted_at'}
            and _clock(candidate['submitted_at']) <= now, 'CAPTURE_CANDIDATE_REQUIRED')
    require(db.select('audit', where=[('actor', '=', submitter['id']),
        ('action', '=', 'diagnostic_capture_submitted'), ('resource', '=', data.candidate_ref),
        ('detail', '=', digest(candidate)), ('created', '=', candidate['submitted_at'])]).fetchone()
        is not None, 'CAPTURE_SUBMISSION_AUDIT_REQUIRED')
    evidence = SubmitDiagnostic.model_validate(candidate['evidence'])
    validate_submission(db, submitter, evidence, now)
    records, audits = validate(db, actor, evidence, now)
    capture_ref = digest(evidence.authority)
    # Reference the already-approved envelope; never mint budget authority.
    # The serializable decision scan prevents reuse even with a new expiry.
    for row in db.select('settings'):
        if row['key'].startswith(PREFIX + 'decision:'):
            previous = json.loads(row['body'])
            require(previous.get('budget_evidence_sha256') != evidence.budget['sha256'],
                    'CAPTURE_ENVELOPE_ALREADY_USED')
    for key, value in records.items():
        # Content-addressed sources may be reused, never replaced; reviews may not.
        if ':price-source:' in key or ':isolation-source:' in key:
            old = get(db, key)
            if old is not None:
                require(old == value, 'CAPTURE_IMMUTABLE_SOURCE_CONFLICT')
                continue
        insert_once(db, key, value)
    for row in audits:
        db.insert('audit', row)
    decision = {'candidate_ref': data.candidate_ref, 'capture_ref': capture_ref,
                'reviewer': actor['id'], 'reviewed_at': now, 'reason': data.reason,
                'budget_evidence_sha256': evidence.budget['sha256']}
    insert_once(db, PREFIX + 'decision:' + data.candidate_ref, decision)
    db.insert('audit', {'actor': actor['id'], 'action': 'diagnostic_capture_published',
                       'resource': data.candidate_ref, 'detail': digest(decision), 'created': now})
    # Verify persisted prospective writes, not only the overlay. Failure rolls back ALL writes.
    DiagnosticAdmission(capture_ref, evidence.runtime['role'], clock=lambda: now).resolve(db)
    return {'capture_ref': capture_ref, 'candidate_ref': data.candidate_ref,
            'status': 'REVIEWED_NOT_INVOKED'}


def handle(store, actor, data, *, session_hash, clock=time.time):
    """Server-only composition; caller must supply middleware identity, never JSON identity."""
    try:
        with store.tx() as db:
            now = _clock(clock())
            require(db.select('hosted_sessions', where=[('id_hash', '=', session_hash),
                ('subject', '=', actor['id']), ('expires', '>', now)]).fetchone() is not None,
                'CAPTURE_CURRENT_HOSTED_SESSION_REQUIRED')
            if isinstance(data, SubmitDiagnostic):
                return submit(db, copy.deepcopy(actor), data, now)
            if isinstance(data, ReviewDiagnostic):
                return review(db, copy.deepcopy(actor), data, now)
            raise Denied('CAPTURE_OPERATOR_OPERATION_DENIED')
    except Denied as exc:
        raise HTTPException(409, str(exc)) from None
    except (ValueError, KeyError, TypeError, AttributeError):
        # Do not expose private evidence, payloads, pricing or readbacks in errors.
        raise HTTPException(409, 'CAPTURE_OPERATOR_VALIDATION_DENIED') from None


def inspect_candidate(store, actor, candidate_ref, *, session_hash):
    """Independent reviewer reads the exact stored candidate, never a replacement payload."""
    try:
        now = time.time()
        with store.tx() as db:
            current_admin(db, actor, now)
            require(db.select('hosted_sessions', where=[('id_hash', '=', session_hash),
                ('subject', '=', actor['id']), ('expires', '>', now)]).fetchone() is not None,
                'CAPTURE_CURRENT_HOSTED_SESSION_REQUIRED')
            if not re.fullmatch('[a-f0-9]{64}', candidate_ref):
                raise HTTPException(404, 'CAPTURE_CANDIDATE_NOT_FOUND')
            candidate = get(db, PREFIX + 'candidate:' + candidate_ref)
            if not isinstance(candidate, dict) or digest(candidate) != candidate_ref:
                raise HTTPException(404, 'CAPTURE_CANDIDATE_NOT_FOUND')
            return candidate
    except Denied as exc:
        raise HTTPException(409, str(exc)) from None
    except (ValueError, KeyError, TypeError, AttributeError):
        raise HTTPException(409, 'CAPTURE_OPERATOR_VALIDATION_DENIED') from None
