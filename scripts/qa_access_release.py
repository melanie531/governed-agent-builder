"""Narrow QA authentication patch to three existing Lambda code packages only.

Preserves every other ZIP member and each function's configuration/resources.
Uses fresh revision IDs, backups, readback hashes; never updates CloudFormation.
"""
import base64
import copy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import urllib.request
import zipfile

import boto3

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'artifacts/qa-access'
REPLACEMENTS = [
    ('        return self.resolve(claims), row["csrf"]',
     '        from .qa_enrollment import approved\n        approved(self, claims, required=cookie.startswith(\'qa.\'))\n        return self.resolve(claims), row["csrf"]'),
    ('        if identity.get("email_verified") is True:\n            self.mint(result, access, tokens["access_token"], email)',
     '        from .qa_enrollment import approved\n        qa = approved(self, access)\n        if identity.get("email_verified") is True or qa:\n            self.mint(result, access, tokens["access_token"], email, qa=qa)'),
    ('    def mint(self, result, access, token, email):\n        self.resolve(access, email)\n        cookie, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)',
     '    def mint(self, result, access, token, email, qa=False):\n        if qa:\n            from .qa_enrollment import approved\n            approved(self, access, required=True)\n        self.resolve(access, email)\n        cookie, csrf = (\'qa.\' if qa else \'\') + secrets.token_urlsafe(32), secrets.token_urlsafe(32)')]


def main():
    # Code and tests must be committed before cloud mutation.
    assert not subprocess.check_output(['git', 'diff', 'HEAD', '--', 'backend/hosted_auth.py',
        'backend/qa_enrollment.py', 'tests/test_qa_enrollment.py'], cwd=ROOT)
    sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT).decode().strip()
    target = json.loads((WORK/'target.json').read_text())
    session = boto3.Session(profile_name='agentic-platform-prod', region_name='us-west-2')
    assert session.client('sts').get_caller_identity()['Account'] == target['account']
    cf = session.client('cloudformation')
    stack = cf.describe_stacks(StackName=target['stack'])['Stacks'][0]
    assert {x['OutputKey']: x['OutputValue'] for x in stack['Outputs']} == target['outputs']
    resources = {x['LogicalResourceId']: x['PhysicalResourceId'] for x in cf.list_stack_resources(StackName=target['stack'])['StackResourceSummaries']}
    assert resources == target['resources']
    client = session.client('lambda')
    registry = (WORK/'enrollments.json').read_bytes()
    assert len(json.loads(registry)['enrollments']) == 2
    patches = {}
    for role in ['Authorizer', 'Business', 'Auth']:
        old = target['functions'][role]
        live = client.get_function(FunctionName=old['name'])['Configuration']
        assert live['CodeSha256'] == old['hash'] and live['RevisionId'] == old['revision']
        assert live['Environment']['Variables'] == old['env']
        original = zipfile.ZipFile(WORK/(role+'-rollback.zip'))
        auth = original.read('backend/hosted_auth.py').decode()
        for before, after in REPLACEMENTS:
            assert auth.count(before) == 1
            auth = auth.replace(before, after)
        replacements = {'backend/hosted_auth.py': auth.encode(),
            'backend/qa_enrollment.py': (ROOT/'backend/qa_enrollment.py').read_bytes(),
            'backend/qa_enrollments.json': registry}
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
            for item in original.infolist():
                archive.writestr(copy.copy(item), replacements.pop(item.filename, original.read(item.filename)))
            for name, value in replacements.items(): archive.writestr(name, value)
        raw = stream.getvalue()
        patched = zipfile.ZipFile(io.BytesIO(raw))
        allowed = {'backend/hosted_auth.py', 'backend/qa_enrollment.py', 'backend/qa_enrollments.json'}
        assert set(patched.namelist()) - set(original.namelist()) <= allowed
        for name in original.namelist():
            if name not in allowed: assert original.read(name) == patched.read(name)
        (WORK/(role+'-release.zip')).write_bytes(raw)
        patches[role] = raw
    receipt = {'commit': sha, 'functions': {}, 'browser_authenticated': False}
    for role, raw in patches.items():
        old = target['functions'][role]
        client.update_function_code(FunctionName=old['name'], ZipFile=raw, RevisionId=old['revision'])
        client.get_waiter('function_updated_v2').wait(FunctionName=old['name'], WaiterConfig={'Delay': 2, 'MaxAttempts': 60})
        f = client.get_function(FunctionName=old['name'])
        expected = base64.b64encode(hashlib.sha256(raw).digest()).decode()
        assert f['Configuration']['CodeSha256'] == expected
        readback = urllib.request.urlopen(f['Code']['Location']).read()
        assert readback == raw
        assert f['Configuration']['Environment']['Variables'] == old['env']
        receipt['functions'][role] = {'rollback_hash': old['hash'], 'cloud_hash': expected,
            'download_verified': True, 'only_auth_members_changed': True}
        (WORK/'release-receipt.json').write_text(json.dumps(receipt, indent=2))
        print(role, 'code update and downloaded readback PASS', flush=True)
    resources = {x['LogicalResourceId']: x['PhysicalResourceId'] for x in cf.list_stack_resources(StackName=target['stack'])['StackResourceSummaries']}
    assert resources == target['resources']
    receipt['resource_ids_unchanged'] = True
    pool = session.client('cognito-idp').describe_user_pool(UserPoolId=target['outputs']['UserPoolId'])['UserPool']
    for field in ['Policies', 'UsernameAttributes', 'AutoVerifiedAttributes', 'AdminCreateUserConfig']:
        assert pool.get(field) == target['pool_snapshot'].get(field)
    receipt['pool_policy_unchanged'] = True
    (WORK/'release-receipt.json').write_text(json.dumps(receipt, indent=2))


if __name__ == '__main__': main()
