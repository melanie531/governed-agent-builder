"""Synthetic, offline exchange isolation tests. No deployment or approval data."""
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import boto3
import pytest
from moto import mock_aws

from backend.diagnostic_capture import PREFIX
from backend.diagnostic_exchange import exchange
from backend.dynamo_store import DynamoStore
from backend.foundation_runs import get, put
from foundation_harness.config import digest
from foundation_harness.diagnostic_exchange import DiagnosticExchange, capture
from tests.test_diagnostic_capture import fixture_capture, ROLE, reviewed

ENDPOINT = 'https://synthetic.execute-api.us-west-2.amazonaws.com/internal/diagnostic/capture'


@pytest.fixture(params=['sqlite', 'dynamo'])
def f(request, fixture_capture):
    f = fixture_capture
    if request.param == 'sqlite':
        yield f
        return
    with mock_aws():
        resource = boto3.resource('dynamodb', region_name='us-west-2')
        resource.create_table(TableName='synthetic-capture-isolation',
            KeySchema=[{'AttributeName': 'pk', 'KeyType': 'HASH'}, {'AttributeName': 'sk', 'KeyType': 'RANGE'}],
            AttributeDefinitions=[{'AttributeName': k, 'AttributeType': 'S'} for k in ('pk', 'sk')],
            BillingMode='PAY_PER_REQUEST')
        store = DynamoStore('synthetic-capture-isolation', resource)
        with f.store.tx() as source, store.tx() as target:
            for table in ('settings', 'principals', 'agents', 'versions', 'audit'):
                for row in source.select(table):
                    target.insert(table, dict(row))
        f.store = store
        yield f


def call(f, operation='reserve', binding=None, **extra):
    body = {'capture_ref': f.ref, 'manifest_digest': f.runtime['manifest_digest'], 'operation': operation, **extra}
    if binding is not None:
        body['binding_digest'] = binding
    return exchange(f.store, principal_arn=f.identity['Arn'], body=body, control=f.control, clock=lambda: 100)


def states(f):
    with f.store.tx() as db:
        return {row['key']: json.loads(row['body']) for row in db.select('settings')}


def reserve_claim(f):
    reserved = call(f)
    call(f, 'claim', reserved['binding_digest'])
    return reserved['binding_digest']


def test_only_mutable_exact_records_and_restricted_evidence(f):
    before = states(f)
    binding = reserve_claim(f)
    result = call(f, 'complete', binding, outcome='CAPTURED',
        observation={'response_digest': digest('synthetic response'), 'observed_model': 'unverified-model'})
    after = states(f)
    changed = {key for key in after if after[key] != before.get(key)}
    assert changed == {'opus-capture:' + f.ref, PREFIX + 'binding:' + f.ref, PREFIX + 'evidence:' + f.ref,
        *('foundation-budget:' + scope for scope in ('account', 'workspace:fixture-workspace',
                                                   'user:fixture-user', 'agent:fixture-agent'))}
    assert after['opus-capture:' + f.ref]['state'] == 'CAPTURED'
    assert after['opus-capture:' + f.ref]['attempts'] == 1
    assert result['receipt'] == after[PREFIX + 'evidence:' + f.ref]
    assert not {'ready', 'approved', 'content', 'headers'} & result['receipt'].keys()
    for operation in ('reserve', 'claim', 'complete'):
        with pytest.raises((RuntimeError, ValueError)):
            call(f, operation, None if operation == 'reserve' else binding,
                 **({'outcome': 'UNKNOWN'} if operation == 'complete' else {}))
    assert states(f) == after


@pytest.mark.parametrize('operation', ['put', 'delete', 'update', 'review', 'approve', 'revoke', 'authorize', 'settle', 'redeem'])
def test_no_arbitrary_or_authority_writer_operation(f, operation):
    before = states(f)
    with pytest.raises(RuntimeError, match='SHAPE_DENIED'):
        call(f, operation)
    assert states(f) == before


@pytest.mark.parametrize('field', ['table', 'key', 'action', 'role', 'user_id', 'agent_id', 'run_ref',
    'request', 'workspace', 'runtime', 'headers', 'approved', 'costs', 'cap_usd', 'deadline'])
def test_reject_payload_control_fields(f, field):
    before = states(f)
    with pytest.raises(RuntimeError, match='SHAPE_DENIED'):
        call(f, **{field: 'attacker'})
    assert states(f) == before


