"""Server-owned, opt-in AgentCore Evaluate collector. Never constructs SDK clients.

Protected settings/approved records are capabilities; Runtime JSON is not. The
codeBased stays gated except for an explicitly injected deterministic TRACE
adapter with protected mapping and authenticated service-entry boundary.
"""
import copy
import hashlib
import json
import math
import re
from datetime import datetime

from botocore.exceptions import ClientError
from botocore.validate import validate_parameters
from foundation_harness.config import canonical, digest
from foundation_harness.context import Denied
from . import foundation_runs as runs
from .result_evidence import AgentCoreEvidenceRepository, EvidenceReader, binding, checked

CAP = 65536


class CollectionInFlight(Denied):
    """A claimed/uncertain Evaluate must not be replayed or abort its owner."""



def snapshot(value):
    """Lossless JSON timestamp normalization for GetEvaluator configuration pins."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: snapshot(v) for k, v in value.items() if k != 'ResponseMetadata'}
    if isinstance(value, list):
        return [snapshot(v) for v in value]
    return value


def read_pinned(s3, bucket, prefix, expected, pin):
    key, version = pin['key'], pin['version_id']
    # Job-specific immutable namespace prevents a descriptor granting another job.
    root = prefix + digest(expected) + '/'
    if (pin.get('bucket', bucket) != bucket or not key.startswith(root)
            or '..' in key.split('/') or '://' in key or not version or version == 'null'):
        raise Denied('EVIDENCE_STORE_GRANT')
    response = s3.get_object(Bucket=bucket, Key=key, VersionId=version)
    stream = response['Body']
    try:
        raw = stream.read(CAP + 1)
    finally:
        stream.close()
    if (not raw or len(raw) > CAP or response.get('ContentLength') != len(raw)
            or response.get('VersionId') != version
            or response.get('Metadata') != {'binding-digest': digest(expected), 'sha256': pin['sha256']}
            or hashlib.sha256(raw).hexdigest() != pin['sha256']):
        raise Denied('EVIDENCE_BYTES')
    return raw


def persist(s3, bucket, prefix, expected, name, value):
    raw = canonical(value)
    if len(raw) > CAP:
        raise Denied('EVIDENCE_CAP')
    sha = hashlib.sha256(raw).hexdigest()
    key = prefix + digest(expected) + '/' + name + '-' + sha + '.json'
    metadata = {'binding-digest': digest(expected), 'sha256': sha}
    try:
        response = s3.put_object(Bucket=bucket, Key=key, Body=raw, IfNoneMatch='*',
            ServerSideEncryption='AES256', ContentType='application/json', Metadata=metadata)
        version = response.get('VersionId')
    except ClientError as error:
        if error.response['Error']['Code'] not in ('PreconditionFailed', '412'):
            raise
        version = s3.head_object(Bucket=bucket, Key=key).get('VersionId')
    pin = {'key': key, 'version_id': version, 'sha256': sha}
    if read_pinned(s3, bucket, prefix, expected, pin) != raw:
        raise Denied('EVIDENCE_READBACK')
    return pin


class PinnedSpanReader:
    """Read a pre-authorized complete span export, not Runtime supplied URLs.

    A trusted exporter must register collection_input in the protected run row.
    It contains an immutable S3 pin and a completed, previously authorized query
    ID. The query returns only binding_digest/trace_id/span_id, never content.
    Exact expected span IDs and counts reject paginated/truncated exports.
    """
    def __init__(self, *, s3, cloudwatch, bucket, prefix):
        self.s3, self.cloudwatch, self.bucket, self.prefix = s3, cloudwatch, bucket, prefix

    def __call__(self, row):
        pin = row['collection_input']
        expected = binding(row)
        if pin['binding'] != expected:
            raise Denied('SPAN_GRANT')
        content = json.loads(read_pinned(self.s3, self.bucket, self.prefix, expected, pin))
        if (content['binding'] != expected or content.get('complete') is not True
                or content.get('nextToken') or content.get('truncated') is not False
                or content['source_ids'] != pin['source_ids']
                or digest(content['sources']) != pin['source_digest']
                or [s['id'] for s in content['sources']] != pin['source_ids']
                or any(not isinstance(s['text'], str) or not s['text'] for s in content['sources'])):
            raise Denied('SPAN_SOURCE_BINDING')
        spans = content['sessionSpans']
        ids = [(s['traceId'], s['spanId']) for s in spans]
        if (not 1 <= len(spans) <= 100 or len(set(ids)) != len(ids)
                or [list(i) for i in ids] != pin['span_ids']
                or any(not re.fullmatch('[a-f0-9]{32}', t) or not re.fullmatch('[a-f0-9]{16}', s) for t, s in ids)):
            raise Denied('SPAN_COVERAGE')
        if pin.get('format') == 'CLOUDWATCH_SPAN_JSON_V1':
            from .run_evidence_exporter import query_spans, content_for
            actual = query_spans(self.cloudwatch, pin['query_id'], row)
            rebuilt, _ = content_for(row, actual)
            if rebuilt != content:
                raise Denied('SPAN_EXPORT_CONTENT_CHANGED')
            return content
        query = self.cloudwatch.get_query_results(queryId=pin['query_id'])
        if query.get('status') != 'Complete' or query.get('nextToken') or len(query['results']) != len(ids):
            raise Denied('SPAN_QUERY_INCOMPLETE')
        actual = []
        for record in query['results']:
            fields = {x['field']: x['value'] for x in record}
            if len(fields) != len(record) or fields.get('binding_digest') != digest(expected):
                raise Denied('SPAN_QUERY_BINDING')
            actual.append((fields['trace_id'], fields['span_id']))
        if len(set(actual)) != len(actual) or set(actual) != set(ids):
            raise Denied('SPAN_QUERY_COVERAGE')
        return content


def decision(response, request, case, native, rule):
    results = response.get('evaluationResults', [])
    if (not response.get('ResponseMetadata', {}).get('RequestId')
            or response.get('ResponseMetadata', {}).get('HTTPStatusCode') != 200 or len(results) != 1):
        raise Denied('EVALUATION_INCOMPLETE')
    result = results[0]
    ctx = result['context']['spanContext']
    if (result['evaluatorId'] != native['evaluatorId'] or result['evaluatorArn'] != native['evaluatorArn']
            or result['evaluatorName'] != native['evaluatorName']
            or ctx['sessionId'] != case['session_id'] or ctx.get('traceId') != case['trace_id']
            or (native['level'] == 'TOOL_CALL' and ctx.get('spanId') != case['span_id'])
            or result.get('errorCode') or result.get('errorMessage') or result.get('ignoredReferenceInputFields')):
        raise Denied('EVALUATION_NATIVE_ERROR')
    # An HTTP 200 or completed query is never a judge decision.
    if rule['kind'] == 'numeric':
        value = result.get('value')
        if type(value) not in (int, float) or not math.isfinite(value) or not rule['min'] <= value <= rule['max']:
            raise Denied('EVALUATION_SCORE')
        return 'PASS' if value >= rule['pass_min'] else 'FAIL'
    if rule['kind'] == 'categorical' and result.get('label') in rule['labels']:
        return 'PASS' if result['label'] in rule['pass_labels'] else 'FAIL'
    raise Denied('EVALUATION_SCORE')


class EvaluationCollector:
    def __init__(self, *, agentcore, control, s3, span_reader, bucket, prefix, code_evaluator=None):
        if any(x is None for x in (agentcore, control, s3, span_reader)) or not bucket or not prefix.endswith('/'):
            raise Denied('COLLECTOR_DEPENDENCIES_REQUIRED')
        self.agentcore, self.control, self.s3, self.span_reader = agentcore, control, s3, span_reader
        self.bucket, self.prefix = bucket, prefix
        self.code_evaluator = code_evaluator

    def context(self, db, job_id):
        row = runs.get(db, 'foundation-run:' + job_id)
        runs.current(db, row)
        job = db.select('jobs', where=[('id', '=', job_id)]).fetchone()
        if not job or job['stage'] != 'EVALUATING':
            raise Denied('COLLECTOR_STAGE')
        # There is no public API accepting these fields. Approval/exact source is
        # read from the existing server-owned approved record, not Runtime output.
        cfg = row['approved']['agentcore_evaluation']
        grant = row['collection_grant']
        if (grant['approval_digest'] != digest(row['approved'])
                or grant['binding'] != binding(row)
                or grant['input_digest'] != digest(row['collection_input'])
                or [c['case_id'] for c in grant['cases']] != cfg['case_ids']):
            raise Denied('COLLECTOR_EXPORT_GRANT')
        source = runs.get(db, cfg['source_record_key'])
        if (not cfg['source_record_key'].startswith('foundation-evaluator:')
                or digest(source) != cfg['source_record_digest']
                or source.get('native') != cfg['native']
                or cfg['rubric_digest'] != binding(row)['rubric_digest']
                or cfg['dataset_digest'] != binding(row)['dataset_digest']):
            raise Denied('COLLECTOR_APPROVAL')
        return row, {**cfg, 'cases': grant['cases']}, digest([binding(row), cfg, source, grant])

    def collect(self, store, job_id):
        key = 'foundation-collect:' + job_id
        with store.tx() as db:
            row, cfg, fence = self.context(db, job_id)
            old = runs.get(db, key)
            if old:
                if old['fence'] == fence and old['status'] == 'CLAIMED':
                    raise CollectionInFlight('COLLECTION_UNCERTAIN_NO_RETRY')
                if old['fence'] != fence or old['status'] != 'COMPLETE' or not row.get('evidence_source'):
                    raise Denied('COLLECTION_UNCERTAIN_NO_RETRY')
                return row  # exact committed idempotent repeat; no Evaluate replay
            if 'codeBased' in cfg['native']['evaluatorConfig']:
                from .code_evaluator import CodeEvaluator
                if (type(self.code_evaluator) is not CodeEvaluator
                        or self.code_evaluator.store is not store
                        or self.code_evaluator.s3 is not self.s3
                        or (self.code_evaluator.bucket, self.code_evaluator.prefix) != (self.bucket, self.prefix)):
                    raise Denied('CODE_EVALUATOR_EVENT_REFERENCE_MAPPING_UNVERIFIED')
                self.code_evaluator.register(db, row, cfg, fence)
            runs.put(db, key, {'status': 'CLAIMED', 'fence': fence})
        # Claim commits before all external operations. A lost result never repeats
        # a paid synchronous Evaluate (which has no idempotency token).
        expected = binding(row)
        if self.s3.get_bucket_versioning(Bucket=self.bucket).get('Status') != 'Enabled':
            raise Denied('VERSIONED_EVIDENCE_REQUIRED')
        block = self.s3.get_public_access_block(Bucket=self.bucket)['PublicAccessBlockConfiguration']
        if not all(block.get(k) is True for k in ('BlockPublicAcls', 'IgnorePublicAcls', 'BlockPublicPolicy', 'RestrictPublicBuckets')):
            raise Denied('PRIVATE_EVIDENCE_REQUIRED')
        native = snapshot(self.control.get_evaluator(evaluatorId=cfg['native']['evaluatorId']))
        if native != cfg['native'] or native['status'] != 'ACTIVE' or native['level'] not in ('TRACE', 'TOOL_CALL'):
            raise Denied('EVALUATOR_CONFIG_CHANGED')
        if cfg['purpose'] == 'report_quality' and native['level'] != 'TRACE':
            raise Denied('REPORT_REQUIRES_TRACE')
        is_code = 'codeBased' in native['evaluatorConfig']
        if is_code and not self.code_evaluator.configured(cfg):
            raise Denied('CODE_EVALUATOR_EVENT_REFERENCE_MAPPING_UNVERIFIED')
        content = self.span_reader(row)
        if cfg['purpose'] == 'report_quality':
            # Native TRACE input must contain actual report/source text, not only
            # hashes. Do not inject manufactured spans or unsupported API fields.
            serialized = canonical(content['sessionSpans']).decode()
            texts = [content['report']['text']] + [x['text'] for x in content['sources']]
            if any(json.dumps(t, ensure_ascii=False)[1:-1] not in serialized for t in texts):
                raise Denied('REPORT_CONTENT_NOT_IN_ACTUAL_TRACE')
        cases = cfg['cases']
        if not 1 <= len(cases) <= 20 or len({c['case_id'] for c in cases}) != len(cases):
            raise Denied('CASE_COVERAGE')
        receipt = {'pipeline': 'AGENTCORE_EVALUATE_V1', 'pipeline_run_id': fence,
            'binding': expected, 'evaluator_id': native['evaluatorId'], 'evaluator_version': digest(native),
            'native_evaluator_config': native, 'level': native['level'],
            'rubric_digest': cfg['rubric_digest'], 'dataset_digest': cfg['dataset_digest'],
            'input_pin': row['collection_input'], 'approval_digest': digest(row['approved']),
            'evaluator_digest': digest(cfg), 'grant_digest': digest(row['collection_grant']), 'cases': []}
        if is_code:
            receipt['code_adapter'] = cfg['code_adapter']
        for case in cases:
            spans = content['sessionSpans']
            if not any(s['traceId'] == case['trace_id'] and s['spanId'] == case['span_id'] for s in spans):
                raise Denied('CASE_TARGET')
            request = {'evaluatorId': native['evaluatorId'], 'evaluationInput': {'sessionSpans': spans},
                'evaluationTarget': ({'traceIds': [case['trace_id']]} if native['level'] == 'TRACE'
                                     else {'spanIds': [case['span_id']]})}
            validate_parameters(request, self.agentcore.meta.service_model.operation_model('Evaluate').input_shape)
            try:
                response = self.agentcore.evaluate(**request)
            except ClientError as error:
                persist(self.s3, self.bucket, self.prefix, expected, 'native-error',
                        {'binding': expected, 'request': request, 'response': error.response})
                raise Denied('EVALUATION_SDK_ERROR') from None
            item = {**case, 'evaluate_request': request, 'evaluate_response': response,
                    'request_id': response.get('ResponseMetadata', {}).get('RequestId'), 'status': 'INCOMPLETE'}
            receipt['cases'].append(item)
            # Persist the exact native error/incomplete body as well as successes.
            persist(self.s3, self.bucket, self.prefix, expected, 'native-' + str(len(receipt['cases'])),
                    {'binding': expected, 'request': request, 'response': response})
            item['status'] = decision(response, request, case, native, cfg['decision'])
            if is_code:
                with store.tx() as db:
                    cached = self.code_evaluator.cached(db, native['evaluatorId'], case['trace_id'])
                result = response['evaluationResults'][0]
                if any(result.get(k) != cached[k] for k in ('label', 'value')):
                    raise Denied('CODE_NATIVE_RESULT_MISMATCH')
        pin = persist(self.s3, self.bucket, self.prefix, expected, 'receipt', receipt)
        spec = {**pin, 'receipt_id': pin['key'], 'receipt_digest': digest(receipt),
                'id': cfg['id'], 'required': len(cases), 'case_ids': [c['case_id'] for c in cases],
                'evaluator_id': native['evaluatorId'], 'evaluator_version': digest(native)}
        evidence = {k: copy.deepcopy(content[k]) for k in ('binding', 'report', 'source_ids', 'trace_ids')}
        if 'provider' in content:
            evidence['provider'] = copy.deepcopy(content['provider'])
        evidence['evaluations'] = [{'id': cfg['id'], 'status': 'FAIL' if any(c['status'] == 'FAIL' for c in receipt['cases']) else 'PASS',
                                   'completed': len(cases), 'required': len(cases), 'judge_complete': not is_code}]
        descriptor = {'binding': expected, 'source_ids': content['source_ids'],
                      'query_id': row['collection_input']['trace_query_id'], 'evaluations': [spec]}
        staged = {**row, 'evidence_source': descriptor}
        checked(staged, {**evidence, 'readback': 'SERVER_READBACK_V1', 'agentcore_receipts': [receipt]})
        descriptor.update(persist(self.s3, self.bucket, self.prefix, expected, 'result', evidence))
        with store.tx() as db:
            current, _, now = self.context(db, job_id)
            if now != fence or runs.get(db, key) != {'status': 'CLAIMED', 'fence': fence} or current.get('evidence_source'):
                raise Denied('COLLECTOR_CAS_CONFLICT')
            current['evidence_source'] = descriptor
            runs.put(db, 'foundation-run:' + job_id, current)
            runs.put(db, key, {'status': 'COMPLETE', 'fence': fence})
        return current


def configured_evidence(settings, *, agentcore=None, control=None, s3=None, cloudwatch=None, code_evaluator=None):
    """Disabled by default; explicit server dependencies and exact store only."""
    if settings.get('enabled') is not True:
        return None, None
    if settings.get('span_contract') != 'PINNED_COMPLETE_SPANS_V1' or any(x is None for x in (agentcore, control, s3, cloudwatch)):
        raise Denied('COLLECTOR_DEPENDENCIES_REQUIRED')
    bucket, prefix = settings['bucket'], settings['prefix']
    span_reader = PinnedSpanReader(s3=s3, cloudwatch=cloudwatch, bucket=bucket, prefix=prefix)
    repository = AgentCoreEvidenceRepository(s3=s3, bucket=bucket, prefix=prefix)
    return (EvaluationCollector(agentcore=agentcore, control=control, s3=s3, span_reader=span_reader, bucket=bucket, prefix=prefix, code_evaluator=code_evaluator),
            EvidenceReader(s3=s3, cloudwatch=cloudwatch, agentcore_evidence=repository, bucket=bucket, prefix=prefix))
