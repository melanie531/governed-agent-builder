"""Pure local receipt/ownership tests: no SDK clients or cloud calls."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import foundation_import_smoke as smoke


@pytest.fixture(autouse=True)
def no_aws(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Cloud session forbidden in offline tests')
    monkeypatch.setattr(smoke.boto3, 'Session', forbidden)


def args(tmp_path, name='run.json', **kwargs):
    return SimpleNamespace(receipt=tmp_path / name, sha256='a' * 64,
                           source_sha='b' * 40, expected_account='9988' + '77665544',
                           region='synthetic-region', new_run=True, resume=False,
                           **kwargs)


def owned(a):
    proof = smoke.prepare_execution(a)
    proof.update(runtime_id='synthetic-runtime', runtime_arn='synthetic-arn',
                 runtime_version='1', cleanup='REQUIRED')
    a.receipt.write_text(json.dumps(proof))
    a.new_run, a.resume = False, True
    return proof


def test_identical_artifact_new_runs_have_distinct_execution_tokens(tmp_path):
    a = smoke.prepare_execution(args(tmp_path, 'first.json'))
    b = smoke.prepare_execution(args(tmp_path, 'second.json'))
    assert a['execution_binding'] == b['execution_binding']
    assert a['probe_execution_id'] != b['probe_execution_id']
    assert a['client_token'] != b['client_token']
    assert smoke.execution_identity(a['probe_execution_id'], a['execution_binding']) == a['client_token']


def test_retry_and_resume_keep_same_identity(tmp_path):
    a = args(tmp_path)
    proof = owned(a)
    for _ in range(2):
        resumed = smoke.prepare_execution(a)
        assert resumed == proof
        assert smoke.execution_identity(resumed['probe_execution_id'], resumed['execution_binding']) == proof['client_token']


@pytest.mark.parametrize('field,value', [('sha256', 'c' * 64), ('source_sha', 'c' * 40),
                                         ('expected_account', '1122' + '33445566'),
                                         ('region', 'different-region')])
def test_wrong_binding_rejected(tmp_path, field, value):
    a = args(tmp_path)
    owned(a)
    setattr(a, field, value)
    with pytest.raises(ValueError, match='BINDING_MISMATCH'):
        smoke.prepare_execution(a)


def test_deleted_run_requires_explicit_new_run_new_receipt(tmp_path):
    a = args(tmp_path)
    proof = owned(a)
    proof['cleanup'] = 'DELETED_VERIFIED'
    a.receipt.write_text(json.dumps(proof))
    with pytest.raises(ValueError, match='COMPLETED_EXECUTION'):
        smoke.prepare_execution(a)
    a.new_run, a.resume = True, False
    with pytest.raises(FileExistsError):
        smoke.prepare_execution(a)
    fresh = smoke.prepare_execution(args(tmp_path, 'fresh.json'))
    assert fresh['client_token'] != proof['client_token']


@pytest.mark.parametrize('state', ['CREATE_OUTCOME_UNKNOWN', 'NO_RUNTIME_CREATED', 'DELETE_REQUESTED'])
def test_unresolved_state_never_recreates(tmp_path, state):
    a = args(tmp_path)
    proof = owned(a)
    proof['cleanup'] = state
    a.receipt.write_text(json.dumps(proof))
    with pytest.raises(ValueError, match='MANUAL_RECONCILIATION'):
        smoke.prepare_execution(a)


def test_legacy_and_tampered_token_rejected(tmp_path):
    a = args(tmp_path)
    proof = owned(a)
    proof['client_token'] = 'wrong'
    a.receipt.write_text(json.dumps(proof))
    with pytest.raises(ValueError, match='TOKEN_MISMATCH'):
        smoke.prepare_execution(a)
    a.receipt.write_text('{}')
    with pytest.raises(ValueError, match='LEGACY_RECEIPT'):
        smoke.prepare_execution(a)


def test_explicit_mode_required(tmp_path):
    a = args(tmp_path)
    a.new_run = False
    with pytest.raises(ValueError, match='EXPLICIT_NEW_RUN'):
        smoke.prepare_execution(a)
    assert not a.receipt.exists()


def test_owned_runtime_and_artifact_tags_required(tmp_path):
    a = args(tmp_path)
    proof = owned(a)
    runtime = dict(agentRuntimeId=proof['runtime_id'], agentRuntimeArn=proof['runtime_arn'],
                   agentRuntimeName=smoke.NAME, agentRuntimeVersion='1')
    tags = {'project': smoke.PROJECT, 'purpose': 'foundation-import-smoke',
            'artifact-sha256': a.sha256, 'probe-execution-id': proof['probe_execution_id']}
    smoke.validate_owned_runtime(proof, runtime, tags)
    tags['probe-execution-id'] = 'another-execution'
    with pytest.raises(ValueError, match='OWNED_RUNTIME_MISMATCH'):
        smoke.validate_owned_runtime(proof, runtime, tags)


def test_source_create_is_new_only_and_invoke_uses_default():
    tree = ast.parse(Path(smoke.__file__).read_text())
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
    creates = [n for n in calls if n.func.attr == 'create_agent_runtime']
    invokes = [n for n in calls if n.func.attr == 'invoke_agent_runtime']
    assert len(creates) == len(invokes) == 1
    assert any(isinstance(n, ast.If) and ast.unparse(n.test) == 'not a.resume'
               and creates[0] in list(ast.walk(n)) for n in ast.walk(tree))
    for call in calls:
        if call.func.attr in ('invoke_agent_runtime', 'stop_runtime_session'):
            assert next(k.value.value for k in call.keywords if k.arg == 'qualifier') == 'DEFAULT'
    source = Path(smoke.__file__).read_text()
    assert source.index("if proof['invokes']:") < source.index('result = r.invoke_agent_runtime')
    assert source.index("proof['invokes'] = 1") < source.index('result = r.invoke_agent_runtime')


@pytest.mark.parametrize('state', ['DELETED_VERIFIED', 'CREATE_OUTCOME_UNKNOWN'])
def test_run_rejects_nonresumable_state_before_any_sdk_session(tmp_path, monkeypatch, state):
    a = args(tmp_path)
    proof = owned(a)
    proof['cleanup'] = state
    a.receipt.write_text(json.dumps(proof))
    a.artifact = tmp_path / 'synthetic.zip'
    monkeypatch.setattr(smoke.subprocess, 'check_output', lambda *args, **kwargs: a.source_sha)
    monkeypatch.setattr(smoke, 'check_archive', lambda *args: b'synthetic')
    with pytest.raises(ValueError):
        smoke.run(a)
    assert json.loads(a.receipt.read_text()) == proof


def test_resume_preserves_already_attempted_invoke_count(tmp_path):
    a = args(tmp_path)
    proof = owned(a)
    proof['invokes'] = 1
    a.receipt.write_text(json.dumps(proof))
    assert smoke.prepare_execution(a)['invokes'] == 1
