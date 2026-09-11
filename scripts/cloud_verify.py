"""Live unauthenticated checks. Never print OAuth URLs, cookies, codes or tokens."""
import json
from pathlib import Path
import sys
from urllib.parse import urlparse, parse_qs
import boto3
import httpx

STATE = Path('/tmp/governed-agent-builder-cloud-state.json')


def main():
    s = json.loads(STATE.read_text()); origin = s['edge']['ApplicationOrigin']
    results = []
    def check(name, condition):
        results.append({'test': name, 'passed': bool(condition)})
        print(('PASS ' if condition else 'FAIL ') + name, flush=True)
    with httpx.Client(timeout=30, follow_redirects=False) as c:
        root = c.get(origin+'/')
        check('HTTPS root 200 HTML', root.status_code == 200 and 'text/html' in root.headers.get('content-type', ''))
        check('HSTS and CSP present', 'max-age=' in root.headers.get('strict-transport-security', '') and "frame-ancestors 'none'" in root.headers.get('content-security-policy', ''))
        http = c.get(origin.replace('https://','http://')+'/')
        check('Viewer HTTP redirects to exact HTTPS origin', http.status_code in (301,302,307,308) and http.headers.get('location') == origin+'/')
        config = c.get(origin+'/studio-config.json')
        check('Real hosted configuration', config.status_code == 200 and config.json() == {'hosted': True, 'mode': 'CLOUD-HOSTED DEMO'})
        for path in ['/api','/api/','/api/me','/api/demo/personas','/api/demo/session','/api/agents','/api/build-options','/api/admin/catalog','/api/admin/audit','/api/jobs/synthetic-missing','/api/agents/synthetic-missing/export','/api/unknown']:
            r = c.get(origin+path)
            check('Unauthenticated GET '+path+' = JSON 401', r.status_code == 401 and r.headers.get('content-type','').startswith('application/json') and r.headers.get('cache-control') == 'no-store')
        for path in ['/api/demo/session','/api/agents','/api/agents/synthetic-missing/invoke','/api/agents/synthetic-missing/deploy-test','/api/admin/policy','/api/auth/logout']:
            r = c.post(origin+path, content=b'{malformed', headers={'Content-Type':'application/json','Origin':origin})
            check('Unauthenticated malformed POST '+path+' rejected before parsing', r.status_code == 401)
        r = c.get(origin+'/api/me', headers={'X-Forwarded-User':'admin','X-Forwarded-Proto':'https','X-Forwarded-Host':urlparse(origin).netloc,'Authorization':'Bearer synthetic-invalid'})
        check('Spoofed forwarded identity cannot authenticate', r.status_code == 401)
        r = c.get(origin+'/auth/callback')
        check('Callback without state/code rejected, not SPA', r.status_code == 400 and r.headers.get('content-type','').startswith('application/json'))
        r = c.get(origin+'/auth/login')
        destination = r.headers.get('location',''); parsed = urlparse(destination); query = parse_qs(parsed.query)
        check('Cognito HTTPS code-PKCE exact callback', r.status_code == 302 and destination.startswith(s['identity']['CognitoDomain']+'/oauth2/authorize?') and query.get('redirect_uri') == [origin+'/auth/callback'] and query.get('response_type') == ['code'] and query.get('code_challenge_method') == ['S256'])
        cookie = r.headers.get('set-cookie','').lower()
        check('Login flow cookie Secure HttpOnly SameSite', all(x in cookie for x in ['secure','httponly','samesite=lax','path=/']))
        login = c.get(destination, follow_redirects=True)
        check('Real Cognito managed login reachable', login.status_code == 200 and urlparse(str(login.url)).hostname == parsed.hostname and ('password' in login.text.lower() or 'sign in' in login.text.lower()))
    aws = boto3.Session(profile_name='agentic-platform-prod', region_name='us-west-2')
    ec2 = aws.client('ec2')
    instance = ec2.describe_instances(InstanceIds=[s['runtime']['InstanceId']])['Reservations'][0]['Instances'][0]
    check('Instance running without public IP/DNS', instance['State']['Name']=='running' and not instance.get('PublicIpAddress') and not instance.get('PublicDnsName'))
    sg = ec2.describe_security_groups(GroupIds=[s['network']['SecurityGroupId']])['SecurityGroups'][0]
    groups = ec2.describe_security_groups(Filters=[{'Name':'vpc-id','Values':[s['network']['VpcId']]},{'Name':'group-name','Values':['CloudFront-VPCOrigins-Service-SG']}])['SecurityGroups']
    ingress = sg['IpPermissions']
    check('Origin ingress only TCP80 CloudFront managed SG, no public CIDR/SSH', len(ingress)==1 and ingress[0].get('FromPort')==80 and ingress[0].get('ToPort')==80 and not ingress[0].get('IpRanges') and not ingress[0].get('Ipv6Ranges') and [g['GroupId'] for g in ingress[0].get('UserIdGroupPairs',[])] == [groups[0]['GroupId']])
    v = ec2.describe_volumes(VolumeIds=[s['network']['DataVolumeId']])['Volumes'][0]
    check('Encrypted EBS attached to isolated instance', v['Encrypted'] and len(v['Attachments'])==1 and v['Attachments'][0]['InstanceId']==instance['InstanceId'])
    d = aws.client('cloudfront').get_distribution(Id=s['edge']['DistributionId'])['Distribution']
    check('CloudFront deployed with AWS viewer certificate', d['Status']=='Deployed' and d['DistributionConfig']['ViewerCertificate']['CloudFrontDefaultCertificate'])
    for suffix in ['network','runtime','edge','ingress','identity']:
        stack = aws.client('cloudformation').describe_stacks(StackName='governed-agent-builder-'+suffix)['Stacks'][0]
        check('CloudFormation '+suffix+' healthy', stack['StackStatus'] in ['CREATE_COMPLETE','UPDATE_COMPLETE'])
    output = {'origin':origin,'results':results,'authenticatedJourney':'NOT RUN: invitation recipient/group and interactive Cognito login required','directOriginEvidence':'No public address and only CloudFront managed SG ingress; not an active external packet test'}
    Path('/tmp/gab-live-verification.json').write_text(json.dumps(output,indent=2))
    print(f"Live checks: {sum(x['passed'] for x in results)}/{len(results)}")
    if not all(x['passed'] for x in results): sys.exit(1)


if __name__ == '__main__': main()
