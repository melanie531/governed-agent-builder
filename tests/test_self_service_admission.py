"""Offline synthetic identities only. No cloud, model invocation or Linux proof."""
import copy
import time
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from backend import foundation_runs as runs
from backend import self_service_admission as admission
from backend.app import create_app
from backend.foundation_approval import register, finalize_artifact, FinalizeFoundation
from backend.foundation_jobs import FoundationJobs
from tests.conftest import login, create, ORIGIN
from tests.test_foundation_approval import inputs, ADMIN, OWNER, PLATFORM
from tests.test_foundation_finalization import verified
from tests.test_serverless import cloud, sign_in


def policy_input(definition):
    return admission.FoundationPolicy(foundation_id='research', source_revision=1, expected_revision=0,
        workspaces=['research'], allowed_capabilities=[definition['model_id'], *definition['tools'], *definition['skills']],
        data_sources=['synthetic-local-only'], max_dataset_cases=20, max_prompt_chars=8000,
        reason='Synthetic reviewed foundation risk constraints')


def prepare(store, definition):
    source, _ = inputs(definition)
    with store.tx() as db:
        register(db, ADMIN, source, PLATFORM)
        admission.approve_policy(db, ADMIN, policy_input(definition))
    return {**OWNER, 'id': definition['owner'], 'external_allowed': definition['owner'] == 'alex'}


def test_business_prompt_and_dataset_auto_no_human_review(app, client, payload, monkeypatch):
    login(client); definition = create(client, payload)
    prepare(app.state.store, definition)
    monkeypatch.setattr('backend.app.compile_approval', lambda *a: pytest.fail('human review invoked'))
    for version in range(1, 4):
        result = client.post(f"/api/agents/{definition['agent_id']}/admission", json={'version': version})
        assert result.status_code == 200, result.text
        receipt = result.json()['receipt']
        assert receipt['provenance'] == 'policy-admission'
        assert receipt['creator'] == 'alex' and receipt['eligibility']['version'] == version
        if version < 3:
            payload['prompt'] += ' Additional bounded instructions.'
            payload['dataset'][0]['input'] += ' Revised dataset.'
            payload['base_version'] = version
            revised = client.post(f"/api/agents/{definition['agent_id']}/versions", json=payload)
            assert revised.status_code == 201
            assert revised.json()['digest'] != definition['digest']
    login(client, 'sam')
    assert client.post(f"/api/agents/{definition['agent_id']}/admission", json={'version': 3}).status_code == 404


@pytest.mark.parametrize('field', ['role', 'approved', 'ready', 'admin', 'receipt', 'limits', 'policy'])
def test_payload_forgery_denied(client, payload, field):
    login(client); definition = create(client, payload)
    assert client.post(f"/api/agents/{definition['agent_id']}/admission", json={'version': 1, field: True}).status_code == 422
    assert client.post('/api/agents', json={**payload, field: True}).status_code == 422


@pytest.mark.parametrize('bad', ['grant', 'foundation', 'epoch', 'policy', 'conflict', 'data', 'source', 'catalog', 'version'])
def test_stale_and_revoked_never_renew(app, client, payload, bad):
    login(client); definition = create(client, payload); owner = prepare(app.state.store, definition)
    with app.state.store.tx() as db:
        approved = admission.admit(db, owner, definition['agent_id'], 1)
        authority = runs.get(db, 'domain-authority:' + definition['digest']); authority['expires_at'] = 0
        runs.put(db, 'domain-authority:' + definition['digest'], authority)
        if bad == 'grant': db.delete('grants', where=[('persona', '=', owner['id'])])
        if bad == 'foundation':
            from backend.app import resource
            import json
            f = resource(db, 'foundations', 'research'); f['approved'] = False
            db.update('foundations', {'body': json.dumps(f)}, where=[('id', '=', 'research')])
        if bad == 'epoch': runs.put(db, 'foundation-epoch', 999)
        if bad == 'policy': runs.put(db, 'policy', {'version': 2})
        if bad in ('conflict', 'data'):
            p = runs.get(db, 'foundation-policy:research')
            p['incompatible_pairs' if bad == 'conflict' else 'data_sources'] = [[definition['model_id'], definition['tools'][0]]] if bad == 'conflict' else []
            runs.put(db, 'foundation-policy:research', p)
        if bad in ('source', 'catalog'):
            s = runs.get(db, 'foundation-source:research'); s['revision' if bad == 'source' else 'catalog'] = 99 if bad == 'source' else {}
            runs.put(db, 'foundation-source:research', s)
        if bad == 'version': db.update('agents', {'current_version': 2}, where=[('id', '=', definition['agent_id'])])
        with pytest.raises(HTTPException): admission.admit(db, owner, definition['agent_id'], 1)
        assert runs.get(db, 'domain-authority:' + definition['digest'])['expires_at'] == 0
        assert runs.get(db, 'foundation-approved:' + definition['digest']) == approved


