"""Offline-capable deterministic AgentCore TRACE adapter; no SDK/client creation.

The injected service boundary authenticates invocation context, NEVER event fields.
No default Lambda entry point is exported: deployment/IAM authentication is not
verified. Only protected collector registrations can grant immutable content reads.
"""
import copy
import json
import re

from foundation_harness.config import canonical, digest
from foundation_harness.context import Denied
from . import foundation_runs as runs
from .result_evidence import Report, binding

ADAPTER = 'DETERMINISTIC_TRACE_V1'
MAX_EVENT_BYTES = 6 * 1024 * 1024


def mapping_key(evaluator_id, trace_id):
    return 'foundation-code-evaluator:' + digest([evaluator_id, trace_id])


def span_ids(spans):
    if not isinstance(spans, list) or not 1 <= len(spans) <= 100:
        raise Denied('MALFORMED_SPANS')
    ids = []
    for span in spans:
        if (not isinstance(span, dict)
                or not isinstance(span.get('traceId'), str)
                or not re.fullmatch('[a-f0-9]{32}', span['traceId'])
                or not isinstance(span.get('spanId'), str)
                or not re.fullmatch('[a-f0-9]{16}', span['spanId'])):
            raise Denied('MALFORMED_SPANS')
        ids.append([span['traceId'], span['spanId']])
    if len({tuple(x) for x in ids}) != len(ids):
        raise Denied('MALFORMED_SPANS')
    return ids