@pytest.mark.parametrize('fault', ['role', 'iam_user', 'manifest', 'capture', 'user', 'expiry', 'revocation'])
def test_authenticated_binding_rechecked_at_claim(f, fault):
    reserved = call(f)
    with f.store.tx() as db:
        if fault == 'role': f.identity['Arn'] = f.identity['Arn'].replace('synthetic-capture', 'another-role')
        elif fault == 'iam_user': f.identity['Arn'] = ROLE
        elif fault == 'manifest': f.runtime['manifest_digest'] = digest('wrong manifest')
        elif fault == 'capture': f.ref = digest('another capture')
        elif fault == 'user': db.update('agents', {'owner': 'wrong-user'}, where=[('id', '=', 'fixture-agent')])
        elif fault == 'expiry': db.update('principals', {'expires': 99}, where=[('id', '=', 'fixture-user')])
        elif fault == 'revocation': put(db, PREFIX + 'revoked:' + f.ref, {'reason': 'synthetic'})
    before = states(f)
    with pytest.raises(RuntimeError):
        call(f, 'claim', reserved['binding_digest'])
    assert states(f) == before


def test_other_valid_capture_cannot_use_binding(f):
    first = call(f)
    with f.store.tx() as db:
        second_authority = {**f.authority, 'expires_at': 199}
        f.ref = reviewed(db, 'authority', second_authority)
    second = call(f)
    assert second['binding_digest'] != first['binding_digest']
    with pytest.raises(RuntimeError, match='BINDING_DENIED'):
        call(f, 'claim', first['binding_digest'])
    call(f, 'claim', second['binding_digest'])


@pytest.mark.parametrize('scope', ['account', 'workspace:fixture-workspace', 'user:fixture-user', 'agent:fixture-agent'])
def test_budget_failure_rolls_back_all_holds_and_ticket(f, scope):
    with f.store.tx() as db:
        put(db, 'foundation-budget:' + scope, {'held_usd': '4.99', 'estimated_usd': '0'})
    before = states(f)
    with pytest.raises(ValueError, match='TOTAL_BUDGET_EXCEEDED'):
        call(f)
    assert states(f) == before


def test_evidence_write_failure_rolls_back_completion(f, monkeypatch):
    import backend.diagnostic_exchange as service
    binding = reserve_claim(f)
    before = states(f)
    original = service.put
    def failing(db, key, value):
        if key.startswith(PREFIX + 'evidence:'):
            raise RuntimeError('synthetic evidence failure')
        return original(db, key, value)
    monkeypatch.setattr(service, 'put', failing)
    with pytest.raises(RuntimeError, match='synthetic evidence failure'):
        call(f, 'complete', binding, outcome='CAPTURED',
            observation={'response_digest': digest('response'), 'observed_model': None})
    assert states(f) == before
    with pytest.raises(ValueError, match='CONSUMED'):
        call(f, 'claim', binding)


@pytest.mark.parametrize('observation', [
    {'response_digest': 'not-hash', 'observed_model': None},
    {'response_digest': digest('response'), 'observed_model': 'x' * 201},
    {'response_digest': digest('response'), 'observed_model': None, 'approved': True},
    {'response_digest': digest('response'), 'observed_model': None, 'runtime_ref': digest('attacker')},
    {'response_digest': digest('response'), 'observed_model': {'text': 'raw'}},
])
def test_evidence_shape_rejected_without_transition(f, observation):
    binding = reserve_claim(f)
    before = states(f)
    with pytest.raises(RuntimeError, match='EVIDENCE_SHAPE_DENIED'):
        call(f, 'complete', binding, outcome='CAPTURED', observation=observation)
    assert states(f) == before


def test_complete_before_claim_and_expired_claim_denied(f):
    reserved = call(f)
    with pytest.raises(ValueError, match='NOT_CLAIMED'):
        call(f, 'complete', reserved['binding_digest'], outcome='UNKNOWN')
    with pytest.raises(RuntimeError):
        exchange(f.store, principal_arn=f.identity['Arn'], control=f.control, clock=lambda: 201,
            body={'capture_ref': f.ref, 'manifest_digest': f.runtime['manifest_digest'],
                  'operation': 'claim', 'binding_digest': reserved['binding_digest']})
    assert states(f)['opus-capture:' + f.ref]['state'] == 'RESERVED'


def test_simultaneous_claims_only_one_commit(fixture_capture):
    f = fixture_capture
    binding = call(f)['binding_digest']
    def attempt(_):
        try:
            call(f, 'claim', binding)
            return 'CLAIMED'
        except ValueError as exc:
            assert 'CONSUMED' in str(exc)
            return 'DENIED'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, range(2))) == ['CLAIMED', 'DENIED']


