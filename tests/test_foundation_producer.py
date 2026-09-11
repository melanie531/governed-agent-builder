"""Synthetic bytes/SDK/principals only, NOT Linux or live deployment evidence."""
import hashlib
import io
import json
import zipfile
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient
from backend.app import create_app
from backend import foundation_runs as runs
from backend.foundation_jobs import ArtifactReadback
from backend.foundation_producer import FoundationProducer, TARGET, compose
from foundation_harness.config import digest
from scripts.package_foundation import ROOT, SOURCES
from tests.conftest import create
from tests.test_serverless import cloud, sign_in
from tests.test_foundation_wiring import install
from tests.test_runtime_deployment import ROLE, ACCOUNT


class S3:
    def __init__(self):
        self.objects = {}; self.puts = 0; self.bad = None
    def get_bucket_versioning(self, **kw): return {'Status': 'Enabled'}
    def get_public_access_block(self, **kw):
        return {'PublicAccessBlockConfiguration': dict.fromkeys(
            ['BlockPublicAcls', 'IgnorePublicAcls', 'BlockPublicPolicy', 'RestrictPublicBuckets'], True)}
    def put_object(self, **kw):
        assert kw['IfNoneMatch'] == '*' and kw['ServerSideEncryption'] == 'AES256'
        if kw['Key'] in self.objects:
            raise ClientError({'Error': {'Code': 'PreconditionFailed'}}, 'PutObject')
        self.puts += 1; self.objects[kw['Key']] = kw['Body']
        return {'VersionId': 'synthetic-version'}
    def head_object(self, **kw): return {'VersionId': 'synthetic-version'}
    def get_object(self, **kw):
        data = self.objects[kw['Key']]
        final = kw['Key'].startswith('releases/')
        return {'VersionId': 'wrong' if final and self.bad == 'version' else 'synthetic-version',
                'Body': io.BytesIO(b'corrupt' if final and self.bad == 'bytes' else data)}


def setup_producer(cloud, payload, tmp_path):
    old, client, _ = cloud; sign_in(cloud); payload['dataset'] = payload['dataset'][:1]
    definition = create(client, payload)
    service, control, runtime = install(old.state.store, definition, tmp_path, evidence=True)
    s3 = S3()
    files = {n: (ROOT / n).read_bytes() for n in SOURCES}
    files.update({n: b'# synthetic dependency, not executable evidence' for n in
        ['main.py', 'bedrock_agentcore/runtime/app.py', 'pydantic_core/__init__.py',
         'opentelemetry/sdk/trace/__init__.py', 'boto3/__init__.py']})
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as z:
        for n, b in files.items(): z.writestr(n, b)
    blob = output.getvalue(); sha = hashlib.sha256(blob).hexdigest()
    s3.objects['approved/base.zip'] = blob
    with old.state.store.tx() as db:
        db.delete('settings', where=[('key', '=', 'foundation-artifact:' + definition['digest'])])
        settings = runs.get(db, 'foundation-deployment')
        settings.update(bucket='synthetic-artifacts', roles=[ROLE], producer_role=ROLE)
        runs.put(db, 'foundation-deployment', settings)
        source = runs.get(db, 'foundation-source:research')
        base = {'provenance': 'platform-foundation-bundle', 'approver': 'synthetic-platform-reviewer',
            'bucket': settings['bucket'], 'artifact_key': 'approved/base.zip', 'artifact_version': 'synthetic-version',
            'package_digest': sha, 'artifact_source_digest': source['config']['foundation']['digest'],
            'source_record_digest': digest(source), 'target': TARGET,
            'dependency_lock_digest': hashlib.sha256(files['runtime/custom_foundation/requirements.lock']).hexdigest(),
            'files_digest': digest({n: hashlib.sha256(b).hexdigest() for n, b in files.items()})}
        runs.put(db, 'foundation-bundle:research', base)
        runs.put(db, 'foundation-base-linux:' + sha, {**base, 'execution': 'ACTUAL_LINUX', 'status': 'PASS',
            'entrypoint_passed': True, 'validator_identity': 'synthetic-validator', 'evidence_digest': 'f'*64})
    service.producer = FoundationProducer(s3, lambda: f'arn:aws:sts::{ACCOUNT}:assumed-role/synthetic-runtime/session')
    service.artifact_reader = ArtifactReadback(s3, 'synthetic-artifacts')
    app = create_app(repository=old.state.store, worker_enabled=False, foundation_jobs=service)
    app.state.hosted_auth.keys = old.state.hosted_auth.keys
    return app, client, definition, service, control, runtime, s3


def deploy(app, client, definition):
    with TestClient(app, base_url=str(client.base_url), cookies=client.cookies, headers=client.headers) as c:
        result = c.post(f"/api/agents/{definition['agent_id']}/deploy-test", json={
            'version': 1, 'idempotency_key': 'synthetic-producer', 'execution_mode': 'live'})
        assert result.status_code == 202, result.text
        assert result.json()['stage'] == 'WAIT_ARTIFACT'
        return result.json()['job_id']


