"""Verified resource facts -> guarded Studio initialization; dry-run by default.

No approval, proof, IAM, flags, Runtime, or artifact writes. A plan is informational,
never authority: apply rebuilds it from AWS and compares its digest before CAS.
"""
import argparse
import copy
import json
import re
from urllib.parse import urlparse

from botocore.exceptions import ClientError
from fastapi import HTTPException
from foundation_harness.config import digest
from backend import foundation_runs as runs
from backend.dynamo_store import DynamoStore, DynamoUnit
from backend.repository import record_key
from .foundation_target import StudioTarget, PROJECT, STACK, PROFILE, REGION, SDK_CONFIG, sanitized

APP = PROJECT + '-serverless-app'
ARTIFACTS = PROJECT + '-serverless-artifacts'
FACT_KEY = 'foundation-initialization'
CONFIG_KEY = 'foundation-deployment'
SCHEMA = 1


def require(value, code):
    if not value:
        raise ValueError(code)


def setting_key(key):
    return {'pk': 'settings', 'sk': record_key('settings', {'key': key})}


def read_setting(table, key):
    """Point read uses the repository's JSON-array sort key and nested body."""
    item = table.get_item(Key=setting_key(key), ConsistentRead=True).get('Item')
    if not item:
        return None
    row = json.loads(item['body'])
    require(row['key'] == key, 'SETTING_KEY_MISMATCH')
    return json.loads(row['body'])


