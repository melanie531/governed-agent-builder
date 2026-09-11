"""Explicit isolated deployment steps; never invoked by git hooks or CI.

State under /tmp is operator-local, not a repository artifact. No user invitations.
"""
import argparse
import hashlib
import json
import mimetypes
from pathlib import Path
import re
import sys
import time
import boto3
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from infra.compute import network_template, runtime_template, ingress_template
from infra.edge import template as edge_template
from infra.identity import template as identity_template
STATE = Path('/tmp/governed-agent-builder-cloud-state.json')
TAGS = [{'Key': k, 'Value': v} for k, v in {'project': 'governed-agent-builder', 'owner': 'melanie531', 'managedBy': 'cloudformation'}.items()]
session = boto3.Session(profile_name='agentic-platform-prod', region_name='us-west-2')
cfn = session.client('cloudformation')


def emit(message):
    print(time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), message, flush=True)


def state():
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save(key, value):
    s = state(); s[key] = value
    STATE.write_text(json.dumps(s, indent=2)); STATE.chmod(0o600)


def verify_account():
    actual = session.client('sts').get_caller_identity()['Account']
    memory = (Path.home()/'.openclaw/workspace/MEMORY.md').read_text()
    expected = re.search(r'## AWS Account — Agentic AI Platform Demo.*?Account ID: `([0-9]+)`', memory, re.S).group(1)
    if actual != expected:
        raise RuntimeError('AWS account mismatch; no mutations permitted')
    emit('STS matched approved account; identifier suppressed')


def deploy(suffix, template, parameters=None):
    name = 'governed-agent-builder-' + suffix
    body = json.dumps(template)
    cfn.validate_template(TemplateBody=body)
    args = dict(StackName=name, TemplateBody=body, Parameters=[{'ParameterKey': k, 'ParameterValue': v} for k, v in (parameters or {}).items()], Tags=TAGS)
    if any(x['Type'].startswith('AWS::IAM::') for x in template['Resources'].values()):
        args['Capabilities'] = ['CAPABILITY_IAM']
    try:
        cfn.describe_stacks(StackName=name)
    except ClientError as e:
        if e.response['Error']['Code'] != 'ValidationError': raise
        cfn.create_stack(**args, OnFailure='DO_NOTHING')
        emit(name + ' CREATE submitted')
    else:
        try:
            cfn.update_stack(**args)
            emit(name + ' UPDATE submitted')
        except ClientError as e:
            if 'No updates are to be performed' not in str(e): raise
            emit(name + ' unchanged')
    previous = ''
    while True:
        stack = cfn.describe_stacks(StackName=name)['Stacks'][0]
        status = stack['StackStatus']
        if status != previous:
            emit(name + ' ' + status); previous = status
        if status in ('CREATE_COMPLETE', 'UPDATE_COMPLETE'):
            outputs = {x['OutputKey']: x['OutputValue'] for x in stack.get('Outputs', [])}
            save(suffix, outputs)
            return outputs
        if not status.endswith('_IN_PROGRESS'):
            for e in cfn.describe_stack_events(StackName=name)['StackEvents']:
                if 'FAILED' in e['ResourceStatus']:
                    reason = re.sub(r'\b\d{12}\b', '[account]', e.get('ResourceStatusReason', ''))
                    emit(e['LogicalResourceId'] + ': ' + reason)
            raise RuntimeError(name + ' ' + status)
        time.sleep(25)


def ssm_command(commands):
    client = session.client('ssm')
    instance = state()['runtime']['InstanceId']
    cid = client.send_command(InstanceIds=[instance], DocumentName='AWS-RunShellScript', Parameters={'commands': commands, 'executionTimeout': ['600']}, TimeoutSeconds=600)['Command']['CommandId']
    emit('SSM command submitted to isolated instance')
    for _ in range(90):
        time.sleep(10)
        try: result = client.get_command_invocation(CommandId=cid, InstanceId=instance)
        except client.exceptions.InvocationDoesNotExist: continue
        if result['Status'] in ('Pending', 'InProgress', 'Delayed'): continue
        # Caller commands contain no auth secrets and must sanitize their output.
        print(re.sub(r'\b\d{12}\b', '[account]', result.get('StandardOutputContent', '')), flush=True)
        if result['Status'] != 'Success':
            print(re.sub(r'\b\d{12}\b', '[account]', result.get('StandardErrorContent', '')), flush=True)
            raise RuntimeError('SSM command ' + result['Status'])
        return
    raise RuntimeError('SSM command timed out')


