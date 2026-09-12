"""Narrow existing Business release: preserves QA/auth/telemetry ZIP and cloud IDs."""
import base64
import copy
import hashlib
import io
import json
import mimetypes
from pathlib import Path
import subprocess
import time
import urllib.request
import zipfile
from scripts.native_catalog_setup import session, save, ROOT, WORK


def main():
    s, account, stack = session(); cf=s.client('cloudformation'); lam=s.client('lambda')
    name=stack['StackName']; assert stack['StackStatus']=='UPDATE_COMPLETE'
    resources={x['LogicalResourceId']:x['PhysicalResourceId'] for x in cf.list_stack_resources(StackName=name)['StackResourceSummaries']}
    outputs={x['OutputKey']:x['OutputValue'] for x in stack['Outputs']}
    previous=cf.get_template(StackName=name,TemplateStage='Original')['TemplateBody']
    if isinstance(previous,str):previous=json.loads(previous)
    save('rollback-template',previous); save('rollback-stack',stack); save('rollback-resources',resources)
    f=lam.get_function(FunctionName=resources['Business']); env=f['Configuration']['Environment']['Variables']
    raw=urllib.request.urlopen(f['Code']['Location']).read(); (WORK/'Business-rollback.zip').write_bytes(raw)
    save('rollback-business-config',f['Configuration'])
    source=json.loads((WORK/'source-config-v2.json').read_text());registry=source['registries'][0]
    original=zipfile.ZipFile(io.BytesIO(raw)); replacements={
        k:(ROOT/k).read_bytes() for k in ('backend/app.py','backend/live_catalog.py')}
    # Refuse to undo concurrent Builder/auth/telemetry edits; preserve installed bytes.
    for path in ('backend/builder_catalog.py','backend/hosted_auth.py','backend/qa_enrollment.py','foundation_harness/telemetry.py','backend/serverless.py'):
        assert original.read(path)==(ROOT/path).read_bytes(), 'Concurrent deployed source mismatch: '+path
    assert 'backend/qa_enrollments.json' in original.namelist()
    assert any('/agent-registry/' in n for n in original.namelist()),'Packaged native SDK missing'
    replacements['backend/native_catalog_source.json']=json.dumps(source,separators=(',',':')).encode()
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as z:
        for info in original.infolist():z.writestr(copy.copy(info),replacements.pop(info.filename,original.read(info.filename)))
        for path,data in replacements.items():z.writestr(path,data)
    release=buf.getvalue(); patched=zipfile.ZipFile(io.BytesIO(release))
    allowed={'backend/app.py','backend/live_catalog.py','backend/native_catalog_source.json'}
    for path in original.namelist():
        if path not in allowed:assert patched.read(path)==original.read(path)
    (WORK/'Business-release.zip').write_bytes(release)
    sha=hashlib.sha256(release).hexdigest(); key='releases/native-catalog/'+sha+'/lambda.zip'
    params={p['ParameterKey']:p['ParameterValue'] for p in stack['Parameters']};bucket=params['ArtifactBucket']
    s.client('s3').put_object(Bucket=bucket,Key=key,Body=release,ServerSideEncryption='AES256')
    save('release-upload',{'bucket':bucket,'key':key,'sha256':sha})
    proposed=copy.deepcopy(previous); r=proposed['Resources']
    r['Business']['Properties']['Code']={'S3Bucket':bucket,'S3Key':key}
    # Use current resolved variables so drift/approved QA or flags cannot be dropped.
    r['Business']['Properties']['Environment']['Variables']={**env,'CATALOG_MODE':'live','NATIVE_CATALOG_PACKAGED_CONFIG':'1'}
    record_arns=[json.loads((WORK/'record-final.json').read_text())['recordArn']]
    statement=[{'Effect':'Allow','Action':['agent-registry:ListDiscoverableRegistryRecords'],'Resource':registry['registry_arn']},
               {'Effect':'Allow','Action':['agent-registry:GetDiscoverableRegistryRecord'],'Resource':record_arns}]
    policies=r['BusinessRole']['Properties']['Policies']; assert not any(p['PolicyName']=='NativeCatalogDiscovery' for p in policies)
    policies.append({'PolicyName':'NativeCatalogDiscovery','PolicyDocument':{'Version':'2012-10-17','Statement':statement}})
    changed=[k for k in r if r[k]!=previous['Resources'][k]];assert set(changed)=={'Business','BusinessRole'}
    save('proposed-template',proposed)
    cf.validate_template(TemplateBody=json.dumps(proposed))
    cs='native-catalog-'+str(time.time_ns())
    result=cf.create_change_set(StackName=name,ChangeSetName=cs,ChangeSetType='UPDATE',TemplateBody=json.dumps(proposed),
        Capabilities=['CAPABILITY_IAM'],Parameters=[{'ParameterKey':k,'UsePreviousValue':True} for k in params],Tags=stack['Tags'])
    save('change-set-created',result)
    for _ in range(60):
        change=cf.describe_change_set(StackName=name,ChangeSetName=cs)
        if change['Status'] not in ('CREATE_PENDING','CREATE_IN_PROGRESS'):break
        time.sleep(2)
    save('change-set-review',change)
    assert change['Status']=='CREATE_COMPLETE' and not change.get('NextToken')
    for x in change['Changes']:
        item=x['ResourceChange'];assert item['LogicalResourceId'] in ('Business','BusinessRole','BusinessIntegration')
        assert item['Action']=='Modify' and item['Replacement']=='False'
        if item['LogicalResourceId']=='BusinessIntegration':
            assert all(d.get('CausingEntity')=='Business.Arn' and d['Target']['RequiresRecreation']=='Never' for d in item['Details'])
    print('Reviewed changeset: '+json.dumps([{k:x['ResourceChange'].get(k) for k in ('LogicalResourceId','Action','Replacement')} for x in change['Changes']]),flush=True)
    cf.execute_change_set(StackName=name,ChangeSetName=cs);save('change-set-executed',{'name':cs,'time':time.time()})
    for _ in range(60):
        after=cf.describe_stacks(StackName=name)['Stacks'][0]
        if not after['StackStatus'].endswith('IN_PROGRESS'):break
        time.sleep(5)
    save('stack-readback',after);assert after['StackStatus']=='UPDATE_COMPLETE',after['StackStatus']
    assert resources=={x['LogicalResourceId']:x['PhysicalResourceId'] for x in cf.list_stack_resources(StackName=name)['StackResourceSummaries']}
    assert outputs=={x['OutputKey']:x['OutputValue'] for x in after['Outputs']}
    live=lam.get_function(FunctionName=resources['Business'])
    assert live['Configuration']['CodeSha256']==base64.b64encode(hashlib.sha256(release).digest()).decode()
    assert urllib.request.urlopen(live['Code']['Location']).read()==release
    assert live['Configuration']['Environment']['Variables']=={**env,'CATALOG_MODE':'live','NATIVE_CATALOG_PACKAGED_CONFIG':'1'}
    for logical in ('Auth','Authorizer'):
        assert previous['Resources'][logical]==proposed['Resources'][logical]
    save('deployment-readback',{'business_code_sha256':sha,'resource_ids_unchanged':True,'auth_qa_telemetry_preserved':True,'source_config_version':2,'model_gateway':'NotConnected','execution_ready':False,'authenticated_ui_verified':False})
    print('Business deployed; code/config readback verified; all physical IDs preserved',flush=True)
    # Publish only built static assets, index last; retain old versioned S3 objects.
    web=outputs['FrontendBucket']; paths=sorted((ROOT/'frontend/dist').rglob('*'),key=lambda p:p.name=='index.html')
    for path in paths:
        if path.is_file():
            object_key=str(path.relative_to(ROOT/'frontend/dist'))
            response=s.client('s3').put_object(Bucket=web,Key=object_key,Body=path.read_bytes(),ServerSideEncryption='AES256',ContentType=mimetypes.guess_type(path.name)[0] or 'application/octet-stream',CacheControl='no-cache' if path.name=='index.html' else 'public,max-age=31536000,immutable')
            save('frontend-last-write',{'key':object_key,'version':response.get('VersionId')})
    response=s.client('cloudfront').create_invalidation(DistributionId=outputs['DistributionId'],InvalidationBatch={'Paths':{'Quantity':1,'Items':['/*']},'CallerReference':'native-catalog-'+str(time.time_ns())})
    save('frontend-invalidation',{'id':response['Invalidation']['Id']})
    print('Source-status frontend published to existing Studio',flush=True)

if __name__=='__main__':main()
