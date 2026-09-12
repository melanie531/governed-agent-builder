"""Owner-approved, one-shot QA provisioning. No credentials are printed or saved locally.

Uses the existing operator profile, existing pool and exact SSM namespace only.
Run after read-only target snapshot. Refuses all existing users/parameters, never
resets an owner or adopts an existing identity. Logs contain paths/status only.
"""
import json
import logging
import secrets
import string
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'artifacts/qa-access'
ORIGIN = 'https://de32ssfw7gsad.cloudfront.net'


def main():
    logging.disable(logging.CRITICAL)
    target = json.loads((WORK / 'target.json').read_text())
    session = boto3.Session(profile_name='agentic-platform-prod', region_name='us-west-2')
    identity = session.client('sts').get_caller_identity()
    assert identity['Account'] == target['account']
    cf = session.client('cloudformation')
    stack = cf.describe_stacks(StackName=target['stack'])['Stacks'][0]
    outputs = {x['OutputKey']: x['OutputValue'] for x in stack['Outputs']}
    assert outputs == target['outputs'] and outputs['ApplicationOrigin'] == ORIGIN
    dist = session.client('cloudfront').get_distribution(Id=outputs['DistributionId'])['Distribution']
    assert dist['DomainName'] == ORIGIN.removeprefix('https://') and dist['ARN'].split(':')[4] == identity['Account']
    pool = outputs['UserPoolId']
    cognito, ssm = session.client('cognito-idp'), session.client('ssm')
    assert cognito.describe_user_pool(UserPoolId=pool)['UserPool']['UsernameAttributes'] == ['email']
    specs = [('business', 'studio-research', 'research'), ('admin', 'studio-admin', 'platform')]
    # Complete absence checks before creating either user. Never fetch any secret value.
    for role, group, workspace in specs:
        username = f'qa-{role}@example.com'
        try:
            cognito.admin_get_user(UserPoolId=pool, Username=username)
        except cognito.exceptions.UserNotFoundException:
            pass
        else:
            raise RuntimeError('Dedicated QA username already exists; no reset permitted')
        for field in ['username', 'password']:
            name = f'/governed-agent-builder/qa/{role}/{field}'
            found = ssm.describe_parameters(ParameterFilters=[{'Key': 'Name', 'Option': 'Equals', 'Values': [name]}])
            assert not found['Parameters'], 'Existing QA parameter; no overwrite permitted'
        cognito.get_group(UserPoolId=pool, GroupName=group)
    rows, receipt = [], []
    for role, group, workspace in specs:
        username = f'qa-{role}@example.com'
        password = ''.join(secrets.choice(string.ascii_letters + string.digits + '!@#%+=_-') for _ in range(48))
        password += 'aA7!'
        prefix = f'/governed-agent-builder/qa/{role}'
        # Save only into target-account encrypted storage, never stdout/files/env.
        for field, value in [('username', username), ('password', password)]:
            ssm.put_parameter(Name=prefix+'/'+field, Value=value, Type='SecureString',
                              Overwrite=False, Description='Owner-approved dedicated Studio QA; rotate within 30 days')
        created = cognito.admin_create_user(UserPoolId=pool, Username=username,
            TemporaryPassword=password, MessageAction='SUPPRESS',
            UserAttributes=[{'Name': 'email', 'Value': username}])
        subject = next(a['Value'] for a in created['User']['Attributes'] if a['Name'] == 'sub')
        try:
            cognito.admin_set_user_password(UserPoolId=pool, Username=username, Password=password, Permanent=True)
            cognito.admin_add_user_to_group(UserPoolId=pool, Username=username, GroupName=group)
            user = cognito.admin_get_user(UserPoolId=pool, Username=username)
            attrs = {a['Name']: a['Value'] for a in user['UserAttributes']}
            assert attrs['sub'] == subject and attrs.get('email_verified', 'false') == 'false'
            assert user['Enabled'] and user['UserStatus'] == 'CONFIRMED'
            groups = cognito.admin_list_groups_for_user(UserPoolId=pool, Username=username)['Groups']
            assert [g['GroupName'] for g in groups] == [group]
        except Exception:
            cognito.admin_disable_user(UserPoolId=pool, Username=username)
            raise
        finally:
            del password
        now = int(time.time())
        rows.append(dict(subject=subject, issuer=f'https://cognito-idp.us-west-2.amazonaws.com/{pool}',
            client=outputs['ClientId'], origin=ORIGIN, group=group, role=role, workspace=workspace,
            enabled=True, enrolled_at=now, expires=now+30*86400,
            enrolled_by=identity['Arn'], approval='owner-direct-qa-enrollment-2026-09-12'))
        receipt.append(dict(role=role, status='CONFIRMED', enabled=True, email_verified=False,
            ssm_paths=[prefix+'/username', prefix+'/password']))
        (WORK/'enrollments.json').write_text(json.dumps({'enrollments': rows}, indent=2))
        (WORK/'provision-receipt.json').write_text(json.dumps(receipt, indent=2))
        print(json.dumps(receipt[-1]), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('QA provisioning stopped safely:', type(exc).__name__, flush=True)
        raise SystemExit(1) from None