def client(f, monkeypatch, fault=None):
    from botocore.credentials import Credentials
    from foundation_harness.transport import IAMTransport
    calls = []
    class Wire(IAMTransport):
        async def _send(self, url, data, headers, timeout):
            if url == ENDPOINT:
                assert '/execute-api/aws4_request' in headers['Authorization']
                body = json.loads(data)
                calls.append(body['operation'])
                result = exchange(f.store, principal_arn=f.identity['Arn'], body=body,
                    control=f.control, clock=lambda: 100)
                if fault == 'lost_claim' and body['operation'] == 'claim':
                    raise TimeoutError('synthetic lost claim response')
                if fault == 'wrong_response' and body['operation'] == 'claim':
                    result['capture_ref'] = digest('other')
                if fault == 'lost_evidence' and body['operation'] == 'complete':
                    raise TimeoutError('synthetic lost completion')
                return result, {}
            assert '/bedrock-agentcore/aws4_request' in headers['Authorization']
            assert states(f)['opus-capture:' + f.ref]['state'] == 'CLAIMED'
            assert json.loads(data)['thinking'] == {'type': 'disabled'}
            f.sends.append(url)
            if fault == 'model_timeout':
                raise TimeoutError('synthetic Model timeout')
            return {'model': 'unverified-model', 'content': [{'text': 'never return this'}]}, {'raw': 'never persist'}
    session = SimpleNamespace(get_credentials=lambda: Credentials('SYNTHETIC', 'synthetic-fixture-secret'))
    monkeypatch.setattr('foundation_harness.diagnostic_exchange.IAMTransport', Wire)
    return DiagnosticExchange(session, ENDPOINT, f.runtime['manifest_digest']), calls


@pytest.mark.parametrize('fault', [None, 'lost_claim', 'wrong_response', 'lost_evidence', 'model_timeout'])
def test_real_signed_exchange_and_model_same_session_no_retry(f, monkeypatch, fault):
    adapter, calls = client(f, monkeypatch, fault)
    def run():
        return capture(adapter, {'capture_ref': f.ref}, sts=f.sts, clock=lambda: 100)
    if fault:
        with pytest.raises((RuntimeError, TimeoutError)):
            run()
    else:
        receipt = run()
        assert receipt['capture_ref'] == f.ref and receipt['observed_model'] == 'unverified-model'
        assert 'never return' not in json.dumps(receipt)
    sends = len(f.sends)
    assert sends == (0 if fault in {'lost_claim', 'wrong_response'} else 1)
    assert calls == (['reserve', 'claim'] if fault in {'lost_claim', 'wrong_response'} else ['reserve', 'claim', 'complete'])
    with pytest.raises(ValueError, match='TICKET_EXISTS'):
        run()
    assert len(f.sends) == sends
    saved = states(f)
    assert saved['foundation-budget:account']['held_usd'] == '0.1'
    assert saved['opus-capture:' + f.ref]['state'] == (
        'CLAIMED' if fault in {'lost_claim', 'wrong_response'} else 'UNKNOWN' if fault == 'model_timeout' else 'CAPTURED')


def event(f):
    return {'version': '2.0', 'routeKey': 'POST /internal/diagnostic/capture',
        'rawPath': '/internal/diagnostic/capture', 'requestContext': {'apiId': 'synthetic', 'stage': '$default',
        'http': {'method': 'POST'}, 'authorizer': {'iam': {'userArn': f.identity['Arn']}}},
        'body': json.dumps({'capture_ref': f.ref, 'manifest_digest': f.runtime['manifest_digest'], 'operation': 'reserve'})}


