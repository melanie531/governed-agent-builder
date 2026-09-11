"""Source integration, synthetic SDK and Moto only; never cloud proof."""
from decimal import Decimal

from tests.test_foundation_executor import config, setup


def test_runtime_handler_requires_persistent_resolved_reservation():
    from foundation_harness.context import ResolvedEntry
    from runtime.custom_foundation.main import RuntimeHandler
    raw = config()
    raw['limits']['maxModelCalls'] = 1
    engine, binding, authority, transport, _ = setup(raw)
    transport.requires_reservation = True
    calls = []
    class Reservation:
        handle = binding.run_ref
        amount_usd = Decimal('0.01')
        def claim(self, operation, call_id):
            calls.append((operation, call_id))
        def settle(self, usage):
            calls.append(('settle', usage))
    entry = ResolvedEntry(binding, 'Stored input', engine.config.limits, Reservation())
    result = RuntimeHandler(engine, lambda ref, context: entry)({'run_ref': binding.run_ref}, object())
    assert result['status'] == 'SUCCEEDED'
    assert result['execution_status'] == 'EXECUTION_SUCCEEDED'
    assert result['release_status'] == 'BLOCKED'
    assert result['release_code'] == 'EVIDENCE_INCOMPLETE'
    assert len(transport.calls) == 1
    assert ('model', 'model-1') in calls
    assert calls[-1][0] == 'settle'

import io
import json
import time
from types import SimpleNamespace

import pytest
from botocore.session import Session
from botocore.validate import validate_parameters
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.foundation_jobs import FoundationJobs
from backend.foundation_deployment import FoundationDeployment
from backend.runtime_deployment import DeploymentPolicy
from backend import foundation_runs as runs
from foundation_harness.config import digest
from foundation_harness.context import Denied
from scripts.package_foundation import save_config, source_digest
from tests.conftest import ORIGIN, login, create
from tests.test_runtime_deployment import Control, ACCOUNT, ROLE
from tests.test_serverless import cloud, sign_in


class LiveControl(Control):
    def get_agent_runtime(self, **kw):
        response = super().get_agent_runtime(**kw)
        return {**response, 'environmentVariables': self.calls[0][1]['environmentVariables']}


class Runtime:
    def __init__(self, store):
        self.store, self.calls = store, []
    def invoke_agent_runtime(self, **kw):
        validate_parameters(kw, Session().get_service_model('bedrock-agentcore').operation_model('InvokeAgentRuntime').input_shape)
        self.calls.append(kw)
        run_ref = json.loads(kw['payload'])['run_ref']
        with self.store.tx() as db:
            row = runs.get(db, 'foundation-run:' + run_ref)
            assert row['state'] == 'DISPATCHED'
            assert runs.get(db, 'foundation-budget:account')['held_usd'] == '0.01'
            principal = f'arn:aws:sts::{ACCOUNT}:assumed-role/synthetic-runtime/session'
            body = {'run_ref': run_ref, 'manifest_digest': row['manifest_digest']}
            runs.exchange(db, principal_arn=principal, body={**body, 'operation': 'redeem'})
            runs.exchange(db, principal_arn=principal, body={**body, 'operation': 'settle',
                'usage': {'input_tokens': 5, 'output_tokens': 2}})
        return {'response': io.BytesIO(json.dumps({'run_ref': run_ref,
            'manifest_digest': row['manifest_digest'], 'runtime_version': '3',
            'execution_status': 'EXECUTION_SUCCEEDED', 'trace_id': 'a'*32,
            'usage': {'input_tokens': 5, 'output_tokens': 2}}).encode()),
            'statusCode': 200, 'runtimeSessionId': kw['runtimeSessionId'],
            'ResponseMetadata': {'RequestId': 'synthetic-sdk-response'}}


