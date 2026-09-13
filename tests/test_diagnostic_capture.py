"""Explicit synthetic protected-record fixtures. NO real approval or cloud calls."""
import copy
import json
from types import SimpleNamespace

import pytest
from botocore.credentials import Credentials

from backend.diagnostic_capture import COST_SERVICES, PREFIX, PURPOSE, capture
from backend.foundation_runs import get, put
from backend.store import Store
from foundation_harness.config import digest
from foundation_harness.transport import IAMTransport

ACCOUNT = '9988' '77665544'
ROLE = f'arn:aws:iam::{ACCOUNT}:role/synthetic-capture'
ARN = f'arn:aws:bedrock-agentcore:us-west-2:{ACCOUNT}:runtime/synthetic-abc'
URL = 'https://gab-foundation-model-m0-example.gateway.bedrock-agentcore.us-west-2.amazonaws.com/bedrockrt/v1/messages'


def save(db, kind, value):
    ref = digest(value)
    put(db, PREFIX + kind + ':' + ref, value)
    return ref


def reviewed(db, kind, value):
    ref = save(db, kind, value)
    receipt = {'subject_digest': ref, 'purpose': PURPOSE, 'reviewer': 'fixture-admin',
               'reviewed_at': 90, 'expires_at': 200}
    put(db, PREFIX + 'review:' + ref, receipt)
    db.insert('audit', {'actor': 'fixture-admin', 'action': 'diagnostic_capture_reviewed',
        'resource': ref, 'detail': digest(receipt), 'created': 90})
    return ref


