"""Offline signed synthetic JWT -> real producer -> real admission consumer.

No protected approval fixtures, cloud calls, credentials, or model inference.
"""
import copy
import hashlib
import json
import time

import pytest
from fastapi import HTTPException

from backend import diagnostic_approval as producer
from backend.diagnostic_capture import DiagnosticAdmission, COST_SERVICES, PREFIX, PURPOSE
from backend.diagnostic_exchange import exchange
from backend.foundation_runs import get
from foundation_harness.config import digest
from tests.conftest import create, login
from tests.test_serverless import cloud, sign_in

SUBMIT = '/api/admin/diagnostic-capture/submissions'
REVIEW = '/api/admin/diagnostic-capture/reviews'
ACCOUNT = '9988' '77665544'
ROLE = f'arn:aws:iam::{ACCOUNT}:role/synthetic-capture'
ARN = f'arn:aws:bedrock-agentcore:us-west-2:{ACCOUNT}:runtime/synthetic-abc'
URL = 'https://gab-foundation-model-m0-example.gateway.bedrock-agentcore.us-west-2.amazonaws.com/bedrockrt/v1/messages'


def evidence(definition, store):
    request = {'endpoint': URL, 'system': 'Synthetic diagnostic', 'prompt': 'Hello', 'max_tokens': 256}
    manifest = {'purpose': PURPOSE, 'source_digest': producer.source_digest(),
                'definition_digest': definition['digest'], 'request_digest': digest(request)}
    isolation = {'source': 'https://example.invalid/isolation-readback', 'role': ROLE,
        'runtime_arn': ARN, 'runtime_version': '1', 'trust_policy': {'Statement': [{
            'Effect': 'Allow', 'Principal': {'Service': 'bedrock-agentcore.amazonaws.com'},
            'Action': 'sts:AssumeRole', 'Condition': {'ArnEquals': {'aws:SourceArn': ARN}}}]},
        'invoke_policy': {'Statement': [{'Effect': 'Allow', 'Action': 'bedrock-agentcore:InvokeAgentRuntime',
                                       'Resource': ARN}]},
        'attached_runtime_versions': [{'runtime_arn': ARN, 'runtime_version': '1'}]}
    runtime = {'role': ROLE, 'runtime_id': 'synthetic-abc', 'runtime_arn': ARN, 'runtime_version': '1',
        'endpoint_name': 'DEFAULT', 'manifest_digest': digest(manifest),
        'definition_digest': definition['digest'], 'model_endpoint': URL,
        'readback': {'roleArn': ROLE, 'agentRuntimeArtifact': {'codeConfiguration': {'code': {'s3': {
            'bucket': 'synthetic-private', 'prefix': 'diagnostic.zip', 'versionId': 'synthetic-version'}}}},
            'networkConfiguration': {'networkMode': 'VPC'}, 'environmentVariables': {
                'DEFINITION_DIGEST': definition['digest'], 'MANIFEST_DIGEST': digest(manifest)}},
        'isolation_evidence': {'source': isolation['source'], 'sha256': digest(isolation), 'role': ROLE,
            'runtime_arn': ARN, 'runtime_version': '1', 'invoke_policy_sha256': digest(isolation['invoke_policy'])}}
    sources, proofs, costs = {}, {}, {}
    for service in sorted(COST_SERVICES):
        source = {'source': 'https://example.invalid/pricing', 'service': service, 'rate_usd': '0.001',
            'quantity_bound': '10', 'usage_bound': '10 synthetic units only', 'retention_seconds': 60,
            'source_excerpt': 'SYNTHETIC pricing fixture, not actual approval'}
        sources[service] = source
        proofs[service] = {k: source[k] for k in ('source', 'usage_bound', 'retention_seconds')}
        proofs[service]['sha256'] = digest(source)
        costs[service] = {'usd': '0.01', 'basis': digest(proofs[service])}
    pricing = {'request_digest': digest(request), 'runtime_ref': digest(runtime),
               'reservation_usd': '0.50', 'costs': costs, 'evidence': proofs}
    with store.tx() as db:
        policy_digest = digest(get(db, 'policy'))
    authority = {'purpose': PURPOSE, 'agent_id': definition['agent_id'], 'version': definition['version'],
        'definition_digest': definition['digest'], 'request': request, 'request_digest': digest(request),
        'runtime_ref': digest(runtime), 'pricing_ref': digest(pricing), 'expires_at': time.time()+300,
        'epoch': 0, 'policy_digest': policy_digest}
    text = 'Synthetic budget evidence, never a real approval.'
    return {'authority': authority, 'runtime': runtime, 'pricing': pricing, 'isolation_source': isolation,
        'price_sources': sources, 'manifest': manifest,
        'runtime_readback': {'status': 'READY', 'agentRuntimeArn': ARN, 'agentRuntimeVersion': '1',
                            **copy.deepcopy(runtime['readback']),
                            'lifecycleConfiguration': {'idleRuntimeSessionTimeout': 60, 'maxLifetime': 60}},
        'endpoint_readback': {'status': 'READY', 'name': 'DEFAULT', 'agentRuntimeArn': ARN,
                             'liveVersion': '1', 'targetVersion': '1'},
        'budget': {'source': 'https://example.invalid/budget', 'sha256': hashlib.sha256(text.encode()).hexdigest(),
                   'source_excerpt': text, 'total_usd': '1', 'capture_usd': '0.50', 'studio_usd': '0.50'},
        'reason': 'Review synthetic evidence without cloud execution'}


