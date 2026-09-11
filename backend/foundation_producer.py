"""Queue-only, bounded ZIP composition. No installer, browser identity or HTTP API.

The operator approves one immutable Foundation bundle after a trusted Linux build.
Each domain composition preserves EVERY base byte except the enumerated config
files. A composition receipt is not independent Linux execution evidence.
"""
import hashlib
import io
import json
import re
import time
import zipfile

from fastapi import HTTPException
from foundation_harness.config import canonical, digest, load_config
from foundation_harness.context import Denied
from foundation_harness.package_admission import validate_admission
from . import foundation_runs as runs
from .self_service_admission import check_current

TARGET = 'linux-arm64-python3.13'
CONFIG_FILES = {'runtime/custom_foundation/harness.json',
                'runtime/custom_foundation/admission.json', 'package-status.json'}
MAX_BYTES = 64 * 1024 * 1024


def read_object(s3, bucket, key, version, sha):
    if not version or version == 'null':
        raise Denied('IMMUTABLE_OBJECT_VERSION_REQUIRED')
    response = s3.get_object(Bucket=bucket, Key=key, VersionId=version)
    stream = response['Body']
    try:
        blob = stream.read(MAX_BYTES + 1)
    finally:
        stream.close()
    if (len(blob) > MAX_BYTES or response.get('VersionId') != version
            or hashlib.sha256(blob).hexdigest() != sha):
        raise Denied('ARTIFACT_CONTENT_DIGEST_DENIED')
    return blob


def bundle_files(blob):
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        entries = archive.infolist()
        names = [i.filename for i in entries]
        if (len(names) != len(set(names)) or sum(i.file_size for i in entries) > MAX_BYTES
                or any(n.startswith('/') or '..' in n.split('/') or '\\' in n for n in names)
                or any((i.external_attr >> 16) & 0o170000 == 0o120000 for i in entries)):
            raise Denied('BOUNDED_REGULAR_BUNDLE_REQUIRED')
        return {n: archive.read(n) for n in names}


def validate_base(db, record, blob):
    from scripts.package_foundation import ROOT, SOURCES, source_digest
    files = bundle_files(blob)
    proof = runs.get(db, 'foundation-base-linux:' + record['package_digest']) or {}
    expected = {k: record[k] for k in ('package_digest', 'artifact_version', 'artifact_source_digest',
                                      'dependency_lock_digest', 'target')}
    if (record.get('provenance') != 'platform-foundation-bundle' or not record.get('approver')
            or record.get('target') != TARGET or record['artifact_source_digest'] != source_digest()
            or proof.get('status') != 'PASS' or proof.get('execution') != 'ACTUAL_LINUX'
            or proof.get('entrypoint_passed') is not True or not proof.get('validator_identity')
            or not re.fullmatch('[a-f0-9]{64}', proof.get('evidence_digest', ''))
            or any(proof.get(k) != v for k, v in expected.items())
            or record['dependency_lock_digest'] != hashlib.sha256(
                (ROOT / 'runtime/custom_foundation/requirements.lock').read_bytes()).hexdigest()
            or record.get('files_digest') != digest({n: hashlib.sha256(b).hexdigest() for n, b in files.items()})
            or any(files.get(n) != (ROOT / n).read_bytes() for n in SOURCES)
            or not {'main.py', 'bedrock_agentcore/runtime/app.py', 'pydantic_core/__init__.py',
                    'opentelemetry/sdk/trace/__init__.py', 'boto3/__init__.py'} <= files.keys()):
        raise Denied('APPROVED_LOCKED_LINUX_BASE_REQUIRED')
    return files, proof


def compose(files, approved):
    """Deterministic config-only composition; all executable/dependency bytes pinned."""
    raw = approved['config']
    load_config(raw, approved['manifest_digest'])
    validate_admission(approved['admission'], raw)
    files = dict(files)
    files['runtime/custom_foundation/harness.json'] = canonical(raw)
    files['runtime/custom_foundation/admission.json'] = canonical(approved['admission'])
    files['package-status.json'] = canonical({'mode': 'live', 'deploy_ready': False,
        'linux_execution': 'UNVERIFIED', 'admission_config_present': True})
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    blob = output.getvalue()
    if len(blob) > MAX_BYTES:
        raise Denied('ARTIFACT_SIZE_CAP')
    return blob