def install(store, definition, tmp_path, evidence=False):
    raw = config()
    raw['foundation']['digest'] = source_digest()
    raw['limits']['maxModelCalls'] = 1
    raw['systemPrompt'] = [{'text': definition['prompt']}]
    raw['model']['id'] = definition['model_id']
    raw['skills'][0]['id'] = definition['skills'][0]
    raw['evaluation']['dataset']['digest'] = digest(definition['dataset'])
    raw['evaluation']['rubric']['digest'] = digest(definition['rubric'])
    saved = save_config(raw, tmp_path)
    network = {'networkMode': 'VPC', 'networkModeConfig': {'subnets': ['subnet-synthetic'], 'securityGroups': ['sg-synthetic']}}
    approved = {'config': raw, 'manifest_digest': saved.stem, 'saved_manifest': str(saved),
                'definition_digest': definition['digest'], 'owner': definition['owner'],
                'workspace': definition['workspace'], 'epoch': 0, 'policy_version': 1,
                'expires_at': time.time()+3600, 'tool_ids': definition['tools'],
                'package_digest': 'e'*64, 'artifact_key': 'approved/ready.zip',
                'artifact_version': 'synthetic-version', 'artifact_source_digest': source_digest(),
                'role': ROLE, 'network': network, 'reservation_usd': '0.01',
                'runtime_admission_reviewed': True,
                'cost_envelope': {'reviewed': True, 'maximum_usd': '0.01', 'rate_card_digest': 'f'*64,
                    'dimensions': ['model', 'gateway', 'policy', 'runtime', 'exchange', 'telemetry', 'storage']}}
    with store.tx() as db:
        runs.put(db, 'foundation-approved:' + definition['digest'], approved)
    control = LiveControl()
    runtime = Runtime(store)
    deployment = FoundationDeployment(control, DeploymentPolicy('us-west-2', ACCOUNT,
        frozenset([ROLE]), 'synthetic-artifacts', allow_mutations=True), network)
    def readback(row):
        return {'run_ref': row['run_ref'], 'definition_digest': definition['digest'],
                'manifest_digest': saved.stem, 'runtime_version': '3', 'epoch': 0, 'policy_version': 1,
                'dataset_digest': digest(definition['dataset']), 'rubric_digest': digest(definition['rubric']),
                'trace_readback': True, 'otel_delivery': True, 'trace_ids': ['a'*32],
                'evaluation_passed': True, 'required_evaluations_complete': True,
                'evaluation_ids': ['synthetic-evaluation']}
    service = FoundationJobs(deployment, runtime, SimpleNamespace(account=ACCOUNT, verify=lambda: None),
        enabled=True, evidence_reader=readback if evidence else None, artifact_reader=lambda approved: None)
    return service, control, runtime


@pytest.mark.parametrize('evidence', [False, True, 'wrong-version', 'wrong-trace', 'missing-eval'])
def test_studio_api_to_sdk_ready_runtime_evidence_gate(cloud, payload, tmp_path, evidence):
    old_app, client, _ = cloud
    sign_in(cloud)
    payload['dataset'] = payload['dataset'][:1]
    definition = create(client, payload)
    service, control, runtime = install(old_app.state.store, definition, tmp_path, evidence)
    if isinstance(evidence, str):
        readback = service.evidence_reader
        overrides = {'wrong-version': {'runtime_version': '999'},
                     'wrong-trace': {'trace_ids': ['b'*32]},
                     'missing-eval': {'evaluation_ids': []}}
        service.evidence_reader = lambda row: {**readback(row), **overrides[evidence]}
    passed = evidence is True
    app = create_app(repository=old_app.state.store, worker_enabled=False, foundation_jobs=service)
    app.state.hosted_auth.keys = old_app.state.hosted_auth.keys
    with TestClient(app, base_url=str(client.base_url), cookies=client.cookies, headers=client.headers) as c:
        result = c.post(f"/api/agents/{definition['agent_id']}/deploy-test", json={
            'version': 1, 'idempotency_key': 'synthetic-live-1', 'execution_mode': 'live'})
        assert result.status_code == 202, result.text
        job = result.json()['job_id']
        assert not control.calls
        duplicate = c.post(f"/api/agents/{definition['agent_id']}/deploy-test", json={
            'version': 1, 'idempotency_key': 'synthetic-live-1', 'execution_mode': 'live'})
        assert duplicate.json()['job_id'] == job and duplicate.json()['reused'] is True
        competing = c.post(f"/api/agents/{definition['agent_id']}/deploy-test", json={
            'version': 1, 'idempotency_key': 'synthetic-live-2', 'execution_mode': 'live'})
        assert competing.status_code == 409
        for stage in ('WAIT_RUNTIME', 'RUNNING', 'EVALUATING', 'EVIDENCE_CHECK', 'LIVE_PASS' if passed else 'BLOCKED'):
            app.state.step_job(job)
            response = c.get('/api/jobs/' + job).json()
            assert response['stage'] == stage, response
        assert control.calls[0][0] == 'CreateAgentRuntime'
        assert control.calls[1][1]['agentRuntimeVersion'] == '3'
        assert runtime.calls[0]['qualifier'] == '3'
        assert response['result']['passed'] is passed
        assert response['result']['billing_estimate_usd'] is None
        app.state.step_job(job)
        assert len(runtime.calls) == 1
        with app.state.store.tx() as db:
            assert runs.get(db, 'foundation-budget:account')['held_usd'] == '0.01'