@pytest.fixture
def candidate(cloud, payload):
    app, client, _ = cloud
    sign_in(cloud)
    definition = create(client, payload)
    body = evidence(definition, app.state.store)
    sign_in(cloud, 'synthetic-submitter', 'studio-admin')
    return body


def session_hash(cloud):
    from backend.hosted_auth import sha, SESSION_COOKIE
    return sha(cloud[1].cookies.get(SESSION_COOKIE))


def states(store):
    with store.tx() as db:
        return {table: [dict(r) for r in db.select(table)] for table in
                ('settings', 'audit', 'grants', 'agents', 'versions')}


def submit(cloud, body):
    response = cloud[1].post(SUBMIT, json=body)
    assert response.status_code == 201, response.text
    return response.json()['candidate_ref']


def reviewed(cloud, body):
    ref = submit(cloud, body)
    sign_in(cloud, 'synthetic-reviewer', 'studio-admin')
    response = cloud[1].post(REVIEW, json={'candidate_ref': ref, 'reason': 'Independent synthetic review'})
    assert response.status_code == 200, response.text
    return ref, response.json()['capture_ref']


def test_authenticated_producer_to_consumer_and_exchange(cloud, candidate):
    from types import SimpleNamespace
    store = cloud[0].state.store
    before = states(store)
    ref = submit(cloud, candidate)
    with store.tx() as db:
        assert get(db, PREFIX+'authority:'+digest(candidate['authority'])) is None
        with pytest.raises(RuntimeError):
            DiagnosticAdmission(digest(candidate['authority']), ROLE).resolve(db)
    sign_in(cloud, 'synthetic-reviewer', 'studio-admin')
    inspection = cloud[1].get(SUBMIT + '/' + ref)
    assert inspection.status_code == 200
    assert digest(inspection.json()) == ref
    assert inspection.json()['submitter'] == 'synthetic-submitter'
    assert inspection.json()['evidence'] == candidate
    response = cloud[1].post(REVIEW, json={'candidate_ref': ref, 'reason': 'Independent synthetic review'})
    assert response.status_code == 200, response.text
    capture_ref = response.json()['capture_ref']
    with store.tx() as db:
        resolved = DiagnosticAdmission(capture_ref, ROLE).resolve(db)
        assert resolved['cap_usd'] == '0.50'
        receipts = [get(db, PREFIX+'review:'+digest(candidate[k])) for k in ('authority', 'runtime', 'pricing')]
        assert all(r['reviewer'] == 'synthetic-reviewer' for r in receipts)
        assert len([r for r in db.select('audit') if r['action'] == 'diagnostic_capture_reviewed']) == 3
    after = states(store)
    for table in ('grants', 'agents', 'versions'):
        assert after[table] == before[table]
    assert not any(r['key'].startswith(('foundation-approved:', 'foundation-run:', 'opus-capture:')) for r in after['settings'])
    control = SimpleNamespace(get_agent_runtime=lambda **_: candidate['runtime_readback'],
                             get_agent_runtime_endpoint=lambda **_: candidate['endpoint_readback'])
    args = {'principal_arn': f'arn:aws:sts::{ACCOUNT}:assumed-role/synthetic-capture/synthetic-session',
            'control': control}
    body = {'capture_ref': capture_ref, 'manifest_digest': candidate['runtime']['manifest_digest'], 'operation': 'reserve'}
    reservation = exchange(store, body=body, **args)
    claimed = exchange(store, body={**body, 'operation': 'claim', 'binding_digest': reservation['binding_digest']}, **args)
    assert claimed['operation'] == 'claim'
    with pytest.raises(ValueError):
        exchange(store, body=body, **args)