class Resources:
    def __init__(self, session, account, region, studio_stack):
        require(re.fullmatch(r'\d{12}', account) and region == REGION and studio_stack == APP,
                'EXPLICIT_EXISTING_STUDIO_TARGET_REQUIRED')
        self.session, self.account, self.region = session, account, region
        self.studio_stack = studio_stack
        self.stacks, self.inventory = {}, {}

    def client(self, name):
        return self.session.client(name, config=SDK_CONFIG)

    def stack(self, name):
        cf = self.client('cloudformation')
        stack = cf.describe_stacks(StackName=name)['Stacks'][0]
        require(stack['StackId'].split(':')[3:5] == [self.region, self.account]
                and stack['StackStatus'] in ('CREATE_COMPLETE', 'UPDATE_COMPLETE')
                and {t['Key']: t['Value'] for t in stack.get('Tags', [])}.get('project') == PROJECT,
                'STACK_PROVENANCE_MISMATCH')
        resources, kwargs = [], {'StackName': stack['StackId']}
        while True:
            page = cf.list_stack_resources(**kwargs)
            resources.extend(page['StackResourceSummaries'])
            if not page.get('NextToken'):
                break
            kwargs['NextToken'] = page['NextToken']
        self.inventory[name] = {r['LogicalResourceId']: r for r in resources}
        outputs = {o['OutputKey']: o['OutputValue'] for o in stack.get('Outputs', [])}
        self.stacks[name] = {'stack_id': stack['StackId'], 'outputs': outputs,
                            'resources_digest': digest([{k: r.get(k) for k in ('LogicalResourceId',
                                'PhysicalResourceId', 'ResourceType', 'ResourceStatus')}
                                for r in sorted(resources, key=lambda r: r['LogicalResourceId'])])}
        return outputs

    def resource(self, stack, logical, kind):
        resource = self.inventory[stack].get(logical, {})
        require(resource.get('ResourceType') == kind and resource.get('ResourceStatus')
                in ('CREATE_COMPLETE', 'UPDATE_COMPLETE'), 'RESOURCE_PROVENANCE_MISMATCH:' + logical)
        return resource['PhysicalResourceId']

    def role(self, stack, logical, expected=None, service='lambda.amazonaws.com'):
        name = self.resource(stack, logical, 'AWS::IAM::Role')
        role = self.client('iam').get_role(RoleName=name)['Role']
        require(role['Arn'].startswith(f'arn:aws:iam::{self.account}:role/')
                and (expected is None or role['Arn'] == expected), 'ROLE_PROVENANCE_MISMATCH')
        statements = role['AssumeRolePolicyDocument'].get('Statement', [])
        require(any(s.get('Effect') == 'Allow' and s.get('Principal', {}).get('Service') == service
                    and s.get('Action') == 'sts:AssumeRole' for s in statements), 'ROLE_TRUST_REQUIRED')
        if service == 'bedrock-agentcore.amazonaws.com':
            require(any(s.get('Condition', {}).get('StringEquals', {}).get('aws:SourceAccount') == self.account
                        and s.get('Condition', {}).get('ArnLike', {}).get('aws:SourceArn', '').startswith(
                            f'arn:aws:bedrock-agentcore:{self.region}:{self.account}:runtime/')
                        for s in statements if s.get('Effect') == 'Allow'), 'RUNTIME_TRUST_SCOPE_REQUIRED')
        return {'arn': role['Arn'], 'role_id': role['RoleId'],
                'trust_digest': digest(role['AssumeRolePolicyDocument'])}

    def permission(self, role, actions, resource, context=()):
        result = self.client('iam').simulate_principal_policy(
            PolicySourceArn=role, ActionNames=actions, ResourceArns=[resource],
            ContextEntries=list(context))
        rows = result.get('EvaluationResults', [])
        return (not result.get('IsTruncated') and len(rows) == len(actions)
                and {r['EvalActionName'] for r in rows} == set(actions)
                and all(r['EvalDecision'] == 'allowed' and not r.get('MissingContextValues') for r in rows))

    def collect(self):
        # STS FIRST; never consult another account after a mismatch.
        require(self.client('sts').get_caller_identity()['Account'] == self.account, 'STS_ACCOUNT_MISMATCH')
        target = StudioTarget(self.session)
        target.verify()  # independently pins actual existing Studio CloudFront/Cognito
        require(target.account == self.account, 'STUDIO_ACCOUNT_MISMATCH')
        app, artifacts = self.stack(APP), self.stack(ARTIFACTS)
        foundation = self.stack(STACK)
        table = self.resource(APP, 'State', 'AWS::DynamoDB::Table')
        require(table == app['StateTable'], 'STATE_OUTPUT_MISMATCH')
        description = self.client('dynamodb').describe_table(TableName=table)['Table']
        require(description['TableArn'] == f'arn:aws:dynamodb:{self.region}:{self.account}:table/{table}'
                and description['TableStatus'] == 'ACTIVE'
                and {k['AttributeName']: k['KeyType'] for k in description['KeySchema']} == {'pk': 'HASH', 'sk': 'RANGE'}
                and {k['AttributeName']: k['AttributeType'] for k in description['AttributeDefinitions']} == {'pk': 'S', 'sk': 'S'},
                'REPOSITORY_COMPOSITE_KEY_SCHEMA_REQUIRED')
        bucket = self.resource(ARTIFACTS, 'Releases', 'AWS::S3::Bucket')
        require(bucket == artifacts['Bucket'], 'ARTIFACT_OUTPUT_MISMATCH')
        params = self.client('cloudformation').describe_stacks(StackName=APP)['Stacks'][0].get('Parameters', [])
        require({p['ParameterKey']: p.get('ParameterValue') for p in params}.get('ArtifactBucket') == bucket,
                'STUDIO_ARTIFACT_STORE_MISMATCH')
        worker = self.role(APP, 'WorkerRole')
        runtime = self.role(STACK, 'FoundationRole', foundation['FoundationRole'], 'bedrock-agentcore.amazonaws.com')
        function = self.client('lambda').get_function_configuration(FunctionName=app['WorkerFunction'])
        require(function['Role'] == worker['arn'] and app['WorkerFunction'] == self.resource(
            APP, 'Worker', 'AWS::Lambda::Function'), 'WORKER_ROLE_MISMATCH')
        # Do not retain the Lambda environment (it can contain unrelated sensitive values).
        # Reconciliation cannot silently change a currently running live binding.
        for logical in ('Business', 'Worker'):
            name = self.resource(APP, logical, 'AWS::Lambda::Function')
            cfg = function if logical == 'Worker' else self.client('lambda').get_function_configuration(FunctionName=name)
            env = cfg.get('Environment', {}).get('Variables', {})
            require(env.get('FOUNDATION_LIVE_ENABLED', '0') != '1'
                    and env.get('FOUNDATION_PRODUCER_ENABLED', '0') != '1', 'LIVE_FLAGS_MUST_REMAIN_OFF')
        api = self.resource(APP, 'Api', 'AWS::ApiGatewayV2::Api')
        endpoint = f'https://{api}.execute-api.{self.region}.amazonaws.com'
        require(app['ApiEndpoint'].rstrip('/') == endpoint and urlparse(endpoint).scheme == 'https',
                'EXCHANGE_ENDPOINT_MISMATCH')
        route_id = self.resource(APP, 'FoundationExchangeRoute', 'AWS::ApiGatewayV2::Route')
        route = self.client('apigatewayv2').get_route(ApiId=api, RouteId=route_id)
        require(route['RouteKey'] == 'POST /internal/foundation/exchange'
                and route['AuthorizationType'] == 'AWS_IAM', 'IAM_EXCHANGE_ROUTE_REQUIRED')
        exchange = self.resource(APP, 'FoundationExchange', 'AWS::Lambda::Function')
        integration = self.client('apigatewayv2').get_integration(
            ApiId=api, IntegrationId=route['Target'].removeprefix('integrations/'))
        require(integration['IntegrationUri'] == f'arn:aws:lambda:{self.region}:{self.account}:function:{exchange}',
                'EXCHANGE_INTEGRATION_MISMATCH')
        s3 = self.client('s3')
        owner = {'Bucket': bucket, 'ExpectedBucketOwner': self.account}
        require(s3.get_bucket_location(**owner).get('LocationConstraint') == self.region,
                'ARTIFACT_BUCKET_REGION_MISMATCH')
        require(s3.get_bucket_versioning(**owner).get('Status') == 'Enabled'
                and all(s3.get_public_access_block(**owner)['PublicAccessBlockConfiguration'].get(k) is True
                    for k in ('BlockPublicAcls', 'IgnorePublicAcls', 'BlockPublicPolicy', 'RestrictPublicBuckets')),
                'PRIVATE_VERSIONED_ARTIFACT_STORE_REQUIRED')
        require(s3.get_bucket_policy_status(**owner)['PolicyStatus']['IsPublic'] is False,
                'PUBLIC_ARTIFACT_POLICY_DENIED')
        encryption = s3.get_bucket_encryption(**owner)['ServerSideEncryptionConfiguration']
        require(encryption.get('Rules') and all(r['ApplyServerSideEncryptionByDefault']['SSEAlgorithm'] == 'AES256'
                    for r in encryption['Rules']), 'AES256_ARTIFACT_STORE_REQUIRED')
        policy = json.loads(s3.get_bucket_policy(**owner)['Policy'])
        require(any(s.get('Effect') == 'Deny' and s.get('Principal') == '*'
                    and s.get('Action') == 's3:*'
                    and set(s.get('Resource', [])) == {f'arn:aws:s3:::{bucket}', f'arn:aws:s3:::{bucket}/*'}
                    and s.get('Condition', {}).get('Bool', {}).get('aws:SecureTransport') in ('false', False)
                    for s in policy.get('Statement', [])), 'ARTIFACT_TLS_REQUIRED')
        listing = s3.list_objects_v2(**owner, MaxKeys=10)
        inventory_summary = {'sample_count': len(listing.get('Contents', [])),
                             'truncated': listing.get('IsTruncated', False),
                             'sample_bytes': sum(o['Size'] for o in listing.get('Contents', [])),
                             'approval_inferred': False}
        facts = {'schema_version': SCHEMA, 'account': self.account, 'region': self.region,
                 'studio_stack': self.studio_stack, 'stacks': self.stacks, 'table': table,
                 'bucket': bucket, 'worker_role': worker, 'runtime_role': runtime,
                 'exchange_endpoint': endpoint + '/internal/foundation/exchange',
                 'bucket_policy_digest': digest(policy), 'encryption_digest': digest(encryption),
                 'artifact_inventory': inventory_summary}
        issues = []
        # Explicit output contract for a separately provisioned existing VPC. No
        # default VPC discovery, ad-hoc subnet selection, or new resource creation.
        subnets = foundation.get('FoundationSubnets', '').split(',')
        groups = foundation.get('FoundationSecurityGroups', '').split(',')
        network = None
        if all(subnets) and all(groups):
            require(all(re.fullmatch(r'subnet-[a-f0-9]+', s) for s in subnets)
                    and all(re.fullmatch(r'sg-[a-f0-9]+', g) for g in groups), 'NETWORK_OUTPUT_INVALID')
            ec2 = self.client('ec2')
            subnet_rows = ec2.describe_subnets(SubnetIds=subnets)['Subnets']
            group_rows = ec2.describe_security_groups(GroupIds=groups)['SecurityGroups']
            require({s['SubnetId'] for s in subnet_rows} == set(subnets)
                    and {g['GroupId'] for g in group_rows} == set(groups)
                    and len({r['VpcId'] for r in [*subnet_rows, *group_rows]}) == 1
                    and all(s['OwnerId'] == self.account and s['State'] == 'available'
                            and not s['MapPublicIpOnLaunch'] for s in subnet_rows)
                    and all(g['OwnerId'] == self.account for g in group_rows), 'EXISTING_PRIVATE_VPC_REQUIRED')
            network = {'networkMode': 'VPC', 'networkModeConfig': {'subnets': subnets, 'securityGroups': groups}}
            facts['network'] = network
        else:
            issues.append('EXISTING_VPC_OUTPUTS_REQUIRED: FoundationSubnets, FoundationSecurityGroups')
        checks = [
            ('WORKER_PASSROLE', worker['arn'], ['iam:PassRole'], runtime['arn'], [
                {'ContextKeyName': 'iam:PassedToService', 'ContextKeyValues': ['bedrock-agentcore.amazonaws.com'], 'ContextKeyType': 'string'}]),
            ('RUNTIME_EXCHANGE', runtime['arn'], ['execute-api:Invoke'],
             f'arn:aws:execute-api:{self.region}:{self.account}:{api}/$default/POST/internal/foundation/exchange', []),
            ('WORKER_BUCKET_METADATA', worker['arn'], ['s3:GetBucketVersioning', 's3:GetBucketPublicAccessBlock'],
             f'arn:aws:s3:::{bucket}', []),
        ]
        if network:
            checks.append(('WORKER_CREATE_RUNTIME', worker['arn'], ['bedrock-agentcore:CreateAgentRuntime'], '*', [
                {'ContextKeyName': 'bedrock-agentcore:subnets', 'ContextKeyValues': subnets, 'ContextKeyType': 'stringList'},
                {'ContextKeyName': 'bedrock-agentcore:securityGroups', 'ContextKeyValues': groups, 'ContextKeyType': 'stringList'}]))
        permissions = {}
        for name, role, actions, resource, context in checks:
            try:
                permissions[name] = 'SIMULATED_ALLOW' if self.permission(role, actions, resource, context) else 'BLOCKED'
            except ClientError as exc:
                permissions[name] = 'UNVERIFIED:' + exc.response['Error']['Code']
            if permissions[name] != 'SIMULATED_ALLOW':
                issues.append(name + '_PERMISSION_REQUIRED')
        facts['permissions'] = permissions
        config = None if issues else {'account': self.account, 'region': self.region,
            'roles': [runtime['arn']], 'bucket': bucket, 'network': network, 'producer_role': worker['arn']}
        return facts, config, issues