@pytest.fixture
def fixture_capture(tmp_path):
    store = Store(str(tmp_path / 'fixture.sqlite'), seed_personas=False)
    definition = {'agent_id': 'fixture-agent', 'owner': 'fixture-user', 'workspace': 'fixture-workspace', 'version': 1}
    definition['digest'] = digest(definition)
    request = {'endpoint': URL, 'system': 'Synthetic diagnostic', 'prompt': 'Hello', 'max_tokens': 256}
    runtime = {'role': ROLE, 'runtime_id': 'synthetic-abc', 'runtime_arn': ARN, 'runtime_version': '1',
        'endpoint_name': 'DEFAULT', 'manifest_digest': digest('synthetic manifest'),
        'definition_digest': definition['digest'], 'model_endpoint': URL,
        'readback': {'roleArn': ROLE, 'agentRuntimeArtifact': {'codeConfiguration': {'code': {'s3': {
            'bucket': 'synthetic-private', 'prefix': 'diagnostic.zip', 'versionId': 'fixture-version'}}}},
            'networkConfiguration': {'networkMode': 'VPC'}, 'environmentVariables': {
                'DEFINITION_DIGEST': definition['digest'], 'MANIFEST_DIGEST': digest('synthetic manifest')}},
        'isolation_evidence': {'source': 'https://example.invalid/fixture-isolation', 'sha256': digest('fixture isolation'),
            'role': ROLE, 'runtime_arn': ARN, 'runtime_version': '1', 'invoke_policy_sha256': digest('fixture policy')}}
    isolation_source = {'source': 'https://example.invalid/fixture-isolation', 'role': ROLE,
        'runtime_arn': ARN, 'runtime_version': '1',
        'trust_policy': {'Statement': [{'Effect': 'Allow', 'Principal': {'Service': 'bedrock-agentcore.amazonaws.com'}}]},
        'invoke_policy': {'Statement': [{'Effect': 'Allow', 'Resource': ARN, 'synthetic_fixture': True}]},
        'attached_runtime_versions': [{'runtime_arn': ARN, 'runtime_version': '1'}]}
    with store.tx() as db:
        runtime['isolation_evidence']['sha256'] = save(db, 'isolation-source', isolation_source)
        runtime['isolation_evidence']['invoke_policy_sha256'] = digest(isolation_source['invoke_policy'])
        db.execute('CREATE TABLE principals(id TEXT PRIMARY KEY, body TEXT NOT NULL, expires REAL NOT NULL)')
        for subject, role, workspace in [('fixture-user', 'business', 'fixture-workspace'), ('fixture-admin', 'admin', 'platform')]:
            db.insert('principals', {'id': subject, 'body': json.dumps({'id': subject, 'role': role, 'workspace': workspace}), 'expires': 300})
        db.insert('agents', {'id': 'fixture-agent', 'owner': 'fixture-user', 'workspace': 'fixture-workspace', 'current_version': 1, 'created': 80})
        db.insert('versions', {'agent': 'fixture-agent', 'version': 1, 'digest': definition['digest'], 'body': json.dumps(definition), 'created': 80})
        runtime_ref = reviewed(db, 'runtime', runtime)
        costs, evidence = {}, {}
        for service in sorted(COST_SERVICES):
            source = {'service': service, 'source': 'https://example.invalid/synthetic-price', 'rate_usd': '0.001',
                'quantity_bound': '10', 'usage_bound': '10 synthetic units, fixture only', 'retention_seconds': 60,
                'source_excerpt': 'SYNTHETIC FIXTURE: not production pricing or operator approval'}
            source_ref = save(db, 'price-source', source)
            proof = {k: source[k] for k in ('source', 'usage_bound', 'retention_seconds')}
            proof['sha256'] = source_ref
            evidence[service] = proof
            costs[service] = {'usd': '0.01', 'basis': digest(proof)}
        pricing_ref = reviewed(db, 'pricing', {'request_digest': digest(request), 'runtime_ref': runtime_ref,
            'reservation_usd': '0.1', 'costs': costs, 'evidence': evidence})
        authority = {'purpose': PURPOSE, 'agent_id': 'fixture-agent', 'version': 1, 'definition_digest': definition['digest'],
            'request': request, 'request_digest': digest(request), 'runtime_ref': runtime_ref, 'pricing_ref': pricing_ref,
            'expires_at': 200, 'epoch': 0, 'policy_digest': digest(get(db, 'policy'))}
        ref = reviewed(db, 'authority', authority)
    sends = []

    class Wire(IAMTransport):
        async def _send(self, url, data, headers, timeout):
            with store.tx() as db:
                assert get(db, 'opus-capture:' + ref)['state'] == 'CLAIMED'
                for scope in ('account', 'workspace:fixture-workspace', 'user:fixture-user', 'agent:fixture-agent'):
                    assert get(db, 'foundation-budget:' + scope)['held_usd'] == '0.1'
            assert '/bedrock-agentcore/aws4_request' in headers['Authorization']
            assert json.loads(data)['thinking'] == {'type': 'disabled'}
            sends.append(url)
            return {'model': 'unknown-diagnostic-model', 'content': [{'text': 'not for caller'}]}, {'raw_header': 'not for caller'}

    runtime_response = {'status': 'READY', 'agentRuntimeArn': ARN, 'agentRuntimeVersion': '1', **copy.deepcopy(runtime['readback'])}
    endpoint_response = {'status': 'READY', 'name': 'DEFAULT', 'agentRuntimeArn': ARN, 'liveVersion': '1', 'targetVersion': '1'}
    identity = {'Arn': f'arn:aws:sts::{ACCOUNT}:assumed-role/synthetic-capture/fixture-session', 'Account': ACCOUNT}
    return SimpleNamespace(store=store, ref=ref, runtime=runtime, authority=authority,
        identity=identity, runtime_response=runtime_response, endpoint_response=endpoint_response,
        sts=SimpleNamespace(get_caller_identity=lambda: identity),
        control=SimpleNamespace(get_agent_runtime=lambda **kw: runtime_response,
            get_agent_runtime_endpoint=lambda **kw: endpoint_response),
        wire=Wire(SimpleNamespace(get_credentials=lambda: Credentials('SYNTHETIC', 'synthetic-fixture-secret'))), sends=sends)


def run(f, payload=None, now=100):
    return capture(f.store, {'capture_ref': f.ref} if payload is None else payload,
        sts=f.sts, control=f.control, transport=f.wire, clock=lambda: now)


def test_real_adapter_ticket_signing_networkcounter1_and_replay(fixture_capture):
    f = fixture_capture
    receipt = run(f)
    assert receipt['observed_model'] == 'unknown-diagnostic-model'
    assert receipt['outcome'] == 'CAPTURED' and len(f.sends) == 1
    assert not {'content', 'headers', 'ready', 'approved', 'production_ready'} & receipt.keys()
    with f.store.tx() as db:
        assert get(db, PREFIX + 'evidence:' + f.ref) == receipt
        assert get(db, 'foundation-approved:' + f.authority['definition_digest']) is None
    f.store = Store(f.store.path, seed_personas=False)
    with pytest.raises(ValueError, match='TICKET_EXISTS'):
        run(f)
    assert len(f.sends) == 1


@pytest.mark.parametrize('fault', ['role', 'account', 'runtime', 'runtime_version', 'endpoint_version', 'artifact', 'network',
    'agent_version', 'owner_role', 'owner_expired', 'workspace', 'approval', 'audit', 'reviewer', 'revoked', 'pricing',
    'price_source', 'runtime_record', 'epoch', 'policy', 'cap', 'authority_digest', 'expired'])