@pytest.mark.parametrize('who', ['business', 'crossuser', 'self', 'anonymous', 'csrf', 'spoof'])
def test_auth_boundary(cloud, candidate, who):
    ref = submit(cloud, candidate)
    if who == 'business': sign_in(cloud)
    elif who == 'crossuser': sign_in(cloud, 'another-owner', 'studio-operations')
    elif who == 'anonymous': cloud[1].cookies.clear()
    elif who == 'csrf': cloud[1].headers.pop('X-CSRF-Token')
    elif who == 'spoof':
        sign_in(cloud)
        cloud[1].headers.update({'X-User-Id': 'synthetic-reviewer', 'X-Role': 'admin'})
    before = states(cloud[0].state.store)
    response = cloud[1].post(REVIEW, json={'candidate_ref': ref, 'reason': 'Synthetic review rejection'})
    assert response.status_code in (401, 403, 409)
    assert states(cloud[0].state.store) == before


@pytest.mark.parametrize('fault', ['expiry', 'digest', 'priceunknown', 'pricecalculation', 'runtime',
    'endpoint', 'source', 'manifest', 'isolation', 'budget', 'lifecycle', 'owner', 'grant', 'payloadidentity'])
def test_invalid_candidate_has_no_writes(cloud, candidate, fault):
    body = copy.deepcopy(candidate)
    if fault == 'expiry': body['authority']['expires_at'] = time.time()-1
    elif fault == 'digest': body['authority']['request_digest'] = 'a'*64
    elif fault == 'priceunknown': body['pricing']['costs']['model_input']['usd'] = None
    elif fault == 'pricecalculation': body['price_sources']['model_input']['rate_usd'] = '0.9'
    elif fault == 'runtime': body['runtime_readback']['agentRuntimeVersion'] = '2'
    elif fault == 'endpoint': body['endpoint_readback']['liveVersion'] = '2'
    elif fault == 'source': body['manifest']['source_digest'] = 'a'*64
    elif fault == 'manifest': body['runtime']['manifest_digest'] = 'b'*64
    elif fault == 'isolation': body['isolation_source']['attached_runtime_versions'].append({'runtime_arn': ARN, 'runtime_version': '2'})
    elif fault == 'budget': body['budget']['capture_usd'] = '5'
    elif fault == 'lifecycle': body['runtime_readback']['lifecycleConfiguration']['maxLifetime'] = 3600
    elif fault == 'owner':
        with cloud[0].state.store.tx() as db:
            db.update('agents', {'owner': 'synthetic-submitter'}, where=[('id', '=', body['authority']['agent_id'])])
    elif fault == 'grant':
        with cloud[0].state.store.tx() as db:
            db.delete('grants', where=[('persona', '=', 'subject-a')])
    elif fault == 'payloadidentity': body['reviewer'] = 'synthetic-reviewer'
    # Rebind outer content addresses so failures exercise semantic validation,
    # not merely an early stale hash check (the explicit digest fault stays stale).
    if fault in ('priceunknown', 'pricecalculation', 'budget'):
        if fault == 'pricecalculation':
            proof = body['pricing']['evidence']['model_input']
            proof['sha256'] = digest(body['price_sources']['model_input'])
            body['pricing']['costs']['model_input']['basis'] = digest(proof)
        body['authority']['pricing_ref'] = digest(body['pricing'])
    if fault == 'isolation':
        body['runtime']['isolation_evidence']['sha256'] = digest(body['isolation_source'])
        body['pricing']['runtime_ref'] = digest(body['runtime'])
        body['authority']['runtime_ref'] = digest(body['runtime'])
        body['authority']['pricing_ref'] = digest(body['pricing'])
    before = states(cloud[0].state.store)
    response = cloud[1].post(SUBMIT, json=body)
    assert response.status_code in (403, 409, 422), response.text
    assert states(cloud[0].state.store) == before


