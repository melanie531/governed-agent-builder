"""Offline IAM routing/package regressions; not evidence of cloud admission."""
import json
from copy import deepcopy

import pytest

from backend import foundation_runs as runs, serverless
from foundation_harness.context import Denied
from infra.serverless import template
from tests.test_foundation_wiring import reserved, ACCOUNT
from tests.test_serverless import cloud


def event(row):
    return {'version': '2.0', 'routeKey': 'POST /internal/foundation/exchange',
            'rawPath': '/internal/foundation/exchange',
            'requestContext': {'apiId': 'synthetic', 'stage': '$default',
                'http': {'method': 'POST'},
                'authorizer': {'iam': {'userArn': f'arn:aws:sts::{ACCOUNT}:assumed-role/synthetic-runtime/session'}}},
            'body': json.dumps({'run_ref': row['run_ref'], 'manifest_digest': row['manifest_digest'], 'operation': 'redeem'})}


@pytest.mark.parametrize('change', ['api', 'stage', 'path', 'method', 'session', 'headers', 'route'])
def test_exchange_trusted_namespace_only(cloud, payload, tmp_path, monkeypatch, change):
    store, _, row = reserved(cloud, payload, tmp_path)
    monkeypatch.setattr(serverless, 'store', lambda: store)
    monkeypatch.setenv('FOUNDATION_ADMISSION_ENABLED', '1')
    monkeypatch.setenv('FOUNDATION_API_ID', 'synthetic')
    value = event(row)
    if change == 'api': value['requestContext']['apiId'] = 'another'
    if change == 'stage': value['requestContext']['stage'] = 'another'
    if change == 'path': value['rawPath'] = '/api/internal/foundation/exchange'
    if change == 'method': value['requestContext']['http']['method'] = 'GET'
    if change == 'session': value['requestContext']['authorizer'] = {'lambda': {'subject': 'owner'}}
    if change == 'headers':
        value['headers'] = value['requestContext'].pop('authorizer')['iam']
    if change == 'route': value['routeKey'] = 'ANY /api/{proxy+}'
    assert serverless.foundation_exchange_handler(value, None)['statusCode'] == 403
    with store.tx() as db:
        assert runs.get(db, 'foundation-run:'+row['run_ref'])['state'] == 'DISPATCHED'


def test_admission_separate_from_live_and_public_handlers(cloud, payload, tmp_path, monkeypatch):
    store, _, row = reserved(cloud, payload, tmp_path)
    monkeypatch.setattr(serverless, 'store', lambda: store)
    monkeypatch.setenv('FOUNDATION_ADMISSION_ENABLED', '1')
    monkeypatch.setenv('FOUNDATION_API_ID', 'synthetic')
    monkeypatch.delenv('FOUNDATION_LIVE_ENABLED', raising=False)
    value = event(row)
    assert serverless.api_handler(value, None)['statusCode'] == 404
    assert serverless.auth_handler(value, None)['statusCode'] == 404
    assert serverless.foundation_exchange_handler(value, None)['statusCode'] == 200


def test_template_isolates_iam_exchange_and_existing_resources():
    r = template()['Resources']
    route = r['FoundationExchangeRoute']['Properties']
    assert route['AuthorizationType'] == 'AWS_IAM'
    assert route['RouteKey'] == 'POST /internal/foundation/exchange'
    assert 'AuthorizerId' not in route
    perm = r['FoundationExchangePermission']['Properties']
    assert perm['SourceArn']['Fn::Sub'].endswith('${Api}/$default/POST/internal/foundation/exchange')
    assert perm['SourceAccount'] == {'Ref': 'AWS::AccountId'}
    env = r['FoundationExchange']['Properties']['Environment']['Variables']
    assert env['FOUNDATION_ADMISSION_ENABLED'] == '1'
    assert 'FOUNDATION_LIVE_ENABLED' not in env
    assert r['Route0']['Properties']['AuthorizationType'] == 'CUSTOM'
    assert r['Route2']['Properties']['AuthorizationType'] == 'NONE'
    assert r['Worker']['Properties']['Environment']['Variables'].get('FOUNDATION_LIVE_ENABLED', '0') == '0'
    policies = r['FoundationExchangeRole']['Properties']['Policies'][0]['PolicyDocument']['Statement']
    assert not any('bedrock' in json.dumps(s) or 'lambda:InvokeFunction' in json.dumps(s) for s in policies)