class CodeEvaluator:
    """Concrete handler + protected registration, sharing collector's CAS store.

    authenticate_service(context) must return True only for an authenticated
    service entry. It receives no event and has no permissive default. A plain
    Lambda context is NOT authenticated identity. Trusted deployment supplies
    this boundary only after establishing caller restrictions independently.
    """
    def __init__(self, *, store, s3, bucket, prefix, authenticate_service=None):
        if store is None or s3 is None or not bucket or not prefix.endswith('/'):
            raise Denied('CODE_DEPENDENCIES_REQUIRED')
        self.store, self.s3, self.bucket, self.prefix = store, s3, bucket, prefix
        self.authenticate_service = authenticate_service

    def configured(self, cfg):
        native = cfg['native']
        return (callable(self.authenticate_service)
                and cfg.get('code_adapter') == ADAPTER and native['level'] == 'TRACE'
                and set(native['evaluatorConfig']) == {'codeBased'}
                and cfg.get('decision') == {'kind': 'categorical', 'labels': ['PASS', 'FAIL'],
                                            'pass_labels': ['PASS']})

    def register(self, db, row, cfg, fence):
        """Called in the SAME transaction as collector CLAIM, before Evaluate."""
        if not self.configured(cfg):
            raise Denied('CODE_EVALUATOR_EVENT_REFERENCE_MAPPING_UNVERIFIED')
        cases = cfg['cases']
        if (not 1 <= len(cases) <= 20
                or len({c['case_id'] for c in cases}) != len(cases)
                or len({c['trace_id'] for c in cases}) != len(cases)):
            raise Denied('AMBIGUOUS_TARGET')
        for case in cases:
            if (not isinstance(case['session_id'], str) or not case['session_id']
                    or [case['trace_id'], case['span_id']] not in row['collection_input']['span_ids']):
                raise Denied('CASE_TARGET')
            record = {'adapter': ADAPTER, 'job_id': row['run_ref'], 'binding': binding(row),
                      'fence': fence, 'approval_digest': digest(row['approved']), 'evaluator_digest': digest(cfg), 'case': copy.deepcopy(case),
                      'native': copy.deepcopy(cfg['native']),
                      'input_pin': copy.deepcopy(row['collection_input'])}
            key = mapping_key(cfg['native']['evaluatorId'], case['trace_id'])
            # Never overwrite another job/version/session, or reopen a used trace.
            if runs.get(db, key) is not None:
                raise Denied('CODE_MAPPING_CONFLICT')
            runs.put(db, key, record)

    def current(self, db, record):
        from .evaluation_collector import EvaluationCollector
        # Reuse the exact authority, source, approval and input checks, no SDK.
        row, cfg, fence = EvaluationCollector.context(self, db, record['job_id'])
        claim = runs.get(db, 'foundation-collect:' + record['job_id'])
        if (not self.configured(cfg) or record['adapter'] != ADAPTER
                or binding(row) != record['binding'] or fence != record['fence']
                or digest(row['approved']) != record['approval_digest']
                or digest(cfg) != record['evaluator_digest'] or cfg['native'] != record['native']
                or row['collection_input'] != record['input_pin']
                or record['case'] not in cfg['cases']
                or not claim or claim['fence'] != fence
                or claim['status'] not in ('CLAIMED', 'COMPLETE')):
            raise Denied('STALE_CODE_AUTHORITY')
        return row

    def cached(self, db, evaluator_id, trace_id):
        record = runs.get(db, mapping_key(evaluator_id, trace_id))
        if not record:
            raise Denied('CODE_MAPPING_REQUIRED')
        self.current(db, record)
        result = record.get('result')
        if not result:
            raise Denied('CODE_RESULT_REQUIRED')
        return copy.deepcopy(result)

    def handle(self, event, context=None):
        """Native response only; errors never contain private values or exceptions."""
        try:
            if not self.authenticate_service or self.authenticate_service(context) is not True:
                raise Denied('SERVICE_ENTRY_REQUIRED')
            if not isinstance(event, dict) or len(canonical(event)) > MAX_EVENT_BYTES:
                raise Denied('EVENT_SIZE_OR_SHAPE')
            if (event.get('schemaVersion') != '1.0' or event.get('evaluationLevel') != 'TRACE'
                    or not isinstance(event.get('evaluatorId'), str)
                    or not isinstance(event.get('evaluatorName'), str)
                    or set(event.get('evaluationTarget', {})) != {'traceIds'}):
                raise Denied('EVENT_SHAPE')
            targets = event['evaluationTarget']['traceIds']
            if (not isinstance(targets, list) or len(targets) != 1
                    or not isinstance(targets[0], str)
                    or not re.fullmatch('[a-f0-9]{32}', targets[0])):
                raise Denied('AMBIGUOUS_TARGET')
            spans = event['evaluationInput']['sessionSpans']
            ids = span_ids(spans)
            if {trace for trace, _ in ids} != {targets[0]}:
                raise Denied('AMBIGUOUS_TARGET')
            # References, URLs, buckets, owner claims are intentionally NEVER read.
            # They cannot select content or modify the deterministic rubric.
            key = mapping_key(event['evaluatorId'], targets[0])
            with self.store.tx() as db:
                record = runs.get(db, key)
                if not record:
                    raise Denied('CODE_MAPPING_REQUIRED')
                self.current(db, record)
                if (event['evaluatorName'] != record['native']['evaluatorName']
                        or ids != record['input_pin']['span_ids']):
                    raise Denied('CODE_TARGET_BINDING')
                event_digest = digest(spans)
                if record.get('result'):
                    if event_digest != record['spans_digest']:
                        raise Denied('CODE_SPANS_CHANGED')
                    return copy.deepcopy(record['result'])
            from .evaluation_collector import read_pinned
            content = json.loads(read_pinned(self.s3, self.bucket, self.prefix,
                                            record['binding'], record['input_pin']))
            pin = record['input_pin']
            if (content['binding'] != record['binding'] or content.get('complete') is not True
                    or content.get('truncated') is not False or content.get('nextToken')
                    or span_ids(content['sessionSpans']) != ids
                    or digest(content['sessionSpans']) != event_digest
                    or content['source_ids'] != pin['source_ids']
                    or digest(content['sources']) != pin['source_digest']
                    or [s['id'] for s in content['sources']] != pin['source_ids']
                    or not content['sources']
                    or len(set(pin['source_ids'])) != len(pin['source_ids'])
                    or any(not isinstance(s['text'], str) or not s['text'].strip() for s in content['sources'])
                    or targets[0] not in content['trace_ids']
                    or any(s.get('sessionId', record['case']['session_id']) != record['case']['session_id']
                           for s in spans)):
                raise Denied('INCOMPLETE_OR_UNBOUND_CONTENT')
            if (not isinstance(content.get('report'), dict)
                    or not isinstance(content['report'].get('text'), str)
                    or not content['report']['text'].strip()):
                raise Denied('INCOMPLETE_REPORT')
            serialized = canonical(content['sessionSpans']).decode()
            if any(json.dumps(text, ensure_ascii=False)[1:-1] not in serialized
                   for text in [content['report']['text']] + [s['text'] for s in content['sources']]):
                raise Denied('CONTENT_NOT_IN_TRACE')
            result = deterministic(content)
            with self.store.tx() as db:
                latest = runs.get(db, key)
                self.current(db, latest)
                if {k: v for k, v in latest.items() if k not in ('result', 'spans_digest')} != record:
                    raise Denied('CODE_MAPPING_CAS')
                if latest.get('result') and (latest['result'] != result or latest['spans_digest'] != event_digest):
                    raise Denied('CODE_RESULT_CONFLICT')
                latest.update(result=result, spans_digest=event_digest)
                runs.put(db, key, latest)
            return result
        except Exception:
            # No raw sources, prompt, event or exception strings in logs/responses.
            return {'errorCode': 'CODE_EVALUATION_DENIED',
                    'errorMessage': 'Authenticated mapping and complete immutable TRACE content required.'}


def deterministic(content):
    """Structural/reference checks, NOT semantic grounding or an LLM judge."""
    try:
        report = Report.model_validate(content['report'])
        citations = {c.id: c for c in report.citations}
        references = set(re.findall(r'\[([^\[\]\n]+)\]', report.text))
        valid = (bool(report.text.strip()) and bool(references)
                 and len(citations) == len(report.citations)
                 and references == set(citations)
                 and all(c.source_id in content['source_ids'] for c in report.citations))
    except Exception:
        valid = False
    return {'label': 'PASS' if valid else 'FAIL', 'value': 1.0 if valid else 0.0,
            'explanation': 'Deterministic reference, citation and report-format checks only; semantic judge unavailable.'}