def pending_authority(db, job_id):
    from .app import get_version
    from .catalog import PERSONAS
    pending = runs.get(db, 'foundation-pending:' + job_id)
    job = db.select('jobs', where=[('id', '=', job_id)]).fetchone()
    if not pending or not job or pending['deadline'] <= time.time():
        raise Denied('AUTHORITY_EXPIRED')
    definition = get_version(db, pending['agent'], pending['version'])
    if definition != pending['definition'] or definition['owner'] != pending['owner']:
        raise Denied('CURRENT_DEFINITION_DENIED')
    principal = db.select('principals', where=[('id', '=', pending['owner'])]).fetchone()
    if principal:
        if principal['expires'] <= time.time():
            raise Denied('MEMBERSHIP_EXPIRED')
        owner = json.loads(principal['body'])
        auth = db.select('job_authority', where=[('id', '=', job_id)]).fetchone()
        session = db.select('hosted_sessions', where=[('id_hash', '=', auth['session_hash'])]).fetchone() if auth else None
        if not session or session['expires'] <= time.time() or session['subject'] != pending['owner']:
            raise Denied('SESSION_REVOKED')
    else:
        owner = PERSONAS.get(pending['owner'])
        if definition.get('mode') == 'CLOUD-HOSTED DEMO' or not owner:
            raise Denied('MEMBERSHIP_REQUIRED')
    approved = runs.get(db, 'foundation-approved:' + definition['digest']) or {}
    check_current(db, owner, definition, approved)
    return definition, owner, approved


def composition_valid(db, binding):
    receipt = runs.get(db, 'foundation-composition:' + binding['package_digest']) or {}
    base = runs.get(db, 'foundation-bundle:' + receipt.get('foundation_id', '')) or {}
    proof = runs.get(db, 'foundation-base-linux:' + base.get('package_digest', '')) or {}
    return (receipt.get('binding_digest') == digest(binding)
            and receipt.get('base_digest') == digest(base)
            and receipt.get('baseline_evidence_digest') == digest(proof)
            and receipt.get('coverage') == 'UNCHANGED_CODE_DEPS_AND_VALIDATED_CONFIG'
            and proof.get('execution') == 'ACTUAL_LINUX' and proof.get('status') == 'PASS')


