"""Host-only exact-target ALPR registration and additive infrastructure preparation.

No SQL, secret reads, resource replacement, Runtime update or target deletion.
prepare writes reviewable files; apply submits just the additive app changes;
register verifies deployed AWS state before enabling the backend admission record.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import re

from backend.dynamo_store import DynamoStore
from backend.foundation_runs import get, put
from backend.journey_alpr import require
from backend import journey_alpr_workload as workloads
from foundation_harness.alpr_exchange import CALLER_HEADER, SpecialistExchange
from foundation_harness.config import digest
from infra.journey_alpr import configure
from scripts.deployment_target import DeploymentTarget, target_arguments
from scripts.journey_platform import alpr_target_configuration

# Operator-approved (profile, region) release targets. Fail closed on anything else;
# account binding and STS equality are enforced separately by DeploymentTarget.
APPROVED_TARGETS = {('nvidia', 'us-west-2')}


def read_runtime(control, iam, arn):
    value = control.get_agent_runtime(agentRuntimeId=arn.rsplit('/', 1)[1])
    endpoint = control.get_agent_runtime_endpoint(agentRuntimeId=value['agentRuntimeId'], endpointName='DEFAULT')
    role_name = value['roleArn'].rsplit('/', 1)[1]
    policies = iam.list_role_policies(RoleName=role_name)
    require(not policies.get('IsTruncated'), 'ALPR_ROLE_POLICY_ENUMERATION_DENIED')
    role_policies = {name: iam.get_role_policy(RoleName=role_name, PolicyName=name)['PolicyDocument']
                     for name in policies['PolicyNames']}
    return {'id': value['agentRuntimeId'], 'arn': value['agentRuntimeArn'], 'version': value['agentRuntimeVersion'],
            'role_policies': role_policies,
            'configuration': workloads.configuration(value), 'endpoint_arn': endpoint['agentRuntimeEndpointArn']}


def register(db, settings, record, manifest):
    """Called only after concrete cloud verification; immutable writes are CAS-protected."""
    reference = digest(record)
    require(digest(manifest) == record['manifest_digest'], 'ALPR_MANIFEST_BINDING_DENIED')
    location = json.loads(record['a']['configuration']['environmentVariables']['JOURNEY_MANIFEST'])
    require(location['digest'] == record['manifest_digest'] and manifest['artifact'] == settings['artifact'],
            'ALPR_ARTIFACT_BINDING_DENIED')
    artifact = manifest['artifact']
    expected_artifact = {'codeConfiguration': {'code': {'s3': {
        'bucket': artifact['bucket'], 'prefix': artifact['key'], 'versionId': artifact['version_id']}},
        'runtime': 'PYTHON_3_13', 'entryPoint': ['main.py']}}
    require(record['a']['configuration']['agentRuntimeArtifact'] == expected_artifact,
            'ALPR_FOUNDATION_ARTIFACT_MISMATCH')
    original = get(db, 'journey-manifest:' + manifest['definition_digest'])
    require(original == manifest, 'ALPR_BACKEND_MANIFEST_REQUIRED')
    for row in (record['a'], record['b']):
        key = workloads.PREFIX + 'role:' + row['configuration']['roleArn']
        identity = digest(row)
        require(get(db, key) in (None, identity), 'ALPR_ROLE_ALREADY_BOUND')
        put(db, key, identity)
    require(get(db, workloads.PREFIX + reference) in (None, record), 'ALPR_DEPLOYMENT_IMMUTABLE')
    put(db, workloads.PREFIX + reference, record)
    listing = {key: record[key] for key in ('b', 'gateway_role', 'specialist')}
    listing_key = 'journey-alpr-listing:' + record['specialist']['deployment_digest']
    require(get(db, listing_key) in (None, listing), 'ALPR_LISTING_IMMUTABLE')
    put(db, listing_key, listing)
    binding = {key: record['a'][key] for key in ('id', 'arn', 'version')}
    binding.update(manifest=location, alpr_deployment=reference)
    settings = copy.deepcopy(settings)
    deployments = settings.setdefault('alpr_deployments', {})
    require(deployments.get(record['manifest_digest']) in (None, binding), 'ALPR_MANIFEST_ALREADY_DEPLOYED')
    deployments[record['manifest_digest']] = binding
    put(db, 'journey-platform', settings)
    return reference, settings


def prepare_template(existing, target, record):
    result = copy.deepcopy(existing)
    configure(result['Resources'], account=target['account'], region=target['region'],
              runtimes=[record[key]['arn'] for key in ('a', 'b')],
              roles=[record[key]['configuration']['roleArn'] for key in ('a', 'b')])
    # Narrow additions for backend A invocation; preserve its existing grants.
    policies = result['Resources']['WorkerRole']['Properties']['Policies']
    policy = {'PolicyName': 'ALPRExactRuntimeInvoke', 'PolicyDocument': {'Version': '2012-10-17', 'Statement': [{
        'Effect': 'Allow', 'Action': ['bedrock-agentcore:GetAgentRuntime', 'bedrock-agentcore:GetAgentRuntimeEndpoint',
                                    'bedrock-agentcore:InvokeAgentRuntime'],
        'Resource': sorted([record['a']['arn'], record['a']['endpoint_arn']])}]}}
    for previous in policies:
        if previous['PolicyName'] == policy['PolicyName']:
            values = policy['PolicyDocument']['Statement'][0]['Resource']
            values[:] = sorted(set(values) | set(previous['PolicyDocument']['Statement'][0]['Resource']))
    policies[:] = [p for p in policies if p['PolicyName'] != policy['PolicyName']] + [policy]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'apply', 'register'])
    target_arguments(parser)
    parser.add_argument('--runtime-a', required=True)
    parser.add_argument('--runtime-b', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--channel-evidence', type=Path)
    parser.add_argument('--release-key', required=True, help='Content-addressed Lambda ZIP key in the existing artifact bucket')
    parser.add_argument('--release-version', required=True, help='Immutable S3 object version of the new source release')
    args = parser.parse_args()
    require((args.profile, args.region) in APPROVED_TARGETS, 'ALPR_APPROVED_TARGET_REQUIRED')
    target = DeploymentTarget(args.expected_account, args.profile, args.region, args.state)
    for arn in (args.runtime_a, args.runtime_b):
        require(re.fullmatch(rf'arn:aws:bedrock-agentcore:us-west-2:{args.expected_account}:runtime/[A-Za-z0-9_-]+', arn),
                'ALPR_APPROVED_TARGET_REQUIRED')
    control, iam = target.session.client('bedrock-agentcore-control'), target.session.client('iam')
    a, b = read_runtime(control, iam, args.runtime_a), read_runtime(control, iam, args.runtime_b)
    output = target.state['app']['outputs']
    backend_role = target.session.client('lambda').get_function_configuration(FunctionName=output['WorkerFunction'])['Role']
    gateway = control.get_gateway(gatewayIdentifier=target.state['journeyGateway']['id'])
    require(gateway['authorizerType'] == 'AWS_IAM' and gateway['roleArn'] == target.state['journeyStack']['outputs']['GatewayRole'],
            'ALPR_GATEWAY_BINDING_DENIED')
    location = json.loads(a['configuration']['environmentVariables']['JOURNEY_MANIFEST'])
    response = target.session.client('s3').get_object(Bucket=location['bucket'], Key=location['key'], VersionId=location['version_id'])
    try: raw = response['Body'].read(65537)
    finally: response['Body'].close()
    require(len(raw) <= 65536, 'ALPR_MANIFEST_SIZE_DENIED')
    manifest = json.loads(raw)
    require(digest(manifest) == location['digest'], 'ALPR_MANIFEST_BINDING_DENIED')
    endpoint = output['ApiEndpoint'].rstrip('/') + '/internal/journey/alpr'
    record = {'a': a, 'b': b, 'backend_role': backend_role, 'gateway_role': gateway['roleArn'],
              'manifest_digest': digest(manifest), 'specialist': workloads.specialist_binding(b)}
    args.output.mkdir(parents=True, exist_ok=True)
    # Always emit actual target policies, not guessed ARNs or broad shared roles.
    policies = {'trust': {}, 'ingress': {}, 'exchange': {}}
    route = f"arn:aws:execute-api:us-west-2:{args.expected_account}:{endpoint.split('//')[1].split('.')[0]}/$default/POST/internal/journey/alpr"
    for name, row, caller in [('a', a, backend_role), ('b', b, gateway['roleArn'])]:
        policies['trust'][name] = workloads.trust_policy(row['arn'])
        for arn in (row['arn'], row['endpoint_arn']): policies['ingress'][arn] = workloads.invocation_policy(arn, caller)
        policies['exchange'][row['configuration']['roleArn']] = {'Version': '2012-10-17', 'Statement': [{
            'Effect': 'Allow', 'Action': 'execute-api:Invoke', 'Resource': route}]}
    policies['gateway_invoke_b'] = {'Version': '2012-10-17', 'Statement': [{
        'Effect': 'Allow', 'Action': 'bedrock-agentcore:InvokeAgentRuntime', 'Resource': [b['arn'], b['endpoint_arn']]}]}
    stack_id = target.state['app']['stackId']
    existing = target.cf.get_template(StackName=stack_id, TemplateStage='Original')['TemplateBody']
    if isinstance(existing, str): existing = json.loads(existing)
    template = prepare_template(existing, target.binding, record)
    require(re.fullmatch(r'releases/[a-f0-9]{64}/lambda\.zip', args.release_key)
            and args.release_version not in ('', 'null'), 'ALPR_IMMUTABLE_BACKEND_RELEASE_REQUIRED')
    artifact_bucket = target.state['artifacts']['outputs']['Bucket']
    target.session.client('s3').head_object(Bucket=artifact_bucket, Key=args.release_key, VersionId=args.release_version)
    code = {'S3Bucket': artifact_bucket, 'S3Key': args.release_key, 'S3ObjectVersion': args.release_version}
    for function in ('Business', 'Worker', 'JourneyALPRExchange'):
        template['Resources'][function]['Properties']['Code'] = code
    for name, value in [('app-template', template), ('policies', policies), ('deployment', record),
                        ('gateway-target', alpr_target_configuration(target.binding, b['arn']))]:
        (args.output / (name + '.json')).write_text(json.dumps(value, indent=2))
    if args.action == 'apply':
        # Do not submit a replacement template or change the artifact release.
        request = {'StackName': stack_id, 'TemplateBody': json.dumps(template), 'Capabilities': ['CAPABILITY_IAM'],
                   'Parameters': [{'ParameterKey': k, 'UsePreviousValue': True} for k in existing.get('Parameters', {})]}
        target.cf.update_stack(**request)
        print('Additive exchange update submitted. Run register only after the stack is stable.')
    elif args.action == 'register':
        require(args.channel_evidence and args.channel_evidence.is_file(), 'ALPR_HOST_FORWARDING_EVIDENCE_REQUIRED')
        env = b['configuration']['environmentVariables']
        config = json.loads(env['ALPR_LIVE_CONFIG'])
        SpecialistExchange(target.session, config)  # Validates the supported, pinned channel; no I/O.
        require(config['channel_evidence_digest'] == hashlib.sha256(args.channel_evidence.read_bytes()).hexdigest()
                and config['endpoint'] == endpoint and a['configuration']['environmentVariables'].get('JOURNEY_ALPR_ENDPOINT') == endpoint
                and all(config[k] == record['specialist'][k] for k in ('runtime_arn', 'runtime_version', 'deployment_digest')),
                'ALPR_EXCHANGE_CONFIGURATION_DENIED')
        require(env.get('ALPR_VIEW_SOURCE') == 'live' and env.get('AWS_REGION') == 'us-west-2'
                and env.get('ALPR_SNOWFLAKE_SSM_PREFIX') == '/governed-agent-builder/alpr'
                and 'CALLER_SCOPE_PROFILE' not in env
                and not any(env.get(k) for k in ('ALPR_SNOWFLAKE_ACCOUNT', 'ALPR_SNOWFLAKE_USER',
                                                'ALPR_SNOWFLAKE_KEY_PATH', 'ALPR_SNOWFLAKE_KEY_SSM_PARAM'))
                and b['configuration'].get('requestHeaderConfiguration') == {'requestHeaderAllowlist': [CALLER_HEADER]},
                'ALPR_LIVE_CONFIGURATION_REQUIRED')
        workloads.verify(control, iam, record)
        repository = DynamoStore(output['StateTable'], target.session.resource('dynamodb'))
        with repository.tx() as db:
            reference, settings = register(db, get(db, 'journey-platform'), record, manifest)
        target.save('journeyPlatform', settings)
        target.save('journeyALPR', {'deployment_ref': reference, 'runtime_a': a['arn'], 'runtime_b': b['arn']})
        print('Registered immutable ALPR deployment:', reference)


if __name__ == '__main__':
    main()