def evidence_report(db, facts, s3):
    """Inspect protected source/bundle/final evidence independently of S3 existence.

    Never import an object/listing/old base startup log as an approval or final PASS.
    Per-definition admission remains the existing worker's responsibility.
    """
    rows = {r['key']: json.loads(r['body']) for r in db.select('settings')}
    categories = ('foundation-source:', 'foundation-policy:', 'foundation-approved:',
                  'foundation-bundle:', 'foundation-artifact:', 'foundation-linux:', 'foundation-base-linux:')
    counts = {p: sum(k.startswith(p) for k in rows) for p in categories}
    issues = [p + 'RECORD_REQUIRED' for p, count in counts.items() if not count]
    objects = []
    from backend.foundation_producer import read_object, validate_base
    from backend.foundation_approval import linux_validation, finalized_artifact
    for key, bundle in rows.items():
        if not key.startswith('foundation-bundle:'):
            continue
        require(bundle['bucket'] == facts['bucket'], 'BUNDLE_BUCKET_MISMATCH')
        require(bundle.get('source_record_digest') == digest(rows.get('foundation-source:' + key.split(':', 1)[1])),
                'BUNDLE_SOURCE_MISMATCH')
        require(bundle.get('artifact_version') not in (None, '', 'null'), 'IMMUTABLE_OBJECT_VERSION_REQUIRED')
        head = s3.head_object(Bucket=facts['bucket'], Key=bundle['artifact_key'],
                             VersionId=bundle['artifact_version'], ExpectedBucketOwner=facts['account'])
        require(head.get('VersionId') == bundle['artifact_version'] and 0 < head['ContentLength'] <= 64 * 1024 * 1024,
                'OBJECT_VERSION_OR_SIZE_MISMATCH')
        blob = read_object(s3, facts['bucket'], bundle['artifact_key'], bundle['artifact_version'], bundle['package_digest'])
        validate_base(db, bundle, blob)
        objects.append({'kind': 'verified_base', 'package_digest': bundle['package_digest'],
                        'artifact_version': bundle['artifact_version']})
    for key, binding in rows.items():
        if key.startswith('foundation-artifact:'):
            approved = rows.get('foundation-approved:' + binding['definition_digest'])
            try:
                require(approved, 'CURRENT_APPROVAL_REQUIRED')
                finalized_artifact(db, approved)
                require(linux_validation(db, binding)['status'] == 'PASS', 'FINAL_BOUND_LINUX_REQUIRED')
            except (ValueError, HTTPException):
                issues.append('CURRENT_FINAL_ARTIFACT_AND_LINUX_REQUIRED')
    return {'record_counts': counts, 'verified_objects': objects,
            'unregistered_s3_objects': 'NOT_INFERRED_FROM_RECORD_COUNTS', 'missing': sorted(set(issues))}


