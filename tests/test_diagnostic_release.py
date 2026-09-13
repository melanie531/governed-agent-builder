"""Synthetic offline packaging and exact additive-deployment checks."""
import ast
import copy
import json
from pathlib import Path
import zipfile
import pytest
from infra.diagnostic_capture import assemble, LOGICAL_IDS, runtime_isolation_policy
from scripts import package_diagnostic_capture as packaging

ACCOUNT = '9988' + '77665544'
RUNTIME = f'arn:aws:bedrock-agentcore:us-west-2:{ACCOUNT}:runtime/gab_foundation_example-example'
BINDINGS = dict(bucket='synthetic-approved-artifacts', key='approved/diagnostic/backend.zip',
    version='synthetic-version', runtime_arn=RUNTIME, endpoint_arn=RUNTIME+'/runtime-endpoint/DEFAULT')


def current():
    return {'Resources': {
        'Api': {'Type': 'AWS::ApiGatewayV2::Api'},
        'State': {'Type': 'AWS::DynamoDB::Table'},
        'Stage': {'Type': 'AWS::ApiGatewayV2::Stage', 'Properties': {'StageName': '$default'}},
        'FoundationExchange': {'Type': 'AWS::Lambda::Function', 'Properties': {'Existing': 'preserve'}},
        'BusinessRole': {'Type': 'AWS::IAM::Role', 'Properties': {'general_requests': 'preserve'}}},
        'Parameters': {'ArtifactKey': {'Type': 'String'}}, 'Outputs': {'Existing': {'Value': 'preserve'}}}


def test_add_only_preserves_every_existing_field():
    before=current(); saved=copy.deepcopy(before); result=assemble(before, **BINDINGS)
    assert before==saved
    assert set(result['Resources'])-set(before['Resources'])==LOGICAL_IDS
    for k,v in before.items():
        if k!='Resources':assert result[k]==v
    for k,v in before['Resources'].items():assert result['Resources'][k]==v
    r=result['Resources']
    assert r['DiagnosticCaptureRoute']['Properties']['AuthorizationType']=='AWS_IAM'
    f=r['DiagnosticCaptureExchange']['Properties']
    assert f['Environment']['Variables']['DIAGNOSTIC_CAPTURE_EXCHANGE_ENABLED']=='0'
    assert f['ReservedConcurrentExecutions']==1 and f['Timeout']==15
    assert f['Code']['S3ObjectVersion']=='synthetic-version'
    assert r['DiagnosticCapturePermission']['Properties']['SourceArn']['Fn::Sub'].endswith('/$default/POST/internal/diagnostic/capture')
    assert '* ' not in json.dumps(r['DiagnosticCapturePermission'])
    statements=r['DiagnosticCaptureRole']['Properties']['Policies'][0]['PolicyDocument']['Statement']
    for s in statements:
        if s['Effect']=='Allow':assert s['Resource']!='*'
    writes=[s for s in statements if 'dynamodb:PutItem' in s['Action']][0]
    assert writes['Condition']['ForAllValues:StringEquals']['dynamodb:LeadingKeys']==['_revision','settings']
    assert all('dynamodb:DeleteItem' not in s['Action'] for s in statements)


@pytest.mark.parametrize('field,value',[('version','null'),('key','other/code.zip'),('runtime_arn',RUNTIME.replace('gab_foundation','shared')),('endpoint_arn',RUNTIME+'/runtime-endpoint/*')])
def test_reject_unbound_deployment(field,value):
    with pytest.raises(ValueError):assemble(current(),**{**BINDINGS,field:value})


def test_refuses_existing_diagnostic_resource():
    with pytest.raises(ValueError):assemble(assemble(current(),**BINDINGS),**BINDINGS)


def test_runtime_has_no_state_allow():
    p=runtime_isolation_policy('syntheticapi',f'arn:aws:lambda:us-west-2:{ACCOUNT}:function:diagnostic')
    assert [s['Action'] for s in p['Statement'] if s['Effect']=='Allow']==[['execute-api:Invoke']]
    assert any(s['Effect']=='Deny' and 'dynamodb:*' in s['Action'] for s in p['Statement'])


def test_backend_zip_copies_ticket_dependency():
    source=(Path(__file__).resolve().parents[1]/'scripts/serverless_package.py').read_text()
    tree=ast.parse(source)
    copying=[n for n in ast.walk(tree) if isinstance(n,ast.For) and isinstance(n.iter,ast.Tuple)
             and any(isinstance(e,ast.Constant) and e.value=='foundation_target.py' for e in n.iter.elts)]
    assert len(copying)==1
    assert 'opus_capture_ticket.py' in [e.value for e in copying[0].iter.elts]


def test_diagnostic_zip_has_no_repository_or_production_admission(tmp_path,monkeypatch):
    monkeypatch.setattr(packaging,'dependency_files',lambda _: {'synthetic_dependency.py':b'pass\n'})
    settings={'region':'us-west-2','exchange_endpoint':'https://syntheticapi.execute-api.us-west-2.amazonaws.com/internal/diagnostic/capture','manifest_digest':'a'*64}
    dest=tmp_path/'diagnostic.zip'; receipt=packaging.package(settings,dest,tmp_path)
    with zipfile.ZipFile(dest) as z:
        names=set(z.namelist())
        assert 'runtime/diagnostic_capture.py' in names and 'foundation_harness/diagnostic_exchange.py' in names
        assert z.read('main.py').startswith(b'from runtime.diagnostic_capture import create_app')
        assert not any(n.startswith(('backend/','scripts/')) or n.endswith('/admission.json') for n in names)
        assert json.loads(z.read('runtime/capture-settings.json'))==settings
    assert receipt['production_ready'] is False and receipt['linux_execution']=='UNVERIFIED'
    with pytest.raises(FileExistsError):packaging.package(settings,dest,tmp_path)
