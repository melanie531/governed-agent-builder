"""Entirely offline synthetic evidence; these tests do not execute Linux ARM64."""
import copy
import pytest
from fastapi import HTTPException
from backend.foundation_approval import (FinalizeFoundation, finalize_artifact, finalized_artifact,
    register, compile_approval, linux_validation)
from backend import foundation_runs as runs
from tests.test_foundation_approval import inputs, PLATFORM, ADMIN, OWNER
from tests.conftest import login, create
from tests.test_serverless import cloud, sign_in


def prepare(store, definition):
    source, review = inputs(definition)
    owner = {**OWNER, 'id': definition['owner']}
    with store.tx() as db:
        register(db, ADMIN, source, PLATFORM)
        approved = compile_approval(db, ADMIN, review, owner, PLATFORM)
    data = FinalizeFoundation(agent_id=definition['agent_id'], version=1,
        definition_digest=definition['digest'], approval_revision=1, package_digest='a'*64,
        artifact_version='synthetic-object-version', request_id='synthetic-finalization')
    return owner, approved, data


def verified(approved, data, settings):
    return 'releases/'+data.package_digest+'/foundation.zip'


def test_finalization_idempotent_and_jobs_fail_missing_linux(app,client,payload):
    login(client); definition = create(client,payload)
    owner, approved, data = prepare(app.state.store,definition)
    calls = []
    def verify(*args): calls.append(1); return verified(*args)
    with app.state.store.tx() as db:
        result = finalize_artifact(db,ADMIN,data,owner,PLATFORM,verifier=verify)
    with app.state.store.tx() as db:
        assert finalize_artifact(db,ADMIN,data,owner,PLATFORM,verifier=verify) == result
        assert runs.get(db,'foundation-approved:'+definition['digest']) == approved
        with pytest.raises(HTTPException,match='LINUX_EXECUTION_NOT_READY'):
            finalized_artifact(db,approved)
    assert calls == [1] and result['status'] == 'NOT_READY'
    assert result['linux_validation']['target'] == 'linux-arm64-python3.13'


@pytest.mark.parametrize('bad', ['role','workspace','owner','grant','epoch','policy','source',
    'catalog','receipt','manifest','expired','revision','version','package','object-version','actor','request'])
def test_finalization_stale_or_conflicting_replay(app,client,payload,bad):
    login(client); definition=create(client,payload)
    owner, approved, data=prepare(app.state.store,definition)
    with app.state.store.tx() as db:
        finalize_artifact(db,ADMIN,data,owner,PLATFORM,verifier=verified)
    with app.state.store.tx() as db:
        actor=copy.deepcopy(ADMIN)
        if bad=='role': actor['role']='business'
        if bad=='workspace': actor['workspace']='research'
        if bad=='owner': owner['workspace']='other'
        if bad=='actor': actor['id']='other-admin'
        if bad=='grant': db.delete('grants',where=[('persona','=',owner['id'])])
        if bad=='epoch': runs.put(db,'foundation-epoch',1)
        if bad=='policy': runs.put(db,'policy',{'version':2})
        if bad in ('source','catalog'):
            source=runs.get(db,'foundation-source:research')
            source['revision' if bad=='source' else 'catalog']=2 if bad=='source' else {}
            runs.put(db,'foundation-source:research',source)
        if bad=='receipt': runs.put(db,'foundation-review:'+approved['receipt']['request_id'],{})
        if bad in ('manifest','expired'):
            approved['manifest_digest' if bad=='manifest' else 'expires_at']='f'*64 if bad=='manifest' else 0
            runs.put(db,'foundation-approved:'+definition['digest'],approved)
        for case,field,value in [('revision','approval_revision',2),('version','version',2),
            ('package','package_digest','f'*64),('object-version','artifact_version','other'),
            ('request','request_id','different-request')]:
            if bad==case: data=data.model_copy(update={field:value})
        with pytest.raises(HTTPException): finalize_artifact(db,actor,data,owner,PLATFORM,verifier=verified)


def test_readback_failure_has_no_partial_binding(app,client,payload):
    login(client); definition=create(client,payload)
    owner,approved,data=prepare(app.state.store,definition)
    def reject(*args): raise ValueError('STATIC_PACKAGE_INVALID')
    with pytest.raises(ValueError):
        with app.state.store.tx() as db:
            finalize_artifact(db,ADMIN,data,owner,PLATFORM,verifier=reject)
    with app.state.store.tx() as db:
        assert runs.get(db,'foundation-artifact:'+definition['digest']) is None
        assert runs.get(db,'foundation-finalize:'+data.request_id) is None


