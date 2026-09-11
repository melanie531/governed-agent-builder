"""Opt-in Studio -> create-only foundation -> pinned Runtime -> evidence gates.

Dependencies are SDK-backed in production; tests inject control/data-plane clients.
No runtime retries: DISPATCHED is persisted before invocation; uncertain calls hold
reservations and block. READY checks are one step per SQS continuation.
"""
import json
import time
from pathlib import Path

from fastapi import HTTPException
from foundation_harness.config import digest, load_config
from foundation_harness.context import Denied
from . import foundation_runs as runs


class FoundationJobs:
    def __init__(self, deployment, runtime_client, target, *, enabled=False, evidence_reader=None, artifact_reader=None):
        self.deployment, self.runtime_client, self.target = deployment, runtime_client, target
        self.enabled, self.evidence_reader = enabled, evidence_reader
        self.artifact_reader = artifact_reader

    def approve_request(self, db, definition, persona):
        if not self.enabled:
            raise HTTPException(503, 'LIVE_DISABLED: no fixture fallback')
        approved = runs.get(db, 'foundation-approved:' + definition['digest'])
        if not approved or approved.get('definition_digest') != definition['digest']:
            raise HTTPException(503, 'APPROVED_ARTIFACT_REQUIRED')
        from .foundation_approval import finalized_artifact
        approved = finalized_artifact(db, approved)
        raw = approved['config']
        cfg = load_config(raw, approved['manifest_digest'])
        if (approved['owner'] != persona['id'] or approved['workspace'] != persona['workspace']
                or cfg.limits.maxModelCalls != 1 or len(definition['dataset']) != 1
                or approved['policy_version'] != runs.get(db, 'policy')['version']
                or approved['epoch'] != (runs.get(db, 'foundation-epoch') or 0)
                or approved['expires_at'] <= time.time()
                or raw['systemPrompt'] != [{'text': definition['prompt']}]
                or cfg.evaluation.dataset.digest != digest(definition['dataset'])
                or cfg.evaluation.rubric.digest != digest(definition['rubric'])
                or cfg.model.id != definition['model_id']
                or cfg.model.version != definition['component_versions'][definition['model_id']]
                or [s.id for s in cfg.skills] != definition['skills']
                or any(s.version != definition['component_versions'][s.id] for s in cfg.skills)
                or approved.get('tool_ids') != definition['tools']
                or len(cfg.tools) != len(definition['tools'])
                or any(t.version != definition['component_versions'][i] for t, i in zip(cfg.tools, definition['tools']))):
            raise HTTPException(403, 'CURRENT_APPROVED_MANIFEST_REQUIRED')
        # Reviewed immutable package provenance, not UI-supplied endpoints/roles.
        if (not approved.get('package_digest') or not approved.get('artifact_version')
                or approved.get('artifact_source_digest') != cfg.foundation.digest
                or approved.get('runtime_admission_reviewed') is not True):
            raise HTTPException(503, 'RUNTIME_ADMISSION_REVIEW_REQUIRED')
        # Caller cannot supply a tiny reservation to bypass the lifetime self-cap.
        # Pricing is reviewed server-owned data; deployment/storage and invocation
        # services must all be covered before enabling this candidate.
        from decimal import Decimal
        envelope = approved.get('cost_envelope', {})
        dimensions = {'model', 'gateway', 'policy', 'runtime', 'exchange', 'telemetry', 'storage'}
        amount = Decimal(approved.get('reservation_usd', 'NaN'))
        maximum = Decimal(envelope.get('maximum_usd', 'NaN'))
        if (envelope.get('reviewed') is not True or set(envelope.get('dimensions', [])) != dimensions
                or not envelope.get('rate_card_digest') or not maximum.is_finite()
                or not amount.is_finite() or not Decimal('0') < maximum <= amount <= Decimal('5')):
            raise HTTPException(503, 'ALL_SERVICE_COST_ENVELOPE_REQUIRED')
        # A workload role is per immutable agent package, never shared across users.
        for record in db.select('settings'):
            if record['key'].startswith('foundation-run:'):
                run = json.loads(record['body'])
                if run['runtime_role'] == approved['role'] and (
                        run['agent'] != definition['agent_id']
                        or run['version'] != definition['version']
                        or run['manifest_digest'] != approved['manifest_digest']):
                    raise HTTPException(403, 'DEDICATED_RUNTIME_ROLE_REQUIRED')
        return approved

    def enqueue(self, db, job_id, definition, persona, deadline):
        existing = runs.get(db, 'foundation-run:' + job_id)
        if existing:
            if (existing['definition_digest'], existing['owner'], existing['workspace']) != (
                    definition['digest'], persona['id'], persona['workspace']):
                raise Denied('RESERVATION_IDEMPOTENCY_CONFLICT')
            return
        approved = self.approve_request(db, definition, persona)
        row = runs.reserve(db, job_id=job_id, definition=definition, persona=persona,
                           approved=approved, epoch=approved['epoch'], deadline=deadline)
        row['stored_input'] = definition['dataset'][0]['input']
        row['approved'] = approved
        runs.put(db, 'foundation-run:' + job_id, row)

    def step(self, store, job_id):
        # Commit the dispatch claim separately from network I/O. A crash cannot
        # roll back the claim and accidentally repeat a paid Runtime invocation.
        with store.tx() as db:
            job = dict(db.select('jobs', where=[('id', '=', job_id)]).fetchone())
            row = runs.get(db, 'foundation-run:' + job_id)
            if job['stage'] in ('LIVE_PASS', 'BLOCKED'):
                return
            runs.current(db, row)
            if not self.enabled:
                raise Denied('LIVE_DISABLED')
            stage = job['stage']
            if stage == 'RUNNING':
                runs.claim_dispatch(db, row)
        approved = row['approved']
        if stage == 'VALIDATING':
            self.target.verify()  # internally compare STS to current Studio stacks
            if self.target.account != self.deployment.adapter.policy.target_account:
                raise Denied('STUDIO_TARGET_MISMATCH')
            if self.artifact_reader is None:
                raise Denied('ARTIFACT_READBACK_REQUIRED')
            self.artifact_reader(approved)
            with store.tx() as db:
                existing = runs.get(db, 'foundation-runtime:' + definition_key(row))
                runs.current(db, row)
                if existing:
                    runs.bind_runtime(db, row, existing)
                    self.transition(db, job_id, 'WAIT_RUNTIME')
                    return
            import tempfile
            from foundation_harness.config import canonical
            with tempfile.TemporaryDirectory(prefix='foundation-manifest-') as directory:
                manifest = Path(directory) / (approved['manifest_digest'] + '.json')
                manifest.write_bytes(canonical(approved['config']))
                runtime = self.deployment.submit_new(manifest,
                    role=approved['role'], artifact_key=approved['artifact_key'],
                    artifact_version=approved['artifact_version'],
                    artifact_manifest_digest=approved['manifest_digest'],
                    artifact_source_digest=approved['artifact_source_digest'],
                    network=approved['network'], deployment_key=definition_key(row))
            with store.tx() as db:
                row = runs.get(db, 'foundation-run:' + job_id)
                runs.current(db, row)
                runs.bind_runtime(db, row, runtime)
                self.transition(db, job_id, 'WAIT_RUNTIME')
        elif stage == 'WAIT_RUNTIME':
            ready = self.deployment.readiness(row['runtime'])
            with store.tx() as db:
                runs.current(db, row)
                self.transition(db, job_id, 'RUNNING' if ready['status'] == 'READY' else 'WAIT_RUNTIME')
        elif stage == 'RUNNING':
            runtime = row['runtime']
            response = self.runtime_client.invoke_agent_runtime(
                agentRuntimeArn=runtime['runtime_arn'], qualifier=runtime['runtime_version'],
                runtimeSessionId=job_id.ljust(33, '0'), contentType='application/json',
                accept='application/json', payload=json.dumps({'run_ref': job_id}).encode())
            stream = response['response']
            try:
                raw = stream.read(65537)
            finally:
                stream.close()
            if (len(raw) > 65536 or response.get('statusCode') != 200
                    or response.get('runtimeSessionId') != job_id.ljust(33, '0')):
                raise Denied('RUNTIME_RESPONSE_INVALID')
            result = json.loads(raw)
            if (result.get('run_ref') != job_id or result.get('manifest_digest') != row['manifest_digest']
                    or result.get('runtime_version') != runtime['runtime_version']):
                raise Denied('RUNTIME_RESPONSE_BINDING_DENIED')
            with store.tx() as db:
                current = runs.get(db, 'foundation-run:' + job_id)
                runs.current(db, current)
                current['response'] = result
                current['runtime_request_id'] = response.get('ResponseMetadata', {}).get('RequestId')
                runs.put(db, 'foundation-run:' + job_id, current)
                self.transition(db, job_id, 'EVALUATING')
        elif stage == 'EVALUATING':
            # A trusted reader must fetch persisted trace/evaluation evidence;
            # fields claimed by the Runtime response are never release authority.
            evidence = self.evidence_reader(row) if self.evidence_reader else None
            with store.tx() as db:
                row = runs.get(db, 'foundation-run:' + job_id)
                runs.current(db, row)
                row['evidence'] = evidence
                runs.put(db, 'foundation-run:' + job_id, row)
                self.transition(db, job_id, 'EVIDENCE_CHECK')
        elif stage == 'EVIDENCE_CHECK':
            with store.tx() as db:
                runs.current(db, row)
                evidence = row.get('evidence') or {}
                result = row.get('response') or {}
                expected = {'run_ref': job_id, 'definition_digest': row['definition_digest'],
                            'manifest_digest': row['manifest_digest'],
                            'runtime_version': row['runtime']['runtime_version'],
                            'policy_version': approved['policy_version'], 'epoch': row['epoch'],
                            'dataset_digest': approved['config']['evaluation']['dataset']['digest'],
                            'rubric_digest': approved['config']['evaluation']['rubric']['digest']}
                passed = (row['state'] == 'FINISHED' and row['settled'] is True
                          and result.get('execution_status') == 'EXECUTION_SUCCEEDED'
                          and all(evidence.get(k) == v for k, v in expected.items())
                          and evidence.get('trace_readback') is True
                          and evidence.get('otel_delivery') is True
                          and bool(evidence.get('trace_ids'))
                          and result.get('trace_id') in evidence.get('trace_ids', [])
                          and evidence.get('evaluation_passed') is True
                          and evidence.get('required_evaluations_complete') is True
                          and bool(evidence.get('evaluation_ids')))
                final = {'passed': bool(passed), 'gate': 'LIVE_PASS' if passed else 'EVIDENCE_INCOMPLETE',
                         'mode': 'live', 'production_ready': False,
                         'execution_status': result.get('execution_status', 'UNKNOWN'),
                         'failure': None if passed else {'code': 'EVIDENCE_INCOMPLETE', 'stage': stage},
                         'usage': result.get('usage'), 'billing_estimate_usd': None,
                         'model_route': result.get('model_route'), 'latency_ms': result.get('latency_ms'),
                         'actual_invoice_usd': None, 'runtime': row['runtime']}
                self.transition(db, job_id, 'LIVE_PASS' if passed else 'BLOCKED', final)

    @staticmethod
    def transition(db, job_id, stage, result=None):
        values = {'stage': stage, 'updated': time.time()}
        if result is not None:
            values['result'] = json.dumps(result)
        db.update('jobs', values, where=[('id', '=', job_id)])
        db.insert('events', {'job': job_id, 'stage': stage,
                            'detail': json.dumps({'mode': 'live', 'stage': stage}), 'created': time.time()})


