import json
from copy import deepcopy
import zipfile
import pytest
from scripts.foundation_probe import example_config
from scripts.package_foundation import package, save_config
from foundation_harness.package_admission import admission_config, validate_admission
from scripts.verify_package_admission import verify_package_admission


def values(tmp_path):
    raw = example_config()
    saved = save_config(raw, tmp_path/'manifests')
    settings = admission_config(raw, 'https://synthetic.execute-api.us-west-2.amazonaws.com/internal/foundation/exchange',
        'arn:aws:iam::'+'9988'+'77665544'+':role/synthetic')
    return raw, saved, settings


def test_live_missing_admission_fails_before_output(tmp_path):
    _, saved, _ = values(tmp_path)
    with pytest.raises(ValueError, match='EXACT_ADMISSION'):
        package(saved, tmp_path/'bad.zip')
    assert not (tmp_path/'bad.zip').exists()


@pytest.mark.parametrize('field,value', [('endpoint','https://evil.invalid'),
    ('endpoint','https://synthetic.execute-api.us-west-2.amazonaws.com/internal/foundation/exchange?token=x'),
    ('manifest_digest','b'*64), ('foundation_digest','b'*64), ('runtime_role','*'),
    ('binding_ref','a'*64), ('credentials',{'synthetic':'value'})])
def test_invalid_cross_manifest_and_credentials_rejected(tmp_path, field, value):
    raw, saved, settings = values(tmp_path)
    settings[field] = value
    with pytest.raises(ValueError):
        package(saved, tmp_path/'bad.zip', admission=settings)
    assert not (tmp_path/'bad.zip').exists()


def test_plausible_other_endpoint_cannot_change_pinned_reference(tmp_path):
    raw, _, settings = values(tmp_path)
    settings['endpoint'] = settings['endpoint'].replace('synthetic.', 'another.')
    with pytest.raises(ValueError, match='REFERENCE_BINDING'):
        validate_admission(settings, raw)


def test_user_supplied_authority_is_not_platform_approval(tmp_path):
    raw, saved, settings = values(tmp_path)
    with pytest.raises(ValueError, match='PLATFORM_OWNED'):
        package(saved, tmp_path/'bad.zip', admission=settings, approved={'authority': True})
    with pytest.raises(ValueError, match='PLATFORM_OWNED'):
        verify_package_admission(raw)


def test_explicit_base_is_labeled_and_exports_no_credentials(tmp_path, monkeypatch):
    raw, saved, settings = values(tmp_path)
    marker = 'synthetic-private-marker-do-not-export'
    monkeypatch.setenv('AWS_SECRET_ACCESS_KEY', marker)
    result = package(saved, tmp_path/'base.zip', mode='base')
    assert result['package_mode'] == 'base' and not result['deploy_ready']
    with zipfile.ZipFile(tmp_path/'base.zip') as archive:
        assert 'runtime/custom_foundation/admission.json' not in archive.namelist()
        assert json.loads(archive.read('package-status.json'))['mode'] == 'base'
        assert all(marker.encode() not in archive.read(n) for n in archive.namelist())
    with pytest.raises(ValueError, match='BASE_MUST_NOT'):
        package(saved, tmp_path/'mixed.zip', mode='base', admission=settings)


def test_live_package_export_has_exact_configs_no_env_credentials(tmp_path, monkeypatch):
    raw, saved, settings = values(tmp_path)
    marker = 'synthetic-not-a-real-credential'
    monkeypatch.setenv('AWS_SESSION_TOKEN', marker)
    monkeypatch.setattr('scripts.verify_package_admission.verify_package_admission', lambda *a, **k: settings)
    monkeypatch.setattr('scripts.package_foundation.dependency_files', lambda _: {})
    proof = package(saved, tmp_path/'live.zip', admission=settings, dependencies=tmp_path)
    with zipfile.ZipFile(tmp_path/'live.zip') as archive:
        assert json.loads(archive.read('runtime/custom_foundation/admission.json')) == settings
        assert json.loads(archive.read('runtime/custom_foundation/harness.json')) == raw
        assert all(marker.encode() not in archive.read(n) for n in archive.namelist())
    assert proof['admission_config_present'] and not proof['deploy_ready']


def test_other_platform_endpoint_is_rejected_even_with_rehashed_reference(tmp_path, monkeypatch):
    import time
    from types import SimpleNamespace
    raw, _, settings = values(tmp_path)
    approved = {'config':raw,'manifest_digest':settings['manifest_digest'],
        'definition_digest':'a'*64,'runtime_admission_reviewed':True,
        'expires_at':time.time()+60,'role':settings['runtime_role'],'admission':settings}
    monkeypatch.setattr('scripts.verify_package_admission.read_platform_approval', lambda *a, **k: approved)
    role = {'Arn':settings['runtime_role'],'AssumeRolePolicyDocument':{'Statement':[
        {'Effect':'Allow','Principal':{'Service':'bedrock-agentcore.amazonaws.com'},
         'Action':'sts:AssumeRole','Condition':{'StringEquals':{'synthetic':'bound'}}}]}}
    clients = {
        'cloudformation':SimpleNamespace(describe_stacks=lambda **k:{'Stacks':[{'Outputs':[
            {'OutputKey':'ApiEndpoint','OutputValue':'https://different.execute-api.us-west-2.amazonaws.com'}]}]},
            list_stack_resources=lambda **k:{'StackResourceSummaries':[{'LogicalResourceId':'FoundationRole','PhysicalResourceId':'synthetic'}]}),
        'apigatewayv2':SimpleNamespace(get_routes=lambda **k:{'Items':[{'RouteKey':'POST /internal/foundation/exchange','AuthorizationType':'AWS_IAM'}]}),
        'iam':SimpleNamespace(get_role=lambda **k:{'Role':role})}
    target=SimpleNamespace(verify=lambda:None,account='9988'+'77665544',client=clients.__getitem__)
    with pytest.raises(ValueError,match='PLATFORM_ENDPOINT_BINDING_MISMATCH'):
        verify_package_admission(raw,approved=approved,target=target)
    # Equal protected metadata yields only data references, not an invocation grant.
    approved['admission']=admission_config(raw,settings['endpoint'].replace('synthetic.','different.'),settings['runtime_role'])
    assert verify_package_admission(raw,approved=approved,target=target)==approved['admission']
    copied=deepcopy(approved);copied['definition_digest']='b'*64
    with pytest.raises(ValueError,match='PROTECTED_APPROVAL_READBACK'):
        verify_package_admission(raw,approved=copied,target=target)


def test_runtime_missing_invalid_cross_manifest_config_never_creates_sdk_session(tmp_path, monkeypatch):
    import boto3
    from runtime.custom_foundation import main
    raw, _, settings = values(tmp_path)
    monkeypatch.setattr(main, '__file__', str(tmp_path/'main.py'))
    calls=[]
    monkeypatch.setattr(boto3, 'Session', lambda **kw: calls.append(kw))
    assert main.invoke({'run_ref':'synthetic-run'})['code']=='AUTHENTICATED_BACKEND_REDEMPTION_NOT_CONNECTED'
    (tmp_path/'harness.json').write_text(json.dumps(raw))
    for settings in [{}, {**settings,'manifest_digest':'f'*64}, {**settings,'endpoint':'https://evil.invalid'}]:
        (tmp_path/'admission.json').write_text(json.dumps(settings))
        assert main.invoke({'run_ref':'synthetic-run'})['code']=='AUTHENTICATED_BACKEND_ADMISSION_FAILED'
    assert calls==[]