def reserved(cloud, payload, tmp_path):
    app, client, _ = cloud
    sign_in(cloud)
    payload['dataset'] = payload['dataset'][:1]
    definition = create(client, payload)
    service, control, runtime = install(app.state.store, definition, tmp_path)
    with app.state.store.tx() as db:
        service.enqueue(db, 'synthetic-run', definition,
            {'id': definition['owner'], 'workspace': definition['workspace']}, time.time()+300)
        # Hosted authority is the same session snapshot used by the real API.
        from backend.hosted_auth import SESSION_COOKIE, sha
        db.insert('job_authority', {'id': 'synthetic-run', 'session_hash': sha(client.cookies.get(SESSION_COOKIE))})
        row = runs.get(db, 'foundation-run:synthetic-run')
        runs.bind_runtime(db, row, {'runtime_arn': 'synthetic-runtime', 'runtime_version': '3'})
        runs.claim_dispatch(db, row)
    return app.state.store, definition, row


def test_durable_reservation_exchange_and_unknown_hold(cloud, payload, tmp_path):
    store, definition, row = reserved(cloud, payload, tmp_path)
    principal = f'arn:aws:sts::{ACCOUNT}:assumed-role/synthetic-runtime/session'
    body = {'run_ref': 'synthetic-run', 'manifest_digest': row['manifest_digest']}
    with store.tx() as db:
        result = runs.exchange(db, principal_arn=principal, body={**body, 'operation': 'redeem'})
    assert result['stored_input'] == definition['dataset'][0]['input']
    for operation in ('model', 'tool', 'export'):
        with store.tx() as db:
            runs.exchange(db, principal_arn=principal, body={**body, 'operation': operation, 'call_id': operation+'-1'})
        with pytest.raises(Denied, match='ALREADY_CLAIMED'), store.tx() as db:
            runs.exchange(db, principal_arn=principal, body={**body, 'operation': operation, 'call_id': operation+'-1'})
    with store.tx() as db:
        runs.exchange(db, principal_arn=principal, body={**body, 'operation': 'settle', 'usage': None})
    with store.tx() as db:
        assert runs.get(db, 'foundation-budget:account')['held_usd'] == '0.01'
        assert runs.get(db, 'foundation-run:synthetic-run')['usage'] is None


@pytest.mark.parametrize('tamper', ['role', 'manifest', 'workspace', 'version', 'epoch', 'expired', 'session'])
def test_exchange_stale_revoke_cross_workspace_denied(cloud, payload, tmp_path, tamper):
    store, definition, row = reserved(cloud, payload, tmp_path)
    principal = f'arn:aws:sts::{ACCOUNT}:assumed-role/synthetic-runtime/session'
    body = {'run_ref': 'synthetic-run', 'manifest_digest': row['manifest_digest'], 'operation': 'redeem'}
    if tamper == 'role': principal = principal.replace('synthetic-runtime', 'other-runtime')
    if tamper == 'manifest': body['manifest_digest'] = '0'*64
    with store.tx() as db:
        if tamper == 'workspace': db.update('agents', {'workspace': 'other'}, where=[('id', '=', definition['agent_id'])])
        if tamper == 'version': db.update('agents', {'current_version': 2}, where=[('id', '=', definition['agent_id'])])
        if tamper == 'epoch': runs.put(db, 'foundation-epoch', 1)
        if tamper == 'expired':
            row['deadline'] = 0
            runs.put(db, 'foundation-run:synthetic-run', row)
        if tamper == 'session': db.delete('hosted_sessions')
    with pytest.raises(Denied), store.tx() as db:
        runs.exchange(db, principal_arn=principal, body=body)


