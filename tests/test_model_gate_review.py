from copy import deepcopy
import json
import pytest
from infra.foundation import template
from infra.model_gate import overlay, inspect_change_set, DEPENDENCIES
from scripts.model_gate_probe import negative_cases
from scripts.model_gate_review import resolve
from tests.test_model_gate import changes


def reviewed():
    old = template(); new = overlay(old); change = changes()
    for name, (prop, cause, kind) in DEPENDENCIES.items():
        change['Changes'].append({'ResourceChange': {'LogicalResourceId': name,
            'ResourceType': kind, 'Action': 'Modify', 'Replacement': 'False',
            'Details': [{'Target': {'Attribute': 'Properties', 'Name': prop, 'RequiresRecreation': 'Never'},
                'Evaluation': 'Dynamic', 'ChangeSource': 'ResourceAttribute', 'CausingEntity': cause}]}})
    return change, old, new, {name: 'verified-digest' for name in DEPENDENCIES}


def test_exact_two_dependencies():
    assert inspect_change_set(*reviewed())


@pytest.mark.parametrize('name', list(DEPENDENCIES))
@pytest.mark.parametrize('field,value', [('Evaluation','Static'), ('ChangeSource','DirectModification'),
    ('CausingEntity','Unreviewed.Arn'), ('Target', {'Attribute':'Properties','Name':'Policies','RequiresRecreation':'Always'})])
def test_dependency_detail_rejections(name, field, value):
    c, old, new, evidence = reviewed()
    resource = next(x['ResourceChange'] for x in c['Changes'] if x['ResourceChange']['LogicalResourceId']==name)
    resource['Details'][0][field] = value
    with pytest.raises(ValueError): inspect_change_set(c, old, new, evidence)


@pytest.mark.parametrize('field,value', [('Action','Remove'), ('Replacement','Conditional'), ('ResourceType','AWS::S3::Bucket'), ('Details',[])])
def test_dependency_resource_rejections(field,value):
    c, old, new, evidence = reviewed()
    c['Changes'][-1]['ResourceChange'][field] = value
    with pytest.raises(ValueError): inspect_change_set(c, old, new, evidence)


def test_changed_template_and_missing_live_evidence_rejected():
    c, old, new, evidence = reviewed()
    with pytest.raises(ValueError): inspect_change_set(c, old, new)
    new['Resources']['ToolPolicy']['Properties']['Name'] = 'changed'
    with pytest.raises(ValueError): inspect_change_set(c, old, new, evidence)


def test_intrinsics_use_exact_actual_resource_principals():
    attrs = {'Role.Arn':'exact-role', 'Gateway.GatewayArn':'exact-gateway'}
    assert resolve({'Fn::GetAtt':['Role','Arn']}, attrs, {}) == 'exact-role'
    assert resolve({'Fn::Sub':'${Role.Arn}:${Gateway.GatewayArn}'}, attrs, {}) == 'exact-role:exact-gateway'
    with pytest.raises(KeyError): resolve({'Fn::GetAtt':['Other','Arn']}, attrs, {})
    with pytest.raises(ValueError): resolve({'Fn::Join':[]}, attrs, {})


def test_exact_four_negatives_no_positive():
    body = {'model':'approved', 'max_tokens':1, 'stream':False}
    cases = negative_cases(body)
    assert [n for n,_ in cases] == ['unknown_model','bad_json','stream_true','excessive_max_tokens']
    assert json.loads(cases[0][1])['model'] == 'unapproved/model'
    assert cases[1][1] == b'{'
    assert json.loads(cases[2][1])['stream'] is True
    assert json.loads(cases[3][1])['max_tokens'] == 257