def test_authorize_checks_exact_manifest_capability(cloud, payload, tmp_path):
    store, _, row = reserved(cloud, payload, tmp_path)
    principal = event(row)['requestContext']['authorizer']['iam']['userArn']
    body = json.loads(event(row)['body'])
    with store.tx() as db:
        runs.exchange(db, principal_arn=principal, body=body)
    with pytest.raises(Denied, match='CAPABILITY'), store.tx() as db:
        runs.exchange(db, principal_arn=principal, body={**body, 'operation': 'authorize',
                      'capability': 'tool', 'resource': 'unapproved'})
    with store.tx() as db:
        runs.exchange(db, principal_arn=principal, body={**body, 'operation': 'authorize',
                      'capability': 'model', 'resource': row['approved']['config']['model']['route']})


def test_same_agent_version_role_reuse_keeps_per_job_reservations(cloud, payload, tmp_path):
    from tests.test_foundation_wiring import install
    from tests.test_serverless import sign_in
    from tests.conftest import create
    import time
    app, client, _ = cloud
    sign_in(cloud)
    payload['dataset'] = payload['dataset'][:1]
    definition = create(client, payload)
    service, _, _ = install(app.state.store, definition, tmp_path)
    persona = {'id': definition['owner'], 'workspace': definition['workspace'], 'role': 'business', 'external_allowed': False}
    with app.state.store.tx() as db:
        service.enqueue(db, 'one', definition, persona, time.time()+300)
        service.enqueue(db, 'two', definition, persona, time.time()+300)
        assert runs.get(db, 'foundation-budget:account')['held_usd'] == '0.02'
        assert runs.get(db, 'foundation-run:one')['run_ref'] == 'one'
        assert runs.get(db, 'foundation-run:two')['run_ref'] == 'two'


def test_linux_dependency_assembly_is_pinned_and_package_rejects_secrets(tmp_path):
    from scripts.package_foundation import dependency_command, dependency_files
    command = dependency_command(tmp_path)
    assert '--require-hashes' in command and '--only-binary' in command
    assert 'aarch64-manylinux2014' in command and '3.13' in command
    (tmp_path/'.env').write_text('synthetic secret')
    with pytest.raises(ValueError, match='UNEXPECTED_DEPENDENCY'):
        dependency_files(tmp_path)


def test_persisted_agent_version_cannot_rebind_runtime(cloud, payload, tmp_path):
    store, _, row = reserved(cloud, payload, tmp_path)
    with pytest.raises(Denied, match='REBIND'), store.tx() as db:
        another = deepcopy(row)
        another['runtime'] = None
        runs.bind_runtime(db, another, {'runtime_arn': 'other', 'runtime_version': '4'})


def test_worker_permissions_require_exact_reviewed_role_artifact_network():
    from infra.serverless import foundation_worker_statements
    role = f'arn:aws:iam::{ACCOUNT}:role/synthetic-runtime'
    statements = foundation_worker_statements(role=role,
        artifact=f'arn:aws:s3:::synthetic-bucket/approved/one.zip',
        runtime_name='gab_foundation_'+'a'*24, subnets=['subnet-synthetic'], groups=['sg-synthetic'])
    passing = next(s for s in statements if s['Action'] == ['iam:PassRole'])
    assert passing['Resource'] == role
    assert passing['Condition']['StringEquals']['iam:PassedToService'] == 'bedrock-agentcore.amazonaws.com'
    creation = next(s for s in statements if s['Action'] == ['bedrock-agentcore:CreateAgentRuntime'])
    assert creation['Condition']['ForAllValues:StringEquals']['bedrock-agentcore:subnets'] == ['subnet-synthetic']
    assert creation['Condition']['Null']['bedrock-agentcore:subnets'] == 'false'
    assert not any('UpdateAgentRuntime' in json.dumps(s) or 'DeleteAgentRuntime' in json.dumps(s) for s in statements)
    with pytest.raises(ValueError):
        foundation_worker_statements(role=role.replace('synthetic-runtime', '*'),
            artifact='arn:aws:s3:::synthetic-bucket/*', runtime_name='*', subnets=[], groups=[])


def test_exchange_response_cannot_change_runtime_version():
    from foundation_harness.backend_exchange import BackendExchange
    from tests.test_foundation_executor import setup
    from dataclasses import asdict
    from types import SimpleNamespace
    _, binding, _, _, _ = setup()
    client = BackendExchange(None, 'https://synthetic.execute-api.us-west-2.amazonaws.com/internal/foundation/exchange', binding.manifest_digest)
    client.entry = binding
    forged = {**asdict(binding), 'runtime_version': 'other'}
    client.transport = SimpleNamespace(send=lambda *args: ({'reservation_handle': binding.run_ref, 'binding': forged}, {}))
    with pytest.raises(Denied, match='BINDING'):
        client.request(binding.run_ref, 'authorize', capability='finish', resource='')
