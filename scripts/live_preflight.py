#!/usr/bin/env python3
"""Offline by default. No writes or paid operations; no credentials/account output.

Read-only mode requires an operator-owned, exact-target manifest. Do not use
until the parent has resolved account authority. No account discovery by guessing.
"""
import argparse
import json
from pathlib import Path
import sys


def validate_manifest(m):
    required = ('region', 'target_account', 'model_gateway_id', 'tool_gateway_id', 'registry_id',
                'runtime_role_arn', 'artifact_bucket', 'bedrock_model_id')
    missing = [k for k in required if not m.get(k)]
    if missing:
        raise ValueError('Missing manifest fields: ' + ', '.join(missing))
    import re
    if not re.fullmatch(r'\d{12}', m['target_account']):
        raise ValueError('Exact target account required')
    if m['model_gateway_id'] == m['tool_gateway_id']:
        raise ValueError('Distinct Model and Tool Gateways required')
    if not m['runtime_role_arn'].startswith(f"arn:aws:iam::{m['target_account']}:role/"):
        raise ValueError('Runtime role target mismatch')
    family = m['bedrock_model_id'].removeprefix('global.').removeprefix('us.').removeprefix('eu.').removeprefix('apac.')
    if not family.startswith(('anthropic.claude-', 'openai.gpt-')):
        raise ValueError('Only Bedrock Claude or Bedrock OpenAI are allowed')


def check_sdk():
    from botocore.session import Session
    model = Session().get_service_model('bedrock-agentcore-control')
    for name in ('ListRegistryRecords', 'GetRegistryRecord', 'ListGatewayTargets', 'GetGatewayTarget',
                 'CreateAgentRuntime', 'UpdateAgentRuntime', 'GetAgentRuntime'):
        model.operation_model(name)


def readonly(m, session):
    # First and only initial remote call; abort before resource reads on mismatch.
    if session.client('sts').get_caller_identity()['Account'] != m['target_account']:
        raise ValueError('Target identity mismatch; no resource discovery performed')
    control = session.client('bedrock-agentcore-control', region_name=m['region'])
    for key in ('model_gateway_id', 'tool_gateway_id'):
        gateway = control.get_gateway(gatewayIdentifier=m[key])
        if gateway.get('status') != 'READY' or not gateway['gatewayArn'].startswith(f"arn:aws:bedrock-agentcore:{m['region']}:{m['target_account']}:"):
            raise ValueError('Gateway target or readiness mismatch')
        targets = control.list_gateway_targets(gatewayIdentifier=m[key], maxResults=50)
        if not any(t.get('status') == 'READY' for t in targets.get('items', [])):
            raise ValueError('No ready gateway target')
    registry = control.get_registry(registryId=m['registry_id'])
    if not registry['registryArn'].startswith(f"arn:aws:bedrock-agentcore:{m['region']}:{m['target_account']}:"):
        raise ValueError('Registry target mismatch')
    bedrock = session.client('bedrock', region_name=m['region'])
    if m['bedrock_model_id'].startswith(('global.', 'us.', 'eu.', 'apac.')):
        bedrock.get_inference_profile(inferenceProfileIdentifier=m['bedrock_model_id'])
    else:
        bedrock.get_foundation_model(modelIdentifier=m['bedrock_model_id'])
    role = session.client('iam').get_role(RoleName=m['runtime_role_arn'].split('/')[-1])
    if role['Role']['Arn'] != m['runtime_role_arn']:
        raise ValueError('Runtime role mismatch')
    session.client('s3', region_name=m['region']).head_bucket(Bucket=m['artifact_bucket'], ExpectedBucketOwner=m['target_account'])
    # Existence != least privilege, protocol compatibility, model access or paid acceptance.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--read-only', action='store_true')
    args = parser.parse_args()
    try:
        check_sdk()
        if args.manifest:
            manifest = json.loads(args.manifest.read_text())
            validate_manifest(manifest)
        elif args.read_only:
            raise ValueError('An exact-target manifest is required')
        if args.read_only:
            import boto3
            readonly(manifest, boto3.Session(region_name=manifest['region']))
        print(json.dumps({'sdk_shapes': 'verified', 'resource_reads': bool(args.read_only),
                          'live_acceptance': 'NOT RUN', 'execution_ready': False,
                          'remaining': ['IAM policy review', 'gateway protocol/target binding', 'runnable harness artifact', 'SQS lifecycle integration', 'live judge/trace verification']}))
        return 0
    except Exception:
        # SDK exceptions and manifests can contain private target identifiers.
        print('Preflight blocked. Verify approved configuration; no exception details are exported.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