@pytest.mark.parametrize('fault', ['disabled', 'route', 'api', 'stage', 'method', 'path', 'version', 'iam', 'header_only', 'base64', 'wrong_role', None])
def test_handler_trusted_aws_context_only(fixture_capture, monkeypatch, fault):
    import backend.serverless as server
    import backend.diagnostic_exchange as service
    f = fixture_capture
    e = event(f)
    monkeypatch.setenv('DIAGNOSTIC_CAPTURE_EXCHANGE_ENABLED', '1')
    monkeypatch.setenv('DIAGNOSTIC_CAPTURE_API_ID', 'synthetic')
    if fault == 'disabled': monkeypatch.delenv('DIAGNOSTIC_CAPTURE_EXCHANGE_ENABLED')
    elif fault == 'route': e['routeKey'] = 'POST /internal/foundation/exchange'
    elif fault == 'api': e['requestContext']['apiId'] = 'other'
    elif fault == 'stage': e['requestContext']['stage'] = 'other'
    elif fault == 'method': e['requestContext']['http']['method'] = 'GET'
    elif fault == 'path': e['rawPath'] = '/api/anything'
    elif fault == 'version': e['version'] = '1.0'
    elif fault in {'iam', 'header_only'}:
        e['requestContext']['authorizer'] = {}
        if fault == 'header_only': e['headers'] = {'userArn': f.identity['Arn']}
    elif fault == 'base64': e['isBase64Encoded'] = True
    elif fault == 'wrong_role': e['requestContext']['authorizer']['iam']['userArn'] = f.identity['Arn'].replace('synthetic-capture', 'other')
    monkeypatch.setenv('STATE_TABLE', 'synthetic-state')
    monkeypatch.setattr(server, 'DynamoStore', lambda *a, **k: f.store)
    def resource(*args, **kwargs):
        assert kwargs['config'].retries == {'total_max_attempts': 1}
        return object()
    monkeypatch.setattr(server.boto3, 'resource', resource)
    monkeypatch.setattr(server.boto3, 'client', lambda *a, **k: f.control)
    original = service.exchange
    monkeypatch.setattr(service, 'exchange', lambda *a, **k: original(*a, **k, clock=lambda: 100))
    response = server.diagnostic_capture_exchange_handler(e, None)
    assert response['statusCode'] == (200 if fault is None else 403)
    assert f.sends == []


def test_default_off_and_no_runtime_database_import_or_product_route_change():
    from pathlib import Path
    source = Path('runtime/diagnostic_capture.py').read_text()
    client_source = Path('foundation_harness/diagnostic_exchange.py').read_text()
    assert 'DynamoStore' not in source + client_source
    assert 'dynamodb' not in source + client_source
    assert 'from backend' not in source + client_source
    assert not Path('runtime/capture-settings.json').exists()
    assert 'internal/diagnostic/capture' not in Path('infra/serverless.py').read_text()


@pytest.mark.parametrize('operation', ['reserve', 'claim', 'complete'])
def test_dynamo_cas_conflict_rolls_back_entire_operation(f, monkeypatch, operation):
    if not isinstance(f.store, DynamoStore):
        pytest.skip('DynamoDB CAS-specific test')
    from backend.dynamo_store import DynamoUnit
    from fastapi import HTTPException
    binding = None
    if operation != 'reserve':
        binding = call(f)['binding_digest']
    if operation == 'complete':
        call(f, 'claim', binding)
    before = states(f)
    original = DynamoUnit.commit
    interrupted = []
    def conflict(unit):
        changed = any(unit.loaded[k] != unit.original[k] for k in unit.loaded)
        if changed and not interrupted:
            interrupted.append(True)
            # Simulate a competing committed revision before our write attempt.
            unit.table.update_item(Key={'pk': '_revision', 'sk': '_revision'},
                UpdateExpression='SET #r = :r', ExpressionAttributeNames={'#r': 'revision'},
                ExpressionAttributeValues={':r': unit.revision + 1})
        return original(unit)
    monkeypatch.setattr(DynamoUnit, 'commit', conflict)
    with pytest.raises(HTTPException) as exc:
        call(f, operation, binding, **({'outcome': 'CAPTURED', 'observation': {
            'response_digest': digest('response'), 'observed_model': None}} if operation == 'complete' else {}))
    assert exc.value.status_code == 409 and interrupted == [True]
    assert states(f) == before


def test_dynamo_stale_claim_cannot_commit_twice(f):
    if not isinstance(f.store, DynamoStore):
        pytest.skip('DynamoDB CAS-specific test')
    from backend.dynamo_store import DynamoUnit
    from backend.diagnostic_exchange import _TransactionStore
    from backend.diagnostic_capture import DiagnosticAdmission
    from scripts.opus_capture_ticket import consume_capture
    from fastapi import HTTPException
    call(f)
    first, second = DynamoUnit(f.store.table), DynamoUnit(f.store.table)
    for unit in (first, second):
        consume_capture(_TransactionStore(unit), f.ref, role=ROLE, workspace='fixture-workspace',
            request_digest=digest(f.authority['request']), now=100,
            admission_check=DiagnosticAdmission(f.ref, ROLE, clock=lambda: 100))
    first.commit()
    with pytest.raises(HTTPException) as exc:
        second.commit()
    assert exc.value.status_code == 409
    row = states(f)['opus-capture:' + f.ref]
    assert row['state'] == 'CLAIMED' and row['attempts'] == 1