def test_denied_paths_have_zero_sends_and_no_reservation(fixture_capture, fault):
    f = fixture_capture
    with f.store.tx() as db:
        if fault == 'role': f.identity['Arn'] = f.identity['Arn'].replace('synthetic-capture', 'other')
        elif fault == 'account': f.identity['Account'] = '0000' '00000000'
        elif fault == 'runtime': f.runtime_response['agentRuntimeArn'] += 'other'
        elif fault == 'runtime_version': f.runtime_response['agentRuntimeVersion'] = '2'
        elif fault == 'endpoint_version': f.endpoint_response['liveVersion'] = '2'
        elif fault == 'artifact': f.runtime_response['agentRuntimeArtifact'] = {}
        elif fault == 'network': f.runtime_response['networkConfiguration'] = {'networkMode': 'PUBLIC'}
        elif fault == 'agent_version': db.update('agents', {'current_version': 2}, where=[('id', '=', 'fixture-agent')])
        elif fault in ('owner_role', 'workspace'):
            owner = {'id': 'fixture-user', 'role': 'admin' if fault == 'owner_role' else 'business',
                     'workspace': 'other' if fault == 'workspace' else 'fixture-workspace'}
            db.update('principals', {'body': json.dumps(owner)}, where=[('id', '=', 'fixture-user')])
        elif fault == 'owner_expired': db.update('principals', {'expires': 99}, where=[('id', '=', 'fixture-user')])
        elif fault == 'approval': db.delete('settings', where=[('key', '=', PREFIX + 'review:' + f.ref)])
        elif fault == 'audit': db.delete('audit')
        elif fault == 'reviewer': db.delete('principals', where=[('id', '=', 'fixture-admin')])
        elif fault == 'revoked': put(db, PREFIX + 'revoked:' + f.ref, {'reason': 'fixture revocation'})
        elif fault == 'pricing': db.delete('settings', where=[('key', '=', PREFIX + 'pricing:' + f.authority['pricing_ref'])])
        elif fault == 'runtime_record': db.delete('settings', where=[('key', '=', PREFIX + 'runtime:' + f.authority['runtime_ref'])])
        elif fault == 'price_source':
            for row in db.select('settings'):
                if row['key'].startswith(PREFIX + 'price-source:'): db.delete('settings', where=[('key', '=', row['key'])])
        elif fault == 'epoch': put(db, 'foundation-epoch', 1)
        elif fault == 'policy': put(db, 'policy', {'version': 2})
        elif fault == 'cap': put(db, 'foundation-budget:account', {'held_usd': '4.99', 'estimated_usd': '0'})
        elif fault == 'authority_digest': put(db, PREFIX + 'authority:' + f.ref, {**f.authority, 'expires_at': 300})
    with pytest.raises((RuntimeError, ValueError)):
        run(f, now=201 if fault == 'expired' else 100)
    assert f.sends == []
    with f.store.tx() as db:
        assert get(db, 'opus-capture:' + f.ref) is None
        assert get(db, 'foundation-budget:user:fixture-user') is None


@pytest.mark.parametrize('extra', ['role', 'user_id', 'agent_id', 'workspace', 'request', 'runtime_version', 'approved', 'headers'])
def test_payload_identity_and_status_never_authority(fixture_capture, extra):
    f = fixture_capture
    with pytest.raises(RuntimeError, match='ONLY_DIAGNOSTIC_REFERENCE'):
        run(f, {'capture_ref': f.ref, extra: 'caller-supplied'})
    assert f.sends == []


def test_product_run_ref_cannot_redeem_capture(fixture_capture):
    f = fixture_capture
    with f.store.tx() as db:
        put(db, 'foundation-run:product', {'state': 'DISPATCHED', 'approved': True})
    for payload in ({'run_ref': 'product'}, {'capture_ref': 'product'}, {'capture_ref': digest('product')}):
        with pytest.raises(RuntimeError): run(f, payload)
    assert f.sends == []


def test_timeout_no_retries_retains_funds(fixture_capture):
    f = fixture_capture
    class TimeoutWire:
        def post(self, *args):
            f.sends.append('unknown')
            raise TimeoutError('fixture timeout')
    f.wire = TimeoutWire()
    with pytest.raises(TimeoutError): run(f)
    with pytest.raises(ValueError, match='TICKET_EXISTS'): run(f)
    assert f.sends == ['unknown']
    with f.store.tx() as db:
        assert get(db, 'opus-capture:' + f.ref)['state'] == 'UNKNOWN'
        assert get(db, 'foundation-budget:account')['held_usd'] == '0.1'