def test_expiry_automatic_revalidation_immutable_receipt(app, client, payload):
    login(client); definition = create(client, payload); owner = prepare(app.state.store, definition)
    with app.state.store.tx() as db:
        record = admission.admit(db, owner, definition['agent_id'], 1)
        auth = runs.get(db, 'domain-authority:' + definition['digest']); auth['expires_at'] = 0
        runs.put(db, 'domain-authority:' + definition['digest'], auth)
        with pytest.raises(HTTPException): admission.check_current(db, owner, definition, record)
        assert admission.admit(db, owner, definition['agent_id'], 1) == record
        admission.check_current(db, owner, definition, record)
        assert runs.get(db, 'domain-authority:' + definition['digest'])['expires_at'] > time.time()


def test_actual_exception_request_only_approves_after_policy_and_grants(app, client, payload):
    login(client); definition = create(client, payload); owner = prepare(app.state.store, definition)
    with app.state.store.tx() as db:
        policy = runs.get(db, 'foundation-policy:research'); policy['allowed_capabilities'] = [definition['model_id']]
        runs.put(db, 'foundation-policy:research', policy)
        with pytest.raises(HTTPException, match='HUMAN_EXCEPTION'): admission.admit(db, owner, definition['agent_id'], 1)
        request = admission.request_exception(db, owner, definition['agent_id'], admission.ExceptionRequest(version=1, reason='Synthetic capability policy exception'))
        decision = admission.ExceptionDecision(approve=True, reason='Reviewed synthetic source and current grants')
        with pytest.raises(HTTPException): admission.decide_exception(db, owner, request['id'], decision, owner)
        with pytest.raises(HTTPException): admission.decide_exception(db, ADMIN, request['id'], decision, owner)
        policy['allowed_capabilities'] += definition['tools'] + definition['skills']
        runs.put(db, 'foundation-policy:research', policy)
        admission.decide_exception(db, ADMIN, request['id'], decision, owner)
        result = admission.admit(db, owner, definition['agent_id'], 1)
        assert result['receipt']['provenance'] == 'human-exception'


def test_forbidden_new_tool_uses_real_capability_request(client, payload):
    login(client)
    payload['tools'].append('restricted-insights'); payload['component_versions']['restricted-insights'] = '1'
    assert client.post('/api/agents', json=payload).status_code == 403
    result = client.post('/api/requests', json={'component_id': 'restricted-insights', 'reason': 'Synthetic business access request'})
    assert result.status_code == 201
    assert client.get('/api/requests').json()[0]['status'] == 'PENDING'


def test_m0_disabled_normal_route_and_legacy_not_migrated(app, client, payload):
    login(client); definition = create(client, payload); owner = prepare(app.state.store, definition)
    _, review = inputs(definition)
    login(client, 'admin')
    assert client.post('/api/internal/m0/foundation-approvals', json=review.model_dump()).status_code == 403
    assert client.post('/api/admin/foundation-approvals', json=review.model_dump()).status_code in (404, 405)
    with app.state.store.tx() as db:
        runs.put(db, 'foundation-approved:' + definition['digest'], {'receipt': {'provenance': 'M0'}})
        with pytest.raises(HTTPException, match='LEGACY_M0'): admission.admit(db, owner, definition['agent_id'], 1)