def prepare(table, facts, config, config_issues, s3):
    # DynamoUnit reads strongly consistent pages and the global CAS fence. Do NOT
    # use store.tx for dry-run: its read-only commit is still TransactWriteItems.
    db = DynamoUnit(table)
    old = runs.get(db, CONFIG_KEY)
    previous = runs.get(db, FACT_KEY)
    report = evidence_report(db, facts, s3)
    candidate = {**(old or {}), **(config or {})} if config is not None else None
    # Provenance belongs in its own record: do not contaminate approval/deployment
    # digests or put fake readiness fields into the driver's consumed settings.
    record = copy.deepcopy(facts)
    record['driver_config_digest'] = digest(candidate) if candidate is not None else None
    plan = {'schema_version': SCHEMA, 'facts': record, 'deployment_config': candidate,
            'expected_current_config_digest': digest(old), 'expected_current_facts_digest': digest(previous),
            'configuration_missing': config_issues, 'evidence': report,
            'readiness': 'NOT_READY', 'remaining_execution': [
                'Dedicated immutable package role, exact object IAM and runtime endpoint scope validation',
                'Private VPC reachability and endpoint/security-group review',
                'Current protected source/policy admission and all-service cost/evaluation configuration',
                'Final-bound Linux execution, actual Runtime/endpoint/evaluation E2E acceptance'],
            'writes': [FACT_KEY] + ([CONFIG_KEY] if candidate is not None else [])}
    return plan