@pytest.mark.parametrize('fault', ['membership', 'owner_membership', 'grant', 'epoch', 'policy', 'version', 'expired', 'submission_audit'])
def test_review_revalidates_current_state(cloud, candidate, fault):
    ref = submit(cloud, candidate)
    sign_in(cloud, 'synthetic-reviewer', 'studio-admin')
    store = cloud[0].state.store
    with store.tx() as db:
        if fault in ('membership', 'owner_membership'):
            db.update('principals', {'expires': time.time()-1}, where=[('id', '=', 'synthetic-submitter' if fault == 'membership' else 'subject-a')])
        elif fault == 'grant': db.delete('grants', where=[('persona', '=', 'subject-a')])
        elif fault in ('epoch', 'policy'):
            db.insert('settings', {'key': 'foundation-epoch' if fault == 'epoch' else 'policy', 'body': '99'}, upsert=True)
        elif fault == 'version': db.update('agents', {'current_version': 2}, where=[('id', '=', candidate['authority']['agent_id'])])
        elif fault == 'submission_audit': db.delete('audit', where=[('action', '=', 'diagnostic_capture_submitted')])
    before = states(store)
    if fault == 'expired':
        with pytest.raises(HTTPException):
            producer.handle(store, {'id': 'synthetic-reviewer', 'role': 'admin', 'workspace': 'platform'},
                producer.ReviewDiagnostic(candidate_ref=ref, reason='Synthetic expiry rejection'),
                session_hash=session_hash(cloud), clock=lambda: candidate['authority']['expires_at']+1)
    else:
        response = cloud[1].post(REVIEW, json={'candidate_ref': ref, 'reason': 'Synthetic state drift rejection'})
        assert response.status_code in (403, 409), response.text
    assert states(store) == before


def test_immutable_review_audit_and_budget_replay(cloud, candidate):
    ref, _ = reviewed(cloud, candidate)
    before = states(cloud[0].state.store)
    response = cloud[1].post(REVIEW, json={'candidate_ref': ref, 'reason': 'Attempt to overwrite review'})
    assert response.status_code == 409
    assert states(cloud[0].state.store) == before
    sign_in(cloud, 'synthetic-submitter', 'studio-admin')
    candidate['authority']['expires_at'] += 1
    second = submit(cloud, candidate)
    sign_in(cloud, 'synthetic-reviewer', 'studio-admin')
    before = states(cloud[0].state.store)
    assert cloud[1].post(REVIEW, json={'candidate_ref': second, 'reason': 'Attempt second capture'}).status_code == 409
    assert states(cloud[0].state.store) == before


@pytest.mark.parametrize('adapter', ['dynamo', 'sqlite'])
def test_atomic_failure_rolls_back_records_receipts_audit(cloud, candidate, tmp_path, monkeypatch, adapter):
    ref = submit(cloud, candidate)
    sign_in(cloud, 'synthetic-reviewer', 'studio-admin')
    store = cloud[0].state.store
    if adapter == 'sqlite':
        from backend.store import Store
        local = Store(str(tmp_path/'producer.sqlite'), seed_personas=False)
        with store.tx() as source, local.tx() as target:
            target.execute('CREATE TABLE principals(id TEXT PRIMARY KEY, body TEXT, expires REAL)')
            target.execute('CREATE TABLE hosted_sessions(id_hash TEXT PRIMARY KEY, subject TEXT, access_token TEXT, csrf TEXT, expires REAL)')
            for table in ('settings', 'audit', 'principals', 'hosted_sessions', 'agents', 'versions', 'grants', 'components', 'foundations'):
                target.delete(table)
                for row in source.select(table): target.insert(table, dict(row))
        store = local
    before = states(store)
    original = producer.insert_once
    def fail(db, key, value):
        if key.startswith(PREFIX+'decision:'): raise RuntimeError('synthetic late write failure')
        return original(db, key, value)
    monkeypatch.setattr(producer, 'insert_once', fail)
    with pytest.raises(RuntimeError, match='late write failure'):
        producer.handle(store, {'id': 'synthetic-reviewer', 'role': 'admin', 'workspace': 'platform'},
            producer.ReviewDiagnostic(candidate_ref=ref, reason='Synthetic rollback verification'),
            session_hash=session_hash(cloud))
    assert states(store) == before