def test_opt_in_off_never_falls_back(client, payload):
    login(client)
    definition = create(client, payload)
    result = client.post(f"/api/agents/{definition['agent_id']}/deploy-test", json={
        'version': 1, 'idempotency_key': 'synthetic-live-1', 'execution_mode': 'live'})
    assert result.status_code == 503


def test_atomic_budget_fence_prevents_double_reservation(cloud, payload, tmp_path):
    from backend.dynamo_store import DynamoUnit
    from fastapi import HTTPException
    app, client, _ = cloud
    sign_in(cloud)
    payload['dataset'] = payload['dataset'][:1]
    definition = create(client, payload)
    service, _, _ = install(app.state.store, definition, tmp_path)
    with app.state.store.tx() as db:
        approved = runs.get(db, 'foundation-approved:' + definition['digest'])
        approved['reservation_usd'] = '5'
        runs.put(db, 'foundation-approved:' + definition['digest'], approved)
    persona = {'id': definition['owner'], 'workspace': definition['workspace']}
    a, b = DynamoUnit(app.state.store.table), DynamoUnit(app.state.store.table)
    service.enqueue(a, 'first', definition, persona, time.time()+300)
    service.enqueue(b, 'second', definition, persona, time.time()+300)
    a.commit()
    with pytest.raises(HTTPException): b.commit()
    with app.state.store.tx() as db:
        assert runs.get(db, 'foundation-run:second') is None
        assert runs.get(db, 'foundation-budget:account')['held_usd'] == '5'
        assert runs.get(db, 'foundation-budget:user:'+definition['owner'])['held_usd'] == '5'
        assert runs.get(db, 'foundation-budget:agent:'+definition['agent_id'])['held_usd'] == '5'


def test_exchange_ignores_forged_identity_headers(cloud, payload, tmp_path, monkeypatch):
    from backend import serverless
    store, _, row = reserved(cloud, payload, tmp_path)
    monkeypatch.setattr(serverless, 'store', lambda: store)
    monkeypatch.setenv('FOUNDATION_LIVE_ENABLED', '1')
    event = {'routeKey': 'POST /internal/foundation/exchange', 'requestContext': {},
             'headers': {'userArn': f'arn:aws:sts::{ACCOUNT}:assumed-role/synthetic-runtime/session'},
             'body': json.dumps({'run_ref': 'synthetic-run', 'manifest_digest': row['manifest_digest'], 'operation': 'redeem'})}
    assert serverless.foundation_exchange_handler(event, None)['statusCode'] == 403
    event['requestContext'] = {'authorizer': {'iam': {'userArn': event['headers']['userArn']}}}
    assert serverless.foundation_exchange_handler(event, None)['statusCode'] == 200
    assert serverless.foundation_exchange_handler(event, None)['statusCode'] == 403


def test_telemetry_failure_never_becomes_execution_release():
    from foundation_harness.telemetry import Telemetry
    class FailedExporter:
        exported = 0
        failed = 1
    telemetry = Telemetry.local()
    telemetry.exporter = FailedExporter()
    telemetry.provider.force_flush = lambda **kw: True
    assert not telemetry.flush()
    telemetry.provider.force_flush = lambda **kw: (_ for _ in ()).throw(RuntimeError('sink down'))
    assert not telemetry.flush()


def test_sqs_live_continuation_is_one_stage_not_thread(cloud, payload, tmp_path, monkeypatch):
    from backend import serverless
    app, client, _ = cloud
    sign_in(cloud)
    payload['dataset'] = payload['dataset'][:1]
    definition = create(client, payload)
    service, _, _ = install(app.state.store, definition, tmp_path)
    live_app = create_app(repository=app.state.store, worker_enabled=False, foundation_jobs=service)
    live_app.state.hosted_auth.keys = app.state.hosted_auth.keys
    monkeypatch.setattr(serverless, 'application', lambda: live_app)
    sent = []
    monkeypatch.setattr(serverless.boto3, 'client', lambda name: SimpleNamespace(send_message=lambda **kw: sent.append(kw)))
    monkeypatch.setenv('JOB_QUEUE_URL', 'https://sqs.synthetic.invalid/queue')
    with TestClient(live_app, base_url=str(client.base_url), cookies=client.cookies, headers=client.headers) as c:
        response = c.post(f"/api/agents/{definition['agent_id']}/deploy-test", json={
            'version': 1, 'idempotency_key': 'synthetic-sqs-1', 'execution_mode': 'live'})
        job_id = response.json()['job_id']
        result = serverless.worker_handler({'Records': [{'messageId': 'message', 'body': json.dumps({'job_id': job_id})}]},
            SimpleNamespace(get_remaining_time_in_millis=lambda: 60000))
        assert result == {'batchItemFailures': []}
        assert c.get('/api/jobs/'+job_id).json()['stage'] == 'WAIT_RUNTIME'
        assert len(sent) == 1 and sent[0]['DelaySeconds'] == 10