def configured_jobs(store):
    """Explicit operator switch; no automatic discovery or fixture fallback."""
    import os
    if os.getenv('FOUNDATION_LIVE_ENABLED', '0') != '1':
        return None
    import boto3
    from botocore.config import Config
    from scripts.foundation_target import StudioTarget
    from .foundation_deployment import FoundationDeployment
    from .runtime_deployment import DeploymentPolicy
    with store.tx() as db:
        settings = runs.get(db, 'foundation-deployment')
    if not settings:
        raise RuntimeError('REVIEWED_FOUNDATION_DEPLOYMENT_REQUIRED')
    session = boto3.Session(region_name=settings['region'])
    sdk = Config(retries={'total_max_attempts': 1}, connect_timeout=5, read_timeout=65)
    policy = DeploymentPolicy(settings['region'], settings['account'],
                              frozenset(settings['roles']), settings['bucket'], allow_mutations=True)
    return FoundationJobs(FoundationDeployment(session.client('bedrock-agentcore-control', config=sdk),
                          policy, settings['network']), session.client('bedrock-agentcore', config=sdk),
                          StudioTarget(session), enabled=True,
                          artifact_reader=ArtifactReadback(session.client('s3', config=sdk), settings['bucket']))


class ArtifactReadback:
    """Verify exact versioned S3 code bytes before CreateRuntime, without execution."""
    def __init__(self, s3, bucket):
        self.s3, self.bucket = s3, bucket

    def __call__(self, approved):
        import hashlib
        import io
        import zipfile
        response = self.s3.get_object(Bucket=self.bucket, Key=approved['artifact_key'],
                                      VersionId=approved['artifact_version'])
        stream = response['Body']
        try:
            data = stream.read(64 * 1024 * 1024 + 1)
        finally:
            stream.close()
        if (len(data) > 64 * 1024 * 1024 or response.get('VersionId') != approved['artifact_version']
                or hashlib.sha256(data).hexdigest() != approved['package_digest']):
            raise Denied('ARTIFACT_CONTENT_DIGEST_DENIED')
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            config_path = 'runtime/custom_foundation/harness.json'
            admission_path = 'runtime/custom_foundation/admission.json'
            if archive.getinfo(config_path).file_size > 32768 or archive.getinfo(admission_path).file_size > 4096:
                raise Denied('ARTIFACT_CONFIG_CAP')
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise Denied('DUPLICATE_ARTIFACT_ENTRIES')
            config = json.loads(archive.read(config_path))
            admission = json.loads(archive.read(admission_path))
            from foundation_harness.package_admission import validate_admission
            validate_admission(admission, config)
            if admission['runtime_role'] != approved['role']:
                raise Denied('ARTIFACT_ROLE_BINDING_DENIED')
            if config != approved['config'] or admission != approved['admission']:
                raise Denied('ARTIFACT_MANIFEST_DENIED')
            # A source-only ZIP is not a deployable Linux dependency package.
            if not {'main.py', 'bedrock_agentcore/runtime/app.py', 'opentelemetry/sdk/trace/__init__.py'} <= set(archive.namelist()):
                raise Denied('LOCKED_RUNTIME_DEPENDENCIES_REQUIRED')


def definition_key(row):
    return digest([row['workspace'], row['owner'], row['agent'], row['version'], row['manifest_digest']])