class FoundationProducer:
    def __init__(self, s3, principal_reader):
        self.s3, self.principal_reader = s3, principal_reader

    def context(self, db, job_id):
        definition, owner, approved = pending_authority(db, job_id)
        settings = runs.get(db, 'foundation-deployment') or {}
        arn = self.principal_reader()
        match = re.fullmatch(r'arn:aws:sts::(\d{12}):assumed-role/([^/]+)/[^/]+', arn or '')
        role = f'arn:aws:iam::{match[1]}:role/{match[2]}' if match else None
        if (not role or role != settings.get('producer_role') or not settings.get('bucket')
                or approved['role'] not in settings.get('roles', [])
                or settings.get('network', {}).get('networkMode') != 'VPC'):
            raise Denied('EXACT_PRODUCER_WORKLOAD_REQUIRED')
        base = runs.get(db, 'foundation-bundle:' + definition['foundation_id']) or {}
        if (not base or base.get('source_record_digest') != digest(runs.get(db, 'foundation-source:' + definition['foundation_id']))
                or base.get('bucket') != settings['bucket']):
            raise Denied('CURRENT_APPROVED_BUNDLE_REQUIRED')
        return definition, owner, approved, settings, base, role

    def produce(self, store, job_id):
        # Admission + intent commit/CAS BEFORE expensive reads or upload. A lost
        # final CAS can leave an unreferenced object, never runtime authority.
        with store.tx() as db:
            definition, owner, approved, settings, base, role = self.context(db, job_id)
            snapshot = digest([approved, settings, base])
            runs.put(db, 'foundation-produce:' + job_id, {'snapshot': snapshot,
                     'manifest_digest': approved['manifest_digest'], 'run_ref': job_id})
        s3, bucket = self.s3, settings['bucket']
        if (s3.get_bucket_versioning(Bucket=bucket).get('Status') != 'Enabled'
                or not all(s3.get_public_access_block(Bucket=bucket)['PublicAccessBlockConfiguration'].get(k) is True
                    for k in ('BlockPublicAcls', 'IgnorePublicAcls', 'BlockPublicPolicy', 'RestrictPublicBuckets'))):
            raise Denied('PRIVATE_VERSIONED_ARTIFACT_BUCKET_REQUIRED')
        blob = read_object(s3, bucket, base['artifact_key'], base['artifact_version'], base['package_digest'])
        with store.tx() as db:
            current = self.context(db, job_id)
            if digest(list(current[2:5])) != snapshot:
                raise Denied('PRODUCER_BINDING_CHANGED')
            files, proof = validate_base(db, base, blob)
            # Protected consumer, not an admin-shaped producer-script receipt.
            from scripts.verify_package_admission import consume_policy_approval
            consume_policy_approval(db, owner, definition, approved)
            package = compose(files, approved)
            sha = hashlib.sha256(package).hexdigest()
            key = 'releases/' + sha + '/foundation.zip'
            runs.put(db, 'foundation-produce:' + job_id, {'snapshot': snapshot,
                     'manifest_digest': approved['manifest_digest'], 'run_ref': job_id, 'package_digest': sha})
        with store.tx() as db:
            current = self.context(db, job_id)
            if digest(list(current[2:5])) != snapshot:
                raise Denied('PRODUCER_BINDING_CHANGED')
            # Conditional content-addressed write supports retries after a crash.
            # No caller-selected key, identity, role or endpoint is accepted.
            from botocore.exceptions import ClientError
            try:
                result = s3.put_object(Bucket=bucket, Key=key, Body=package,
                    IfNoneMatch='*', ServerSideEncryption='AES256', ContentType='application/zip')
                version = result.get('VersionId')
            except ClientError as error:
                if error.response['Error']['Code'] not in ('PreconditionFailed', '412'):
                    raise
                version = s3.head_object(Bucket=bucket, Key=key).get('VersionId')
            if read_object(s3, bucket, key, version, sha) != package:
                raise Denied('COMPLETE_LOCKED_PACKAGE_DIGEST_REQUIRED')
            # Re-read eligibility following external I/O; repository commit CAS
            # fences concurrent changes, including source/grants/session revoke.
            current = self.context(db, job_id)
            if digest(list(current[2:5])) != snapshot:
                raise Denied('PRODUCER_BINDING_CHANGED')
            from .foundation_approval import FinalizeFoundation, _finalize_artifact
            data = FinalizeFoundation(agent_id=definition['agent_id'], version=definition['version'],
                definition_digest=definition['digest'], approval_revision=approved['revision'],
                package_digest=sha, artifact_version=version, request_id='producer-' + approved['manifest_digest'])
            result = _finalize_artifact(db, {'id': role}, data, owner,
                runs.get(db, 'foundation-source:' + definition['foundation_id'])['platform'],
                verifier=lambda *args: key)
            receipt = {'binding_digest': digest(result['binding']), 'base_digest': digest(base),
                'foundation_id': definition['foundation_id'], 'baseline_evidence_digest': digest(proof),
                'coverage': 'UNCHANGED_CODE_DEPS_AND_VALIDATED_CONFIG',
                'final_target_execution': 'UNVERIFIED'}
            receipt_key = 'foundation-composition:' + sha
            old = runs.get(db, receipt_key)
            if old and old != receipt:
                raise Denied('IMMUTABLE_COMPOSITION_CONFLICT')
            runs.put(db, receipt_key, receipt)
            return result