def test_real_producer_api_to_runtime_queue_and_retry(cloud, payload, tmp_path, monkeypatch):
    app, client, definition, service, control, runtime, s3 = setup_producer(cloud, payload, tmp_path)
    monkeypatch.setattr('backend.app.compile_approval', lambda *a: pytest.fail('human domain review'))
    monkeypatch.setattr('subprocess.run', lambda *a, **k: pytest.fail('no dependency installer'))
    job = deploy(app, client, definition)
    service.step(app.state.store, job)
    with app.state.store.tx() as db:
        binding = runs.get(db, 'foundation-artifact:' + definition['digest'])
        assert binding['actor'] == ROLE
        assert runs.get(db, 'foundation-linux:' + binding['package_digest']) is None
        assert db.select('jobs', where=[('id', '=', job)]).fetchone()['stage'] == 'VALIDATING'
        assert runs.get(db, 'foundation-run:' + job)['manifest_digest'] == binding['manifest_digest']
    result = service.producer.produce(app.state.store, job)
    assert result['binding'] == binding and s3.puts == 1
    assert result['linux_validation']['status'] == 'UNVERIFIED'
    service.step(app.state.store, job)
    assert control.calls[0][0] == 'CreateAgentRuntime'
    assert control.calls[0][1]['agentRuntimeArtifact']['codeConfiguration']['code']['s3']['versionId'] == 'synthetic-version'
    for _ in range(4): service.step(app.state.store, job)
    with app.state.store.tx() as db:
        # Synthetic successful response/eval cannot make inherited baseline proof
        # count as actual execution of the final package.
        assert db.select('jobs', where=[('id', '=', job)]).fetchone()['stage'] == 'BLOCKED'


@pytest.mark.parametrize('bad', ['owner', 'grant', 'epoch', 'source', 'receipt', 'expired', 'session',
    'worker', 'version', 'bytes', 'base-proof', 'lock', 'files', 'base-version'])
def test_producer_fails_closed(cloud, payload, tmp_path, bad):
    app, client, definition, service, control, runtime, s3 = setup_producer(cloud, payload, tmp_path)
    job = deploy(app, client, definition)
    with app.state.store.tx() as db:
        if bad == 'owner': db.update('agents', {'owner': 'wrong-user'}, where=[('id', '=', definition['agent_id'])])
        if bad == 'grant': db.delete('grants', where=[('persona', '=', definition['owner'])])
        if bad == 'epoch': runs.put(db, 'foundation-epoch', 99)
        if bad == 'source':
            source = runs.get(db, 'foundation-source:research'); source['revision'] = 99
            runs.put(db, 'foundation-source:research', source)
        if bad == 'receipt':
            a = runs.get(db, 'foundation-approved:' + definition['digest'])
            runs.put(db, 'domain-admission:' + a['receipt']['id'], {})
        if bad == 'expired':
            a = runs.get(db, 'domain-authority:' + definition['digest']); a['expires_at'] = 0
            runs.put(db, 'domain-authority:' + definition['digest'], a)
        if bad == 'session': db.delete('hosted_sessions')
        if bad in ('base-proof', 'lock', 'files', 'base-version'):
            b = runs.get(db, 'foundation-bundle:research')
            if bad == 'base-proof': runs.put(db, 'foundation-base-linux:' + b['package_digest'], {'status': 'PASS'})
            else:
                b[{'lock': 'dependency_lock_digest', 'files': 'files_digest', 'base-version': 'artifact_version'}[bad]] = 'wrong'
                runs.put(db, 'foundation-bundle:research', b)
    if bad == 'worker': service.producer.principal_reader = lambda: 'forged'
    if bad in ('version', 'bytes'): s3.bad = bad
    with pytest.raises(Exception): service.producer.produce(app.state.store, job)
    with app.state.store.tx() as db:
        assert runs.get(db, 'foundation-artifact:' + definition['digest']) is None
        assert runs.get(db, 'foundation-run:' + job) is None
    assert not control.calls


def test_revocation_during_upload_cas_cannot_finalize(cloud, payload, tmp_path):
    app, client, definition, service, control, runtime, s3 = setup_producer(cloud, payload, tmp_path)
    job = deploy(app, client, definition)
    put = s3.put_object
    def revoke(**kw):
        result = put(**kw)
        with app.state.store.tx() as db: runs.put(db, 'foundation-epoch', 99)
        return result
    s3.put_object = revoke
    with pytest.raises(Exception): service.producer.produce(app.state.store, job)
    with app.state.store.tx() as db:
        assert runs.get(db, 'foundation-artifact:' + definition['digest']) is None
    assert not control.calls


def test_producer_config_is_explicit_and_not_http():
    from infra.serverless import template
    default = template()['Resources']
    assert 'FOUNDATION_PRODUCER_ENABLED' not in default['Worker']['Properties']['Environment']['Variables']
    explicit = template(foundation_producer={'bucket': 'synthetic-artifacts', 'base_key': 'approved/base.zip',
        'base_version': 'synthetic-version', 'producer_role': ROLE})['Resources']
    assert explicit['Worker']['Properties']['Environment']['Variables']['FOUNDATION_PRODUCER_ENABLED'] == '1'
    assert 'FOUNDATION_PRODUCER_ENABLED' not in explicit['Business']['Properties']['Environment']['Variables']
    assert 'FOUNDATION_LIVE_ENABLED' not in explicit['Worker']['Properties']['Environment']['Variables']
    for bad in ('../base.zip', 'releases/arbitrary.zip', 'approved/../base.zip'):
        with pytest.raises(ValueError):
            template(foundation_producer={'bucket': 'synthetic-artifacts', 'base_key': bad,
                'base_version': 'synthetic-version', 'producer_role': ROLE})


def test_authority_not_renewed_by_worker(cloud, payload, tmp_path):
    app, client, definition, service, control, runtime, s3 = setup_producer(cloud, payload, tmp_path)
    job = deploy(app, client, definition)
    with app.state.store.tx() as db:
        before = runs.get(db, 'domain-authority:' + definition['digest'])
    service.step(app.state.store, job)
    with app.state.store.tx() as db:
        assert before == runs.get(db, 'domain-authority:' + definition['digest'])
