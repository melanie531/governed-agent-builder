"""Least-privilege IaC regression: only Business gains the new entity key."""
import copy
import subprocess
from infra.serverless import template

BASE='db7c6056b1417ef7650c27dff5b7cbdb8533857e'

def baseline():
    source=subprocess.check_output(['git','show',BASE+':infra/serverless.py'],text=True)
    namespace={'__name__':'infra.serverless_baseline','__package__':'infra'}
    exec(compile(source,'baseline/serverless.py','exec'),namespace)
    return namespace['template']()

def test_entire_template_changes_only_two_business_leadingkeys():
    old=baseline();new=template();expected=copy.deepcopy(old);changed=0
    for policy in expected['Resources']['BusinessRole']['Properties']['Policies']:
        for st in policy['PolicyDocument']['Statement']:
            keys=st.get('Condition',{}).get('ForAllValues:StringEquals',{}).get('dynamodb:LeadingKeys')
            if keys is not None:
                assert 'general_requests' not in keys
                st['Condition']['ForAllValues:StringEquals']['dynamodb:LeadingKeys']=[*keys,'general_requests'];changed+=1
    assert changed==2
    assert new==expected

def test_other_roles_and_unapproved_partitions_remain_ungranted():
    doc=template()
    for name,resource in doc['Resources'].items():
        if resource.get('Type')!='AWS::IAM::Role':continue
        for policy in resource['Properties'].get('Policies',[]):
            for st in policy['PolicyDocument']['Statement']:
                keys=st.get('Condition',{}).get('ForAllValues:StringEquals',{}).get('dynamodb:LeadingKeys')
                if keys is None:continue
                assert '*' not in keys
                assert 'unapproved_partition' not in keys
                if name!='BusinessRole':assert 'general_requests' not in keys