def test_finalization_cas_race(cloud,payload):
    from backend.dynamo_store import DynamoUnit
    app,client,_=cloud;sign_in(cloud);definition=create(client,payload)
    owner,approved,data=prepare(app.state.store,definition)
    a,b=DynamoUnit(app.state.store.table),DynamoUnit(app.state.store.table)
    finalize_artifact(a,ADMIN,data,owner,PLATFORM,verifier=verified)
    finalize_artifact(b,ADMIN,data,owner,PLATFORM,verifier=verified)
    a.commit()
    with pytest.raises(HTTPException,match='Concurrent governance'): b.commit()


def test_actual_api_auth_and_extra_field_denial(cloud,payload,monkeypatch):
    app,client,_=cloud;sign_in(cloud);definition=create(client,payload)
    owner,approved,data=prepare(app.state.store,definition)
    monkeypatch.setattr('backend.app.platform_metadata',lambda *a:PLATFORM)
    assert client.post('/api/admin/foundation-artifacts/finalize',json=data.model_dump()).status_code==403
    sign_in(cloud,subject='synthetic-reviewer',group='studio-admin')
    for field,value in [('bucket','arbitrary'),('role','admin'),('ready',True),('linux_validation','PASS')]:
        assert client.post('/api/admin/foundation-artifacts/finalize',json={**data.model_dump(),field:value}).status_code==422
    # Exercise real route/service/CAS, replace only external static S3/build I/O.
    original=finalize_artifact
    monkeypatch.setattr('backend.app.finalize_artifact',lambda *a:original(*a,verifier=verified))
    response=client.post('/api/admin/foundation-artifacts/finalize',json=data.model_dump())
    assert response.status_code==200,response.text
    assert response.json()['status']=='NOT_READY'


@pytest.mark.parametrize('field', ['package_digest','manifest_digest','admission_digest',
    'artifact_source_digest','artifact_version','target','execution','validator_identity','entrypoint_passed'])
def test_linux_receipt_exact_binding(app,client,payload,field):
    login(client);definition=create(client,payload)
    owner,approved,data=prepare(app.state.store,definition)
    with app.state.store.tx() as db:
        binding=finalize_artifact(db,ADMIN,data,owner,PLATFORM,verifier=verified)['binding']
        proof={**binding,'status':'PASS','execution':'ACTUAL_LINUX','validator_identity':'synthetic-only',
               'evidence_digest':'b'*64,'entrypoint_passed':True}
        runs.put(db,'foundation-linux:'+data.package_digest,proof)
        assert finalized_artifact(db,approved)['package_digest']==data.package_digest
        proof[field]=False if field=='entrypoint_passed' else ''
        runs.put(db,'foundation-linux:'+data.package_digest,proof)
        assert linux_validation(db,binding)['status']=='UNVERIFIED'


