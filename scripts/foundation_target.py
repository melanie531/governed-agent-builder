"""Current Studio binding, metadata only. No identity/user/secret mutations."""
import re

import boto3
from botocore.config import Config

PROFILE = 'agentic-platform-prod'
REGION = 'us-west-2'
DOMAIN = 'de32ssfw7gsad.cloudfront.net'
PROJECT = 'governed-agent-builder'
STACK = PROJECT + '-foundation-m0'
SDK_CONFIG = Config(retries={'total_max_attempts': 1}, connect_timeout=5, read_timeout=10)


def sanitized(error):
    """Only operator diagnostics; never pass arbitrary payloads or headers here."""
    if hasattr(error, 'response') and 'Error' in error.response:
        body = error.response['Error']
        text = f"{body['Code']}: {body.get('Message', '')}"
    else:
        text = f'{type(error).__name__}: {error}'
    text = re.sub(r'\b\d{12}\b', '[ACCOUNT]', text)
    text = re.sub(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b', '[ACCESS_KEY]', text)
    text = re.sub(r'(?i)(https?://[^\s?"]+)\?[^\s"]+', r'\1?[REDACTED]', text)
    return text


class StudioTarget:
    """Account is compared internally with CURRENT site-owned resources.

    No account identifiers are persisted, printed or accepted as a CLI override.
    Call verify again immediately before each cloud mutation.
    """
    def __init__(self, session=None):
        self.session = session or boto3.Session(profile_name=PROFILE, region_name=REGION)
        self.account = None

    def client(self, name):
        return self.session.client(name, config=SDK_CONFIG)

    def verify(self):
        account = self.client('sts').get_caller_identity()['Account']
        if not re.fullmatch(r'\d{12}', account):
            raise RuntimeError('Invalid STS target')
        cf = self.client('cloudformation')
        stacks = {}
        for suffix in ('artifacts', 'app'):
            name = PROJECT + '-serverless-' + suffix
            stack = cf.describe_stacks(StackName=name)['Stacks'][0]
            arn = stack['StackId'].split(':')
            tags = {x['Key']: x['Value'] for x in stack.get('Tags', [])}
            if (arn[3:5] != [REGION, account]
                    or stack['StackStatus'] not in ('CREATE_COMPLETE', 'UPDATE_COMPLETE')
                    or tags.get('project') != PROJECT):
                raise RuntimeError('Current Studio stack target/stability/ownership mismatch')
            stacks[suffix] = stack
        response = cf.list_stack_resources(StackName=PROJECT + '-serverless-app')
        if response.get('NextToken'):
            raise RuntimeError('Studio resource metadata pagination requires review')
        resources = response['StackResourceSummaries']

        def one(kind):
            matches = [x['PhysicalResourceId'] for x in resources if x['ResourceType'] == kind]
            if len(matches) != 1:
                raise RuntimeError('Current Studio resource binding ambiguous')
            return matches[0]

        dist = self.client('cloudfront').get_distribution(
            Id=one('AWS::CloudFront::Distribution'))['Distribution']
        if (dist['DomainName'] != DOMAIN or dist['ARN'].split(':')[4] != account
                or dist['Status'] != 'Deployed' or not dist['DistributionConfig']['Enabled']):
            raise RuntimeError('Current Studio CloudFront mismatch')
        pool = self.client('cognito-idp').describe_user_pool(
            UserPoolId=one('AWS::Cognito::UserPool'))['UserPool']
        if pool['Arn'].split(':')[3:5] != [REGION, account]:
            raise RuntimeError('Current Studio Cognito ARN mismatch')
        if self.account is not None and self.account != account:
            raise RuntimeError('STS identity changed during operation')
        self.account = account
        return {'target_gate': 'MATCH', 'profile': PROFILE, 'region': REGION,
                'studio': DOMAIN, 'identity_resources': 'PRESERVED'}


if __name__ == '__main__':
    import json
    try:
        print(json.dumps(StudioTarget().verify()))
    except Exception as exc:
        print(sanitized(exc))
        raise SystemExit(1) from None
