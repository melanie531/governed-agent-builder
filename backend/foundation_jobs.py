"""Opt-in Studio -> create-only foundation -> pinned Runtime -> evidence gates.

Dependencies are SDK-backed in production; tests inject control/data-plane clients.
No runtime retries: DISPATCHED is persisted before invocation; uncertain calls hold
reservations and block. READY checks are one step per SQS continuation.
"""
import json
import time
from uuid import uuid4
from pathlib import Path

from fastapi import HTTPException
from foundation_harness.config import digest, load_config
from foundation_harness.context import Denied
from . import foundation_runs as runs


class FoundationJobs:
    def __init__(self, deployment, runtime_client, target, *, enabled=False, evidence_reader=None, artifact_reader=None, policy_evaluator=None, producer=None, evidence_collector=None, evidence_exporter=None):
        self.deployment, self.runtime_client, self.target = deployment, runtime_client, target
        self.enabled, self.evidence_reader = enabled, evidence_reader
        self.artifact_reader = artifact_reader
        self.policy_evaluator = policy_evaluator
        self.producer = producer
        self.evidence_collector = evidence_collector
        self.evidence_exporter = evidence_exporter

    def readiness_issues(self, db, definition, persona):
        """Read server-owned authority only; never reserve, renew, create or invoke.

        Artifact bytes, target identity and SDK READY/endpoint pins are rechecked
        by the existing worker immediately before use, not asserted by the UI.
        """
        issues = []
        def block(code, message=None):
            issues.append({'code': code, 'message': message or code.replace('_', ' ')})
        if not self.enabled:
            block('LIVE_DISABLED')
        if (self.deployment is None or self.target is None or self.runtime_client is None
                or not callable(getattr(self.deployment, 'submit_new', None))
                or not callable(getattr(self.deployment, 'readiness', None))):
            block('deployment_driver_missing')
        if self.artifact_reader is None:
            block('ARTIFACT_READBACK_REQUIRED')
        if not definition.get('digest') or not definition.get('agent_id'):
            block('saved_version_required', 'Save an immutable version before deployment admission and artifact verification')
            return issues
        if not runs.get(db, 'foundation-deployment'):
            block('REVIEWED_FOUNDATION_DEPLOYMENT_REQUIRED')
        if not runs.get(db, 'foundation-artifact:' + definition['digest']):
            block('VERIFIED_ARTIFACT_FINALIZATION_REQUIRED')
        try:
            approved = self.approve_request(db, definition, persona)
            if self.deployment is not None:
                self.deployment.adapter.policy.validate(approved['role'])
                if self.target.account != self.deployment.adapter.policy.target_account:
                    block('STUDIO_TARGET_MISMATCH')
                network = approved.get('network', {})
                if (network != self.deployment.network or network.get('networkMode') != 'VPC'
                        or not network.get('networkModeConfig', {}).get('subnets')
                        or not network.get('networkModeConfig', {}).get('securityGroups')):
                    block('APPROVED_EXISTING_VPC_BINDING_REQUIRED')
        except HTTPException as exc:
            # Only internal fixed codes, never endpoints, roles or provider text.
            import re
            code = exc.detail if isinstance(exc.detail, str) and re.fullmatch(r'[A-Z_]{1,80}', exc.detail) else 'CURRENT_EXECUTION_BINDING_REQUIRED'
            block(code)
        except (ValueError, KeyError, AttributeError, TypeError, ArithmeticError):
            block('INVALID_DEPLOYMENT_BINDING')
        except Exception:
            block('DEPLOYMENT_POLICY_DENIED')
        return issues

    def approve_request(self, db, definition, persona):
        if not self.enabled:
            raise HTTPException(503, 'LIVE_DISABLED: no fixture fallback')
        approved = runs.get(db, 'foundation-approved:' + definition['digest'])
        if not approved or approved.get('definition_digest') != definition['digest']:
            raise HTTPException(503, 'APPROVED_ARTIFACT_REQUIRED')
        from .self_service_admission import check_current
        check_current(db, persona, definition, approved)
        from .foundation_approval import finalized_artifact
        approved = finalized_artifact(db, approved)
        raw = approved['config']
        cfg = load_config(raw, approved['manifest_digest'])
        if (approved['owner'] != persona['id'] or approved['workspace'] != persona['workspace']
                or cfg.limits.maxModelCalls != 1 or len(definition['dataset']) != 1
                or approved['policy_version'] != runs.get(db, 'policy')['version']
                or approved['epoch'] != (runs.get(db, 'foundation-epoch') or 0)
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

    def invocation_endpoint(self, runtime):
        """qualifier is an endpoint NAME, never the immutable numeric version.

        DEFAULT is used only after exact control-plane readback. Recheck immediately
        before invocation so a retarget observed after readiness fails closed.
        Runtime response and evidence still independently bind runtime_version.
        """
        name = 'DEFAULT'
        endpoint = self.deployment.adapter.client.get_agent_runtime_endpoint(
            agentRuntimeId=runtime['runtime_id'], endpointName=name)
        if (endpoint.get('name') != name or endpoint.get('status') != 'READY'
                or endpoint.get('agentRuntimeArn') != runtime['runtime_arn']
                or endpoint.get('liveVersion') != runtime['runtime_version']
                or endpoint.get('targetVersion') != runtime['runtime_version']):
            raise Denied('RUNTIME_ENDPOINT_VERSION_NOT_READY')
        return {'name': name, 'runtime_arn': runtime['runtime_arn'],
                'live_version': endpoint['liveVersion'], 'target_version': endpoint['targetVersion']}

    def enqueue(self, db, job_id, definition, persona, deadline, *, renew_authority=True):
        existing = runs.get(db, 'foundation-run:' + job_id)
        if existing:
            if (existing['definition_digest'], existing['owner'], existing['workspace']) != (
                    definition['digest'], persona['id'], persona['workspace']):
                raise Denied('RESERVATION_IDEMPOTENCY_CONFLICT')
            return
        from .self_service_admission import admit
        if not self.enabled:
            raise HTTPException(503, 'LIVE_DISABLED: no fixture fallback')
        if renew_authority:
            approved = admit(db, persona, definition['agent_id'], definition['version'], evaluator=self.policy_evaluator)
        else:
            approved = runs.get(db, 'foundation-approved:' + definition['digest'])
            from .self_service_admission import check_current
            check_current(db, persona, definition, approved)
        if not runs.get(db, 'foundation-artifact:' + definition['digest']):
            runs.put(db, 'foundation-pending:' + job_id, {
                'definition': definition, 'owner': persona['id'], 'agent': definition['agent_id'],
                'version': definition['version'], 'deadline': deadline})
            self.transition(db, job_id, 'WAIT_ARTIFACT')
            return
        approved = self.approve_request(db, definition, persona)
        row = runs.reserve(db, job_id=job_id, definition=definition, persona=persona,
                           approved=approved, epoch=approved['epoch'], deadline=deadline)
        row['stored_input'] = definition['dataset'][0]['input']
        row['approved'] = approved
        runs.put(db, 'foundation-run:' + job_id, row)

    def step(self, store, job_id):
        if self.producer is not None:
            with store.tx() as db:
                job = db.select('jobs', where=[('id', '=', job_id)]).fetchone()
                produce = job and job['stage'] == 'WAIT_ARTIFACT'
            if produce:
                if not self.enabled:
                    raise Denied('LIVE_DISABLED')
                self.producer.produce(store, job_id)
        # Commit the dispatch claim separately from network I/O. A crash cannot
        # roll back the claim and accidentally repeat a paid Runtime invocation.
        with store.tx() as db:
            job = dict(db.select('jobs', where=[('id', '=', job_id)]).fetchone())
            row = runs.get(db, 'foundation-run:' + job_id)
            if job['stage'] == 'WAIT_ARTIFACT':
                pending = runs.get(db, 'foundation-pending:' + job_id)
                if not pending or pending['deadline'] <= time.time():
                    raise Denied('AUTHORITY_EXPIRED')
                from .catalog import PERSONAS
                principal = db.select('principals', where=[('id', '=', pending['owner'])]).fetchone()
                if principal and principal['expires'] <= time.time():
                    raise Denied('MEMBERSHIP_EXPIRED')
                persona = json.loads(principal['body']) if principal else PERSONAS.get(pending['owner'])
                if not persona or (pending['definition'].get('mode') == 'CLOUD-HOSTED DEMO' and not principal):
                    raise Denied('MEMBERSHIP_REQUIRED')
                if principal:
                    auth = db.select('job_authority', where=[('id', '=', job_id)]).fetchone()
                    session = db.select('hosted_sessions', where=[('id_hash', '=', auth['session_hash'])]).fetchone() if auth else None
                    if not session or session['expires'] <= time.time() or session['subject'] != pending['owner']:
                        raise Denied('SESSION_REVOKED')
                from .self_service_admission import check_current
                approved = runs.get(db, 'foundation-approved:' + pending['definition']['digest'])
                check_current(db, persona, pending['definition'], approved)
                if not runs.get(db, 'foundation-artifact:' + pending['definition']['digest']):
                    return  # Mechanical producer not yet delivered; no ready bypass.
                self.enqueue(db, job_id, pending['definition'], persona, pending['deadline'], renew_authority=False)
                self.transition(db, job_id, 'VALIDATING')
                return
            if job['stage'] in ('LIVE_PASS', 'BLOCKED'):
                return
            runs.current(db, row)
            if not self.enabled:
                raise Denied('LIVE_DISABLED')
            stage = job['stage']
            if stage == 'EVALUATING':
                owner_key = 'foundation-evaluating:' + job_id
                active = runs.get(db, owner_key)
                if active and active.get('expires', float('inf')) > time.time():
                    return  # another step owns collection AND the outer transition
                owner = {'token': uuid4().hex, 'stage': stage, 'expires': time.time() + 240,
                         'fence': digest([row['approved'], row.get('response_digest'), row.get('runtime')])}
                runs.put(db, owner_key, owner)
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
            endpoint = self.invocation_endpoint(row['runtime']) if ready['status'] == 'READY' else None
            with store.tx() as db:
                runs.current(db, row)
                if endpoint:
                    row['invocation_endpoint'] = endpoint
                    runs.put(db, 'foundation-run:' + job_id, row)
                self.transition(db, job_id, 'RUNNING' if ready['status'] == 'READY' else 'WAIT_RUNTIME')
        elif stage == 'RUNNING':
            runtime = row['runtime']
            endpoint = self.invocation_endpoint(runtime)
            if endpoint != row.get('invocation_endpoint'):
                raise Denied('RUNTIME_ENDPOINT_BINDING_CHANGED')
            response = self.runtime_client.invoke_agent_runtime(
                agentRuntimeArn=runtime['runtime_arn'], qualifier=endpoint['name'],
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
                if current.get('response') is not None:
                    raise Denied('IMMUTABLE_RESPONSE_ALREADY_RECORDED')
                current['response'] = result
                current['response_digest'] = digest(result)
                current['runtime_request_id'] = response.get('ResponseMetadata', {}).get('RequestId')
                runs.put(db, 'foundation-run:' + job_id, current)
                self.transition(db, job_id, 'EVALUATING')
        elif stage == 'EVALUATING':
            # A trusted reader must fetch persisted trace/evaluation evidence;
            # fields claimed by the Runtime response are never release authority.
            from .evaluation_collector import CollectionInFlight
            failure_code = None
            try:
                if self.evidence_exporter is not None:
                    row = self.evidence_exporter.export(store, job_id, owner)
                if self.evidence_collector is not None and (self.evidence_exporter is None or row['approved'].get('agentcore_evaluation')):
                    row = self.evidence_collector.collect(store, job_id)
                evidence = self.evidence_reader(row) if self.evidence_reader else None
            except CollectionInFlight:
                with store.tx() as db:
                    if runs.get(db, owner_key) == owner:
                        db.delete('settings', where=[('key', '=', owner_key)])
                return  # keep the rightful collector's stage and evidence intact
            except Exception as error:
                # Only fixed internal codes; never provider bodies or exception text.
                import re
                failure_code = str(error) if isinstance(error, Denied) and re.fullmatch(r'[A-Z_]{1,80}', str(error)) else 'EVIDENCE_PROVIDER_ERROR'
                evidence = None  # Provider failures must not leak or fall back to fixtures.
            with store.tx() as db:
                row = runs.get(db, 'foundation-run:' + job_id)
                runs.current(db, row)
                observed = db.select('jobs', where=[('id', '=', job_id)]).fetchone()
                if (observed['stage'] != owner['stage'] or runs.get(db, owner_key) != owner
                        or digest([row['approved'], row.get('response_digest'), row.get('runtime')]) != owner['fence']):
                    return
                if failure_code:
                    row['evidence_failure'] = failure_code
                if evidence is not None or not row.get('evidence'):
                    row['evidence'] = evidence
                db.delete('settings', where=[('key', '=', owner_key)])
                runs.put(db, 'foundation-run:' + job_id, row)
                self.transition(db, job_id, 'EVIDENCE_CHECK')
        elif stage == 'EVIDENCE_CHECK':
            with store.tx() as db:
                runs.current(db, row)
                evidence = row.get('evidence') or {}
                result = row.get('response') or {}
                from .result_evidence import project
                projection = project(row, evidence)
                from .foundation_approval import linux_validation
                artifact_binding = runs.get(db, 'foundation-artifact:' + row['definition_digest'])
                target_executed = linux_validation(db, artifact_binding)['status'] == 'PASS'
                passed = (target_executed and row['state'] == 'FINISHED' and row['settled'] is True
                          and result.get('execution_status') == 'EXECUTION_SUCCEEDED'
                          and projection['status'] == 'AVAILABLE'
                          and projection['required_judge_passed']
                          and projection['quality_status'] == 'PASS'
                          and all(e['status'] == 'PASS' for e in projection['evaluations']))
                final = {'passed': bool(passed), 'gate': 'LIVE_PASS' if passed else 'EVIDENCE_INCOMPLETE',
                         'mode': 'live', 'production_ready': False,
                         'execution_status': result.get('execution_status', 'UNKNOWN'),
                         'failure': None if passed else {'code': 'EVIDENCE_INCOMPLETE', 'stage': stage},
                         'result_evidence': projection,
                         'usage': projection['cost']['usage'], 'billing_estimate_usd': None,
                         'model_route': projection['cost']['model_route'],
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


def configured_jobs(store, *, worker=False):
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
    producer = None
    if worker and os.getenv('FOUNDATION_PRODUCER_ENABLED', '0') == '1':
        from .foundation_producer import FoundationProducer
        sts = session.client('sts', config=sdk)
        producer = FoundationProducer(session.client('s3', config=sdk),
            lambda: sts.get_caller_identity()['Arn'])
    from .evaluation_collector import configured_evidence
    evidence_settings = settings.get('evaluation_collector', {})
    collector, reader = (None, None)
    exporter = None
    if worker and evidence_settings.get('enabled') is True:
        collector, reader = configured_evidence(evidence_settings,
            agentcore=session.client('bedrock-agentcore', config=sdk),
            control=session.client('bedrock-agentcore-control', config=sdk),
            s3=session.client('s3', config=sdk), cloudwatch=session.client('logs', config=sdk))
    if collector is not None:
        from .run_evidence_exporter import RunEvidenceExporter
        exporter = RunEvidenceExporter(s3=collector.s3, cloudwatch=reader.cloudwatch,
            bucket=evidence_settings['bucket'], prefix=evidence_settings['prefix'])
    return FoundationJobs(FoundationDeployment(session.client('bedrock-agentcore-control', config=sdk),
                          policy, settings['network']), session.client('bedrock-agentcore', config=sdk),
                          StudioTarget(session), enabled=True, producer=producer,
                          evidence_collector=collector, evidence_reader=reader, evidence_exporter=exporter,
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