@pytest.mark.parametrize('bad', [None, 'bytes', 'version', 'source-only', 'public', 'unversioned', 'null-version', 'rebuild'])
def test_complete_static_artifact_verifier(app,client,payload,tmp_path,monkeypatch,bad):
    """Real ZIP reader and reconstruction comparison, synthetic SDK/dependency bytes."""
    import io
    import json
    import hashlib
    import zipfile
    from types import SimpleNamespace
    from backend.foundation_approval import verify_final_artifact
    from scripts.package_foundation import package, save_config
    login(client);definition=create(client,payload)
    owner,approved,data=prepare(app.state.store,definition)
    deps={'bedrock_agentcore/runtime/app.py':b'# synthetic',
          'opentelemetry/sdk/trace/__init__.py':b'# synthetic'}
    monkeypatch.setattr('scripts.package_foundation.dependency_files',lambda *a:deps)
    monkeypatch.setattr('scripts.verify_package_admission.verify_package_admission',lambda *a,**k:approved['admission'])
    monkeypatch.setattr('subprocess.run',lambda *a,**k:None)
    saved=save_config(approved['config'],tmp_path/'config')
    package(saved,tmp_path/'complete.zip',admission=approved['admission'],approved=approved,dependencies=tmp_path)
    blob=(tmp_path/'complete.zip').read_bytes()
    if bad=='source-only':
        buf=io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(blob)) as source, zipfile.ZipFile(buf,'w') as dest:
            for name in source.namelist():
                if not name.startswith('bedrock_agentcore/'):dest.writestr(name,source.read(name))
        blob=buf.getvalue()
    data=data.model_copy(update={'package_digest':hashlib.sha256(blob).hexdigest()})
    if bad=='null-version':data=data.model_copy(update={'artifact_version':'null'})
    calls=[]
    class S3:
        def get_bucket_versioning(self,**kwargs):return {'Status':'Suspended' if bad=='unversioned' else 'Enabled'}
        def get_public_access_block(self,**kwargs):
            return {'PublicAccessBlockConfiguration':dict.fromkeys(
                ['BlockPublicAcls','IgnorePublicAcls','BlockPublicPolicy','RestrictPublicBuckets'],bad!='public')}
        def get_object(self,**kwargs):
            calls.append(kwargs)
            return {'VersionId':'other' if bad=='version' else data.artifact_version,
                    'Body':io.BytesIO(b'wrong' if bad=='bytes' else blob)}
    monkeypatch.setattr('boto3.Session',lambda **k:SimpleNamespace(client=lambda *a,**k:S3()))
    if bad=='rebuild':deps['boto3/new.py']=b'# tampered rebuild'
    settings={'bucket':'synthetic-private-existing','roles':[approved['role']],
              'network':{'networkMode':'VPC'},'region':'us-west-2'}
    if bad:
        from foundation_harness.context import Denied
        with pytest.raises((HTTPException,Denied)):verify_final_artifact(approved,data,settings)
    else:
        assert verify_final_artifact(approved,data,settings)=='releases/'+data.package_digest+'/foundation.zip'
        assert calls==[{'Bucket':settings['bucket'],'Key':'releases/'+data.package_digest+'/foundation.zip',
                       'VersionId':data.artifact_version}]


def test_governance_change_during_verification_cas_denied(cloud,payload):
    from backend.dynamo_store import DynamoUnit
    app,client,_=cloud;sign_in(cloud);definition=create(client,payload)
    owner,approved,data=prepare(app.state.store,definition)
    pending=DynamoUnit(app.state.store.table)
    def revoke(*args):
        with app.state.store.tx() as db:runs.put(db,'foundation-epoch',1)
        return verified(*args)
    finalize_artifact(pending,ADMIN,data,owner,PLATFORM,verifier=revoke)
    with pytest.raises(HTTPException):pending.commit()
    with app.state.store.tx() as db:assert runs.get(db,'foundation-artifact:'+definition['digest']) is None


def test_finalized_approval_cannot_be_replaced(app,client,payload):
    login(client);definition=create(client,payload)
    owner,approved,data=prepare(app.state.store,definition)
    _,review=inputs(definition)
    review=review.model_copy(update={'expected_revision':1,'request_id':'replacement-review'})
    with app.state.store.tx() as db:
        finalize_artifact(db,ADMIN,data,owner,PLATFORM,verifier=verified)
        with pytest.raises(HTTPException,match='FINALIZED_APPROVAL_IMMUTABLE'):
            compile_approval(db,ADMIN,review,owner,PLATFORM)


def test_live_enabled_submission_requires_actual_linux(app,client,payload):
    from types import SimpleNamespace
    from backend.foundation_jobs import FoundationJobs
    login(client);definition=create(client,payload)
    owner,approved,data=prepare(app.state.store,definition)
    with app.state.store.tx() as db:
        finalize_artifact(db,ADMIN,data,owner,PLATFORM,verifier=verified)
        jobs=FoundationJobs(None,None,SimpleNamespace(),enabled=True)
        with pytest.raises(HTTPException,match='LINUX_EXECUTION_NOT_READY'):
            jobs.approve_request(db,definition,owner)


def test_changed_deployment_or_source_invalidates_binding(app,client,payload):
    login(client);definition=create(client,payload)
    owner,approved,data=prepare(app.state.store,definition)
    with app.state.store.tx() as db:
        finalize_artifact(db,ADMIN,data,owner,PLATFORM,verifier=verified)
        runs.put(db,'foundation-deployment',{'bucket':'different'})
        with pytest.raises(HTTPException,match='CURRENT_ARTIFACT_DEPLOYMENT_REQUIRED'):
            finalized_artifact(db,approved)
        runs.put(db,'foundation-deployment',None)
        runs.put(db,'foundation-source:research',{})
        with pytest.raises(HTTPException,match='CURRENT_REGISTERED_ARTIFACT_SOURCE_REQUIRED'):
            finalized_artifact(db,approved)