def test_deploy_business_auto_waits_for_mechanical_artifact(cloud, payload, monkeypatch):
    old, client, _ = cloud; sign_in(cloud); definition = create(client, payload)
    prepare(old.state.store, definition)
    service = FoundationJobs(None, None, SimpleNamespace(), enabled=True)
    app = create_app(repository=old.state.store, worker_enabled=False, foundation_jobs=service)
    app.state.hosted_auth.keys = old.state.hosted_auth.keys
    monkeypatch.setattr('backend.app.compile_approval', lambda *a: pytest.fail('manual approval'))
    with TestClient(app, base_url=str(client.base_url), cookies=client.cookies, headers=client.headers) as c:
        response = c.post(f"/api/agents/{definition['agent_id']}/deploy-test", json={'version': 1, 'execution_mode': 'live', 'idempotency_key': 'synthetic-auto-deploy'})
        assert response.status_code == 202, response.text
        assert response.json()['stage'] == 'WAIT_ARTIFACT'
        job = response.json()['job_id']; app.state.step_job(job)
        assert c.get('/api/jobs/' + job).json()['stage'] == 'WAIT_ARTIFACT'
        with app.state.store.tx() as db:
            assert runs.get(db, 'foundation-run:' + job) is None
            assert runs.get(db, 'foundation-approved:' + definition['digest'])['receipt']['provenance'] == 'policy-admission'


def test_policy_receipt_still_needs_artifact_and_linux(app, client, payload):
    login(client); definition = create(client, payload); owner = prepare(app.state.store, definition)
    service = FoundationJobs(None, None, SimpleNamespace(), enabled=True)
    with app.state.store.tx() as db:
        admission.admit(db, owner, definition['agent_id'], 1)
        with pytest.raises(HTTPException, match='ARTIFACT_FINALIZATION'): service.approve_request(db, definition, owner)
        data = FinalizeFoundation(agent_id=definition['agent_id'], version=1, definition_digest=definition['digest'], approval_revision=1,
                                  package_digest='a'*64, artifact_version='synthetic-version', request_id='synthetic-finalization')
        finalize_artifact(db, ADMIN, data, owner, PLATFORM, verifier=verified)
        with pytest.raises(HTTPException, match='LINUX_EXECUTION'): service.approve_request(db, definition, owner)


def test_admission_dynamo_cas_revocation_conflict(cloud, payload):
    from backend.dynamo_store import DynamoUnit
    app, client, _ = cloud; sign_in(cloud); definition = create(client, payload); owner = prepare(app.state.store, definition)
    pending = DynamoUnit(app.state.store.table)
    admission.admit(pending, owner, definition['agent_id'], 1)
    with app.state.store.tx() as db: runs.put(db, 'foundation-epoch', 999)
    with pytest.raises(HTTPException): pending.commit()
    with app.state.store.tx() as db: assert runs.get(db, 'foundation-approved:' + definition['digest']) is None


def test_injected_risk_evaluator_cannot_bypass_or_autoapprove(app, client, payload):
    login(client); definition = create(client, payload); owner = prepare(app.state.store, definition)
    with app.state.store.tx() as db:
        with pytest.raises(HTTPException, match='RISK_REVIEW'): admission.admit(db, owner, definition['agent_id'], 1, evaluator=lambda *a: False)
        assert runs.get(db, 'foundation-approved:' + definition['digest']) is None
        db.delete('grants', where=[('persona', '=', owner['id'])])
        with pytest.raises(HTTPException): admission.admit(db, owner, definition['agent_id'], 1, evaluator=lambda *a: True)
