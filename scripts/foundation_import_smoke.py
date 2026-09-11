"""Isolated first-target import smoke; no admission or production authority.

Requires explicit operator account/profile/region and the immutable base hash.
Never changes IAM, existing runtimes, admission, users or network configuration.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
import uuid
import zipfile

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from scripts.foundation_target import StudioTarget, PROJECT, STACK, sanitized
from scripts.package_foundation import SOURCES, source_digest

NAME = 'gab_foundation_import_smoke'  # Existing trust requires gab_foundation_*.
EXPECTED = {'status': 'BLOCKED',
            'code': 'AUTHENTICATED_BACKEND_REDEMPTION_NOT_CONNECTED',
            'production_ready': False}


def check_archive(path, expected):
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError('ARTIFACT_HASH_MISMATCH')
    with zipfile.ZipFile(path) as z:
        if len(z.namelist()) != len(set(z.namelist())):
            raise ValueError('DUPLICATE_ZIP_PATH')
        if any(n.endswith('admission.json') for n in z.namelist()):
            raise ValueError('BASE_ONLY_REQUIRED')
        status = json.loads(z.read('package-status.json'))
        if status != {'admission_config_present': False, 'deploy_ready': False,
                      'linux_execution': 'UNVERIFIED', 'mode': 'base'}:
            raise ValueError('BASE_STATUS_REQUIRED')
        root = Path(__file__).resolve().parents[1]
        if not all(z.read(n) == (root / n).read_bytes() for n in SOURCES):
            raise ValueError('SOURCE_BYTES_MISMATCH')
        manifest = json.loads(z.read('runtime/custom_foundation/harness.json'))
        if manifest['foundation']['digest'] != source_digest():
            raise ValueError('SOURCE_DIGEST_MISMATCH')
        native = [n for n in z.namelist() if n.endswith('.so')]
        if not native:
            raise ValueError('NATIVE_DEPENDENCIES_REQUIRED')
        for n in native:
            elf = z.read(n)
            if elf[:4] != b'\x7fELF' or int.from_bytes(elf[18:20], 'little') != 183:
                raise ValueError('LINUX_AARCH64_ELF_REQUIRED')
    return data


RECEIPT_VERSION = 2


def execution_identity(execution_id, binding):
    # Identity belongs to an execution, never to reusable artifact bytes.
    return hashlib.sha256(json.dumps(
        [NAME, execution_id, binding], sort_keys=True).encode()).hexdigest()


def prepare_execution(a):
    binding = {'artifact_sha256': a.sha256, 'source_sha': a.source_sha,
               'region': a.region,
               'account_sha256': hashlib.sha256(a.expected_account.encode()).hexdigest()}
    if getattr(a, 'resume', False):
        proof = json.loads(a.receipt.read_text())
        if proof.get('receipt_version') != RECEIPT_VERSION:
            raise ValueError('LEGACY_RECEIPT_REQUIRES_MANUAL_RECONCILIATION')
        if proof.get('execution_binding') != binding:
            raise ValueError('EXECUTION_BINDING_MISMATCH')
        execution_id = proof.get('probe_execution_id', '')
        if not re.fullmatch(r'[a-f0-9]{32}', execution_id):
            raise ValueError('INVALID_EXECUTION_ID')
        if proof.get('client_token') != execution_identity(execution_id, binding):
            raise ValueError('EXECUTION_TOKEN_MISMATCH')
        if proof.get('cleanup') == 'DELETED_VERIFIED':
            raise ValueError('COMPLETED_EXECUTION_REQUIRES_EXPLICIT_NEW_RUN')
        if not proof.get('runtime_id') or proof.get('cleanup') != 'REQUIRED':
            # Unknown create outcomes are NOT permission to create with a new token.
            raise ValueError('UNRESOLVED_EXECUTION_REQUIRES_MANUAL_RECONCILIATION')
        if proof.get('invokes') not in (0, 1):
            raise ValueError('INVALID_INVOKE_COUNT')
        return proof
    if not getattr(a, 'new_run', False):
        raise ValueError('EXPLICIT_NEW_RUN_OR_RESUME_REQUIRED')
    execution_id = uuid.uuid4().hex
    proof = {'receipt_version': RECEIPT_VERSION, 'probe_execution_id': execution_id,
             'execution_binding': binding,
             'client_token': execution_identity(execution_id, binding),
             'cleanup': 'NO_RUNTIME_CREATED', 'invokes': 0}
    # Reserve the receipt before AWS activity; never overwrite an older execution.
    a.receipt.parent.mkdir(parents=True, exist_ok=True)
    with a.receipt.open('x') as f:
        a.receipt.chmod(0o600)
        json.dump(proof, f)
    return proof


def validate_owned_runtime(proof, runtime, tags):
    if (runtime.get('agentRuntimeId') != proof['runtime_id']
            or re.sub(r'\b\d{12}\b', '[ACCOUNT]', runtime.get('agentRuntimeArn', ''))
            != re.sub(r'\b\d{12}\b', '[ACCOUNT]', proof['runtime_arn'])
            or runtime.get('agentRuntimeName') != NAME
            or runtime.get('agentRuntimeVersion') != proof['runtime_version']
            or tags.get('project') != PROJECT
            or tags.get('purpose') != 'foundation-import-smoke'
            or tags.get('artifact-sha256') != proof['execution_binding']['artifact_sha256']
            or tags.get('probe-execution-id') != proof['probe_execution_id']):
        raise ValueError('OWNED_RUNTIME_MISMATCH')


def run(a):
    started = time.monotonic()
    if subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip() != a.source_sha:
        raise ValueError('SOURCE_COMMIT_MISMATCH')
    data = check_archive(a.artifact, a.sha256)
    proof = prepare_execution(a)
    proof.update({'artifact_sha256': a.sha256, 'source_digest': source_digest(),
             'source_sha': a.source_sha, 'production_ready': False,
             'network': 'PUBLIC explicitly authorized isolated startup only',
             'actual_usage': None, 'actual_cost_usd': None,
             'variable_cost_estimate': '<1 USD engineering estimate, not invoice cap',
             'not_proven': ['admission', 'gateway', 'model', 'Browser', 'eval', 'UI'],
             'result': 'BLOCKED_OR_FAILED'})
    target = StudioTarget(boto3.Session(profile_name=a.profile, region_name=a.region))
    rid = arn = version = None
    sid = str(uuid.uuid5(uuid.NAMESPACE_URL, NAME + proof['probe_execution_id']))
    token = proof['client_token']
    def save():
        proof['duration_seconds'] = round(time.monotonic() - started, 2)
        a.receipt.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(proof, indent=2, default=str)
        text = re.sub(r'\b\d{12}\b', '[ACCOUNT]', text)
        a.receipt.touch(mode=0o600, exist_ok=True)
        a.receipt.chmod(0o600)
        a.receipt.write_text(text + '\n')
    def gate():
        result = target.verify()
        if target.account != a.expected_account:
            raise ValueError('EXPLICIT_ACCOUNT_MISMATCH')
        return result
    try:
        proof['identity'] = gate()
        cf = target.client('cloudformation')
        stack = cf.describe_stacks(StackName=STACK)['Stacks'][0]
        if (stack['StackId'].split(':')[3:5] != [a.region, a.expected_account]
                or {x['Key']: x['Value'] for x in stack['Tags']}.get('project') != PROJECT):
            raise ValueError('FOUNDATION_STACK_IDENTITY_MISMATCH')
        outputs = {x['OutputKey']: x['OutputValue'] for x in stack['Outputs']}
        role = outputs['FoundationRole']
        iam = target.client('iam')
        role_name = role.split('/')[-1]
        trust = iam.get_role(RoleName=role_name)['Role']['AssumeRolePolicyDocument']
        statements = trust['Statement']
        expected_trust = {'Effect': 'Allow', 'Principal': {'Service': 'bedrock-agentcore.amazonaws.com'},
                          'Action': 'sts:AssumeRole', 'Condition': {
                              'StringEquals': {'aws:SourceAccount': a.expected_account},
                              'ArnLike': {'aws:SourceArn': f'arn:aws:bedrock-agentcore:{a.region}:{a.expected_account}:runtime/gab_foundation_*'}}}
        if statements != [expected_trust]:
            raise ValueError('EXISTING_TRUST_MISMATCH')
        policy_names = iam.list_role_policies(RoleName=role_name)['PolicyNames']
        policies = {n: iam.get_role_policy(RoleName=role_name, PolicyName=n)['PolicyDocument'] for n in policy_names}
        overlay = policies['foundation-admission-exchange-only']['Statement']
        if not any(s['Effect'] == 'Deny' and s['Action'] == ['lambda:InvokeFunction'] and s['Resource'] == '*' for s in overlay):
            raise ValueError('DIRECT_LAMBDA_DENY_REQUIRED')
        if not any(s['Effect'] == 'Allow' and s['Action'] == ['execute-api:Invoke'] and
                   s['Resource'].startswith(f'arn:aws:execute-api:{a.region}:{a.expected_account}:') and
                   s['Resource'].endswith('/$default/POST/internal/foundation/exchange') and '*' not in s['Resource'] for s in overlay):
            raise ValueError('EXACT_EXCHANGE_ALLOW_REQUIRED')
        proof['role_policies_sha256'] = hashlib.sha256(json.dumps(policies, sort_keys=True).encode()).hexdigest()
        model_policies = [iam.get_role_policy(RoleName='gab-foundation-m0-model', PolicyName=n)['PolicyDocument']
                          for n in iam.list_role_policies(RoleName='gab-foundation-m0-model')['PolicyNames']]
        if not any(s['Effect'] == 'Deny' and 'bedrock-mantle:CreateInference' in s['Action']
                   for p in model_policies for s in p['Statement']):
            raise ValueError('MODEL_INFERENCE_DENY_REQUIRED')
        proof['iam_changes'] = 0
        c = target.client('bedrock-agentcore-control')
        existing = []
        for page in c.get_paginator('list_agent_runtimes').paginate():
            existing += [r for r in page['agentRuntimes'] if r['agentRuntimeName'] == NAME]
        if a.resume:
            runtime = c.get_agent_runtime(agentRuntimeId=proof['runtime_id'],
                                          agentRuntimeVersion=proof['runtime_version'])
            tags = c.list_tags_for_resource(resourceArn=runtime['agentRuntimeArn'])['tags']
            validate_owned_runtime(proof, runtime, tags)
            if any(r['agentRuntimeId'] != proof['runtime_id'] for r in existing):
                raise ValueError('UNEXPECTED_ADDITIONAL_PROBE')
            rid, arn, version = proof['runtime_id'], runtime['agentRuntimeArn'], proof['runtime_version']
            if proof['invokes']:
                raise ValueError('INVOKE_ALREADY_ATTEMPTED_CLEANUP_ONLY')
        elif existing:
            raise ValueError('EXISTING_PROBE_REQUIRES_RECEIPT_RECONCILIATION_NO_DUPLICATE')
        if not a.resume:
            art = cf.describe_stacks(StackName=PROJECT + '-serverless-artifacts')['Stacks'][0]
            bucket = {x['OutputKey']: x['OutputValue'] for x in art['Outputs']}['Bucket']
            s3 = target.client('s3')
            if not all(s3.get_public_access_block(Bucket=bucket)['PublicAccessBlockConfiguration'].values()):
                raise ValueError('PRIVATE_BUCKET_REQUIRED')
            key = 'foundation-import-smoke/' + a.sha256 + '/runtime-base.zip'
            gate()
            try:
                head = s3.head_object(Bucket=bucket, Key=key)
            except ClientError as e:
                if e.response['Error']['Code'] not in ('404', 'NoSuchKey'):
                    raise
                s3.put_object(Bucket=bucket, Key=key, Body=data, ServerSideEncryption='AES256',
                              ContentType='application/zip', IfNoneMatch='*', Metadata={'sha256': a.sha256})
                head = s3.head_object(Bucket=bucket, Key=key)
            version_id = head.get('VersionId')
            args = {'Bucket': bucket, 'Key': key}
            if version_id:
                args['VersionId'] = version_id
            downloaded = s3.get_object(**args)['Body'].read()
            if hashlib.sha256(downloaded).hexdigest() != a.sha256:
                raise ValueError('S3_DOWNLOAD_HASH_MISMATCH')
            proof['s3'] = {'bucket': bucket, 'key': key, 'version_id': version_id, 'download_hash_verified': True}
            artifact = {'bucket': bucket, 'prefix': key}
            if version_id:
                artifact['versionId'] = version_id
            gate()
            proof['cleanup'] = 'CREATE_OUTCOME_UNKNOWN'
            save()
            response = c.create_agent_runtime(agentRuntimeName=NAME,
                agentRuntimeArtifact={'codeConfiguration': {'code': {'s3': artifact},
                                      'runtime': 'PYTHON_3_13', 'entryPoint': ['main.py']}},
                roleArn=role, networkConfiguration={'networkMode': 'PUBLIC'},
                protocolConfiguration={'serverProtocol': 'HTTP'},
                lifecycleConfiguration={'idleRuntimeSessionTimeout': 60, 'maxLifetime': 120},
                clientToken=token, description='Owned isolated base-only import smoke; no admission or inference',
                tags={'project': PROJECT, 'purpose': 'foundation-import-smoke', 'artifact-sha256': a.sha256,
                      'probe-execution-id': proof['probe_execution_id']})
            rid, arn = response['agentRuntimeId'], response['agentRuntimeArn']
            version = response['agentRuntimeVersion']
            proof.update(runtime_id=rid, runtime_arn=arn, runtime_version=version, cleanup='REQUIRED')
            save()
        deadline = time.monotonic() + 300
        while True:
            runtime = c.get_agent_runtime(agentRuntimeId=rid, agentRuntimeVersion=version)
            proof['runtime_status'] = runtime['status']
            save()
            if runtime['status'] == 'READY':
                break
            if runtime['status'] in ('CREATE_FAILED', 'UPDATE_FAILED') or time.monotonic() >= deadline:
                raise RuntimeError('RUNTIME_NOT_READY: ' + sanitized(RuntimeError(runtime.get('failureReason', runtime['status']))))
            time.sleep(10)
        proof['runtime_configuration'] = {'network': runtime['networkConfiguration'], 'artifact': runtime['agentRuntimeArtifact']}
        # Invoke qualifier is an ENDPOINT NAME, not a numeric runtime version.
        endpoint = c.get_agent_runtime_endpoint(agentRuntimeId=rid, endpointName='DEFAULT')
        if endpoint['status'] != 'READY' or endpoint['liveVersion'] != version:
            raise ValueError('DEFAULT_ENDPOINT_VERSION_NOT_READY')
        proof['endpoint'] = {'name': 'DEFAULT', 'live_version': endpoint['liveVersion']}
        r = target.session.client('bedrock-agentcore', config=Config(retries={'total_max_attempts': 1}, connect_timeout=5, read_timeout=120))
        proof['invokes'] = 1
        save()
        result = r.invoke_agent_runtime(agentRuntimeArn=arn, qualifier='DEFAULT', runtimeSessionId=sid,
                                       contentType='application/json', payload=b'{"run_ref":"synthetic-smoke"}')
        body = json.loads(result['response'].read())
        proof['http_status'] = result['ResponseMetadata']['HTTPStatusCode']
        proof['response_matches_expected'] = body == EXPECTED
        if body != EXPECTED:
            raise ValueError('UNEXPECTED_RESPONSE_BODY_NOT_RECORDED')
        proof['response'] = body
        proof['result'] = 'PASS_FIRST_TARGET_STARTUP_IMPORT_ONLY'
        proof['model_tool_calls'] = '0 by verified missing-admission branch, not billing telemetry'
    except Exception as e:
        proof['result'] = 'BLOCKED_OR_FAILED'
        proof['error'] = sanitized(e)
    finally:
        if rid:
            errors = []
            if proof['invokes']:
                try:
                    gate()
                    target.client('bedrock-agentcore').stop_runtime_session(agentRuntimeArn=arn, qualifier='DEFAULT', runtimeSessionId=sid)
                    proof['session_stop'] = 'ACCEPTED'
                except Exception as e:
                    errors.append(sanitized(e))
            try:
                gate()
                c.delete_agent_runtime(agentRuntimeId=rid, clientToken=token + '-delete')
                proof['cleanup'] = 'DELETE_REQUESTED'
                until = time.monotonic() + 90
                while time.monotonic() < until:
                    try:
                        c.get_agent_runtime(agentRuntimeId=rid)
                    except ClientError as e:
                        if e.response['Error']['Code'] == 'ResourceNotFoundException':
                            proof['cleanup'] = 'DELETED_VERIFIED'
                            break
                        raise
                    time.sleep(5)
            except Exception as e:
                errors.append(sanitized(e))
            if errors:
                proof['cleanup_errors'] = errors
        save()
    print(a.receipt.read_text())
    return 0 if proof['result'].startswith('PASS') and proof['cleanup'] == 'DELETED_VERIFIED' else 2


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--expected-account', required=True)
    p.add_argument('--profile', required=True, choices=['agentic-platform-prod'])
    p.add_argument('--region', required=True, choices=['us-west-2'])
    p.add_argument('--artifact', required=True, type=Path)
    p.add_argument('--sha256', required=True)
    p.add_argument('--source-sha', required=True)
    p.add_argument('--receipt', required=True, type=Path)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--new-run', action='store_true', help='New execution; requires a new receipt path')
    mode.add_argument('--resume', action='store_true', help='Resume only the receipt-owned runtime; never create')
    a = p.parse_args()
    if not re.fullmatch(r'\d{12}', a.expected_account) or not re.fullmatch(r'[a-f0-9]{64}', a.sha256):
        p.error('Exact account and SHA-256 required')
    if a.new_run and a.receipt.exists():
        p.error('Receipt exists: use --resume or --new-run with a new receipt path')
    raise SystemExit(run(a))