def test_dynamo_cas_only_one_publisher(cloud, candidate):
    from backend.dynamo_store import DynamoUnit
    ref = submit(cloud, candidate)
    sign_in(cloud, 'synthetic-reviewer', 'studio-admin')
    store = cloud[0].state.store
    first, second = DynamoUnit(store.table), DynamoUnit(store.table)
    actor = {'id': 'synthetic-reviewer', 'role': 'admin', 'workspace': 'platform'}
    data = producer.ReviewDiagnostic(candidate_ref=ref, reason='Synthetic concurrent review')
    producer.review(first, actor, data, time.time())
    producer.review(second, actor, data, time.time())
    first.commit()
    before = states(store)
    with pytest.raises(HTTPException): second.commit()
    assert states(store) == before


def test_demo_admin_cannot_publish(client, candidate):
    login(client, 'admin')
    assert client.post(SUBMIT, json=candidate).status_code == 403
    assert client.post(REVIEW, json={'candidate_ref': 'a'*64, 'reason': 'Synthetic local admin'}).status_code == 403


def test_ready_without_target_version_publishes_no_budget_authority(cloud, candidate):
    del candidate['endpoint_readback']['targetVersion']
    _, capture_ref = reviewed(cloud, candidate)
    with cloud[0].state.store.tx() as db:
        assert DiagnosticAdmission(capture_ref, ROLE).resolve(db)
        keys = [r['key'] for r in db.select('settings')]
        assert not any(k.startswith((PREFIX+'budget-authority:', 'budget-approval:')) for k in keys)


@pytest.mark.parametrize('fault', ['boolean_epoch', 'boolean_version', 'boolean_expiry',
    'boolean_money', 'blank_reason', 'oversized_evidence', 'explicit_null_target'])
def test_strict_payload_rejected_without_writes(cloud, candidate, fault):
    body = copy.deepcopy(candidate)
    if fault == 'boolean_epoch': body['authority']['epoch'] = False
    elif fault == 'boolean_version': body['authority']['version'] = True
    elif fault == 'boolean_expiry': body['authority']['expires_at'] = True
    elif fault == 'boolean_money': body['budget']['total_usd'] = True
    elif fault == 'blank_reason': body['reason'] = ' '*20
    elif fault == 'oversized_evidence': body['runtime_readback']['unexpected'] = 'x'*128001
    elif fault == 'explicit_null_target': body['endpoint_readback']['targetVersion'] = None
    before = states(cloud[0].state.store)
    expected = 413 if fault == 'oversized_evidence' else 409
    assert cloud[1].post(SUBMIT, json=body).status_code == expected
    assert states(cloud[0].state.store) == before


def test_candidate_inspection_rejects_missing_session_safely(cloud, candidate):
    ref = submit(cloud, candidate)
    with pytest.raises(HTTPException) as error:
        producer.inspect_candidate(cloud[0].state.store,
            {'id': 'synthetic-submitter', 'role': 'admin', 'workspace': 'platform'},
            ref, session_hash='not-a-session')
    assert error.value.status_code == 409
    assert error.value.detail == 'CAPTURE_CURRENT_HOSTED_SESSION_REQUIRED'


def test_review_rejects_client_identity_and_approval_boolean(cloud, candidate):
    ref = submit(cloud, candidate)
    sign_in(cloud, 'synthetic-reviewer', 'studio-admin')
    before = states(cloud[0].state.store)
    response = cloud[1].post(REVIEW, json={'candidate_ref': ref,
        'reason': 'Independent synthetic review', 'approved': True, 'reviewer': 'forged'})
    assert response.status_code == 422
    assert states(cloud[0].state.store) == before