def apply(table, plan, expected_plan_digest, reverify):
    """Apply facts/config only; fresh resource reads + global repository CAS.

    No --force, all-settings replacement, approval seeding or proof registration.
    The callable must rebuild a fresh plan, not load caller-authored JSON.
    """
    require(plan['schema_version'] == SCHEMA and digest(plan) == expected_plan_digest, 'PLAN_DIGEST_MISMATCH')
    fresh = reverify()
    require(fresh == plan, 'RESOURCE_OR_CONFIGURATION_CHANGED')
    db = DynamoUnit(table)
    old, previous = runs.get(db, CONFIG_KEY), runs.get(db, FACT_KEY)
    candidate, facts = plan['deployment_config'], plan['facts']
    if previous == facts and (candidate is None or candidate == old):
        return {'status': 'UNCHANGED', 'readiness': 'NOT_READY'}
    require(digest(old) == plan['expected_current_config_digest']
            and digest(previous) == plan['expected_current_facts_digest'], 'CURRENT_CONFIG_CHANGED')
    if previous is not None:
        require(previous.get('schema_version') == SCHEMA, 'UNKNOWN_INITIALIZATION_SCHEMA')
    runs.put(db, FACT_KEY, facts)
    if candidate is not None:
        runs.put(db, CONFIG_KEY, candidate)
    db.commit()  # same revision fence as every application governance transaction
    return {'status': 'INITIALIZED', 'writes': plan['writes'], 'readiness': 'NOT_READY'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-account', required=True)
    parser.add_argument('--region', required=True, choices=[REGION])
    parser.add_argument('--profile', required=True, choices=[PROFILE])
    parser.add_argument('--studio-stack', required=True, choices=[APP])
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--expected-plan-digest')
    args = parser.parse_args(argv)
    require(not args.apply or args.expected_plan_digest, 'APPLY_REQUIRES_CURRENT_PLAN_DIGEST')
    import boto3
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    resources = Resources(session, args.expected_account, args.region, args.studio_stack)
    facts, config, issues = resources.collect()
    store = DynamoStore(facts['table'], session.resource('dynamodb', config=SDK_CONFIG))
    s3 = resources.client('s3')
    plan = prepare(store.table, facts, config, issues, s3)
    if args.apply:
        def reverify():
            fresh, candidate, missing = Resources(session, args.expected_account, args.region, args.studio_stack).collect()
            require(fresh['table'] == store.table.name, 'STATE_TABLE_CHANGED')
            return prepare(store.table, fresh, candidate, missing, s3)
        return apply(store.table, plan, args.expected_plan_digest, reverify)
    return {'mode': 'DRY_RUN', 'plan_digest': digest(plan), 'plan': plan}


if __name__ == '__main__':
    try:
        print(json.dumps(main(), indent=2))
    except Exception as exc:
        print(sanitized(exc))
        raise SystemExit(1) from None