def test_approved_artifact_readback_rejects_changed_zip():
    from backend.foundation_jobs import ArtifactReadback
    data = b'not-the-approved-package'
    reader = ArtifactReadback(SimpleNamespace(get_object=lambda **kw: {
        'Body': io.BytesIO(data), 'VersionId': 'version'}), 'synthetic-bucket')
    with pytest.raises(Denied, match='CONTENT_DIGEST'):
        reader({'artifact_key': 'package.zip', 'artifact_version': 'version', 'package_digest': '0'*64})


def test_deployable_package_admission_is_deterministic(tmp_path):
    from scripts.package_foundation import package
    raw = config()
    raw['foundation']['digest'] = source_digest()
    saved = save_config(raw, tmp_path)
    admission = {'endpoint': 'https://synthetic.execute-api.us-west-2.amazonaws.com/internal/foundation/exchange',
                 'manifest_digest': saved.stem}
    a = package(saved, tmp_path/'a.zip', admission=admission)
    b = package(saved, tmp_path/'b.zip', admission=admission)
    assert a == b
    assert a['production_ready'] is False


def test_runtime_unknown_outcome_not_retried(cloud, payload, tmp_path):
    app, client, _ = cloud
    sign_in(cloud)
    payload['dataset'] = payload['dataset'][:1]
    definition = create(client, payload)
    service, _, runtime = install(app.state.store, definition, tmp_path)
    attempts = []
    def timeout(**kw):
        attempts.append(kw)
        raise TimeoutError('unknown outcome')
    runtime.invoke_agent_runtime = timeout
    live = create_app(repository=app.state.store, worker_enabled=False, foundation_jobs=service)
    live.state.hosted_auth.keys = app.state.hosted_auth.keys
    with TestClient(live, base_url=str(client.base_url), cookies=client.cookies, headers=client.headers) as c:
        response = c.post(f"/api/agents/{definition['agent_id']}/deploy-test", json={
            'version': 1, 'idempotency_key': 'synthetic-timeout-1', 'execution_mode': 'live'})
        job = response.json()['job_id']
        for _ in range(6): live.state.step_job(job)
        assert len(attempts) == 1
        assert c.get('/api/jobs/'+job).json()['stage'] == 'BLOCKED'
        with app.state.store.tx() as db:
            assert runs.get(db, 'foundation-budget:account')['held_usd'] == '0.01'
            assert runs.get(db, 'foundation-run:'+job)['usage'] is None


@pytest.mark.parametrize('handle', [None, 'other-run'])
def test_runtime_handler_rejects_missing_or_cross_run_reservation(handle):
    from foundation_harness.context import ResolvedEntry
    from runtime.custom_foundation.main import RuntimeHandler
    engine, binding, _, transport, _ = setup()
    reservation = SimpleNamespace(handle=handle, amount_usd=Decimal('0.01'))
    entry = ResolvedEntry(binding, 'stored', engine.config.limits, reservation)
    with pytest.raises(Denied, match='SERVER_RESERVATION_BINDING_REQUIRED'):
        RuntimeHandler(engine, lambda ref, context: entry)({'run_ref': binding.run_ref}, None)
    assert transport.calls == []


@pytest.mark.parametrize('iam', [None, [], {'userArn': 123}, {'userArn': {'role': 'forged'}}])
def test_exchange_malformed_verified_principal_fails_closed(iam, monkeypatch):
    from backend.serverless import foundation_exchange_handler
    monkeypatch.setenv('FOUNDATION_LIVE_ENABLED', '1')
    event = {'routeKey': 'POST /internal/foundation/exchange',
             'requestContext': {'authorizer': {'iam': iam}}, 'body': '{}'}
    assert foundation_exchange_handler(event, None)['statusCode'] == 403