def main(action):
    verify_account()
    if action == 'network':
        deploy('network', network_template(), {'AvailabilityZone': 'us-west-2a'})
    elif action == 'runtime':
        s = state(); n = s['network']
        archive = Path('/tmp/gab-release.tgz'); digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        key = 'releases/' + digest + '/release.tgz'
        session.client('s3').upload_file(str(archive), n['ArtifactBucket'], key, ExtraArgs={'ServerSideEncryption': 'AES256'})
        emit('Pinned SHA256 release uploaded to isolated private artifact bucket')
        ami = session.client('ssm').get_parameter(Name='/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64')['Parameter']['Value']
        params = {k: n[k] for k in ['SubnetId', 'SecurityGroupId', 'InstanceProfile', 'DataVolumeId', 'ArtifactBucket']}
        params.update(ArtifactKey=key, ArtifactSha256=digest, AmiId=ami)
        deploy('runtime', runtime_template(), params)
        save('artifactSha256', digest)
    elif action == 'edge':
        r = state()['runtime']
        deploy('edge', edge_template(), {'PrivateOriginArn': r['PrivateOriginArn'], 'PrivateOriginHostname': r['PrivateDnsName']})
    elif action == 'ingress':
        n = state()['network']
        groups = session.client('ec2').describe_security_groups(Filters=[{'Name': 'vpc-id', 'Values': [n['VpcId']]}, {'Name': 'group-name', 'Values': ['CloudFront-VPCOrigins-Service-SG']}])['SecurityGroups']
        if len(groups) != 1: raise RuntimeError('Expected exactly one CloudFront managed origin SG in isolated VPC')
        deploy('ingress', ingress_template(), {'OriginSecurityGroup': n['SecurityGroupId'], 'CloudFrontManagedSecurityGroup': groups[0]['GroupId']})
    elif action == 'identity':
        deploy('identity', identity_template(state()['edge']['ApplicationOrigin']))
    elif action == 'frontend':
        s = state(); client = session.client('s3')
        for p in sorted((ROOT/'frontend/dist').rglob('*')):
            if p.is_file():
                client.upload_file(str(p), s['edge']['FrontendBucket'], str(p.relative_to(ROOT/'frontend/dist')), ExtraArgs={'ContentType': mimetypes.guess_type(p.name)[0] or 'application/octet-stream', 'CacheControl': 'no-cache', 'ServerSideEncryption': 'AES256'})
        emit('Built frontend uploaded to new OAC-only bucket')
    elif action == 'configure':
        s = state(); i = s['identity']; url = s['edge']['ApplicationOrigin']; host = url.removeprefix('https://')
        env = '\n'.join(['HOSTED_PREVIEW=1', 'EXECUTION_MODE=local', 'PUBLIC_URL='+url, 'STATE_PATH=/data/state.sqlite', 'COGNITO_REGION=us-west-2', 'COGNITO_USER_POOL_ID='+i['UserPoolId'], 'COGNITO_CLIENT_ID='+i['ClientId'], 'COGNITO_DOMAIN='+i['CognitoDomain']])+'\n'
        nginx = '''server { listen 80 default_server; server_name _; return 444; }
server {
 listen 80;
 server_name HOST;
 client_max_body_size 1m;
 location / {
  proxy_pass http://127.0.0.1:5187;
  proxy_set_header Host HOST;
  proxy_set_header X-Forwarded-For "";
  proxy_set_header X-Forwarded-Proto "";
  proxy_set_header X-Forwarded-Host "";
  proxy_set_header Connection "";
  proxy_read_timeout 30s;
 }
}
'''.replace('HOST', host)
        ssm_command(['set -eu', 'cloud-init status --wait >/dev/null', 'test -f /etc/systemd/system/studio.service', 'mountpoint -q /data',
                     "cat > /etc/studio/runtime.env <<'GABENV'\n"+env+'GABENV', 'chmod 0600 /etc/studio/runtime.env',
                     "cat > /etc/nginx/conf.d/studio.conf <<'GABNGINX'\n"+nginx+'GABNGINX', 'nginx -t',
                     'systemctl restart studio nginx', 'sleep 3', 'systemctl is-active studio nginx',
                     "curl --fail --silent -H 'Host: "+host+"' http://127.0.0.1/studio-config.json", 'echo',
                     'systemctl start studio-backup.service', 'echo RUNTIME_CONFIGURED'])
    elif action == 'release':
        s = state(); archive = Path('/tmp/gab-release.tgz')
        digest = hashlib.sha256(archive.read_bytes()).hexdigest(); key = 'releases/' + digest + '/release.tgz'
        session.client('s3').upload_file(str(archive), s['network']['ArtifactBucket'], key, ExtraArgs={'ServerSideEncryption': 'AES256'})
        ssm_command(['set -eu', 'cloud-init status --wait >/dev/null', 'systemctl stop studio',
            "aws s3 cp 's3://"+s['network']['ArtifactBucket']+'/'+key+"' /opt/studio/release.tgz --region us-west-2 --only-show-errors",
            "printf '%s  %s\\n' '"+digest+"' /opt/studio/release.tgz | sha256sum -c -",
            'tar -xzf /opt/studio/release.tgz -C /opt/studio',
            '/opt/studio/venv/bin/pip install --no-index --find-links=/opt/studio/wheels --require-hashes -r /opt/studio/requirements.txt >/dev/null',
            'chown -R root:root /opt/studio; chmod -R a+rX /opt/studio',
            'test -f /opt/studio/uv.lock', 'systemctl start studio', 'echo RELEASE_VERIFIED'])
        save('artifactSha256', digest)
    elif action == 'bootstrap-status':
        ssm_command(['cloud-init status --wait >/dev/null; echo CLOUD_INIT_EXIT=$?', "grep -E 'BOOTSTRAP_READY|Error:|Failed|No match|ERROR' /var/log/cloud-init-output.log | tail -15", 'mountpoint /data', 'systemctl is-active nginx'])
    else:
        raise ValueError(action)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('action', choices=['network','runtime','edge','ingress','identity','frontend','configure','release','bootstrap-status'])
    main(parser.parse_args().action)