def test_entry_disabled_without_baked_settings_no_clients(monkeypatch):
    import boto3
    from runtime.diagnostic_capture import invoke
    monkeypatch.setattr(boto3, 'Session', lambda **kw: pytest.fail('no clients when disabled'))
    assert invoke({'capture_ref': 'a' * 64}, {'headers': {'role': ROLE}})['code'] == 'DIAGNOSTIC_NOT_CONFIGURED'


def test_revoked_between_reserve_and_claim_has_zero_sends(fixture_capture, monkeypatch):
    import backend.diagnostic_capture as adapter
    f = fixture_capture
    original = adapter.reserve_capture
    def revoke_after_reserve(*args, **kwargs):
        result = original(*args, **kwargs)
        with f.store.tx() as db:
            put(db, PREFIX + 'revoked:' + f.ref, {'reason': 'synthetic post-reserve revocation'})
        return result
    monkeypatch.setattr(adapter, 'reserve_capture', revoke_after_reserve)
    with pytest.raises(RuntimeError, match='REVOKED'): run(f)
    assert f.sends == []
    with f.store.tx() as db:
        assert get(db, 'opus-capture:' + f.ref)['state'] == 'RESERVED'
        assert get(db, 'foundation-budget:account')['held_usd'] == '0.1'


def test_concurrent_adapter_calls_only_one_send(fixture_capture):
    from concurrent.futures import ThreadPoolExecutor
    f = fixture_capture
    def attempt(_):
        try:
            return run(f)['outcome']
        except ValueError as error:
            assert 'TICKET_EXISTS' in str(error)
            return 'DENIED'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, range(2))) == ['CAPTURED', 'DENIED']
    assert len(f.sends) == 1


@pytest.mark.parametrize('fault', ['source_tamper', 'price_math', 'no_approved_amount', 'missing_service', 'boolean_only'])
def test_review_does_not_substitute_for_price_evidence(fixture_capture, fault):
    f = fixture_capture
    with f.store.tx() as db:
        price = get(db, PREFIX + 'pricing:' + f.authority['pricing_ref'])
        if fault == 'source_tamper':
            ref = price['evidence']['model_input']['sha256']
            source = get(db, PREFIX + 'price-source:' + ref)
            put(db, PREFIX + 'price-source:' + ref, {**source, 'source_excerpt': 'tampered'})
        else:
            if fault == 'price_math': price['costs']['model_input']['usd'] = '0.0001'
            elif fault == 'no_approved_amount': price['reservation_usd'] = None
            elif fault == 'missing_service': del price['costs']['network']; del price['evidence']['network']
            else: price = {'approved': True, 'reservation_usd': '5'}
            f.authority['pricing_ref'] = reviewed(db, 'pricing', price)
            f.ref = reviewed(db, 'authority', f.authority)
    with pytest.raises((ValueError, RuntimeError)): run(f)
    assert f.sends == []


def test_entry_composes_actual_adapter_ignores_context_headers(fixture_capture, monkeypatch):
    import boto3
    import foundation_harness.diagnostic_exchange as adapter
    from runtime import diagnostic_capture as entry
    from tests.test_diagnostic_exchange import client, ENDPOINT
    f = fixture_capture
    exchange, calls = client(f, monkeypatch)
    class Settings:
        def is_file(self): return True
        def read_bytes(self):
            return json.dumps({'region': 'us-west-2', 'exchange_endpoint': ENDPOINT,
                               'manifest_digest': f.runtime['manifest_digest']}).encode()
    class File:
        def with_name(self, name):
            assert name == 'capture-settings.json'
            return Settings()
    monkeypatch.setattr(entry, 'Path', lambda _: File())
    def aws_client(name, config):
        assert name == 'sts'  # No DynamoDB or Runtime control clients in Runtime.
        assert config.retries == {'total_max_attempts': 1}
        return f.sts
    session = exchange.transport.session
    session.client = aws_client
    monkeypatch.setattr(boto3, 'Session', lambda **k: session)
    original_capture = adapter.capture
    monkeypatch.setattr(adapter, 'capture', lambda *a, **k: original_capture(*a, **k, clock=lambda: 100))
    receipt = entry.invoke({'capture_ref': f.ref}, {'headers': {'role': 'attacker', 'runtime_version': '999'}})
    assert receipt['outcome'] == 'CAPTURED' and len(f.sends) == 1
    assert calls == ['reserve', 'claim', 'complete']
