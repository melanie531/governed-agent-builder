"""Worker-only run -> Logs readback -> versioned private S3 -> CAS export grant.

CloudWatch JSON format: AWS supported-frameworks-strands public span examples.
Native private attributes: AWS supported-frameworks-generic. No synthetic spans.
"""
import copy
import json
import re
import time

from foundation_harness.config import digest, canonical
from foundation_harness.context import Denied
from . import foundation_runs as runs
from .evaluation_collector import persist, read_pinned, CollectionInFlight
from .result_evidence import binding, Report

FORMAT = 'CLOUDWATCH_SPAN_JSON_V1'
SCOPE = 'opentelemetry.instrumentation.owned_foundation'


class ExportWaiting(CollectionInFlight):
    pass


def normalize(value):
    """Documented flattened CloudWatch span JSON, not OTLP protobuf JSON."""
    if not isinstance(value, dict) or len(canonical(value)) > 65536:
        raise Denied('SPAN_FORMAT')
    for key, size in (('traceId', 32), ('spanId', 16)):
        if not re.fullmatch('[a-f0-9]{' + str(size) + '}', value.get(key, '')):
            raise Denied('SPAN_ID')
    if (not isinstance(value.get('attributes'), dict) or not isinstance(value.get('scope'), dict)
            or type(value.get('startTimeUnixNano')) is not int
            or type(value.get('endTimeUnixNano')) is not int
            or value['startTimeUnixNano'] >= value['endTimeUnixNano']
            or value.get('droppedAttributesCount', 0) or value.get('droppedEventsCount', 0)
            or value.get('droppedLinksCount', 0)):
        raise Denied('SPAN_FORMAT_OR_TRUNCATED')
    return copy.deepcopy(value)


def execution(row):
    response = row['response']
    if (row.get('state') != 'FINISHED' or row.get('settled') is not True
            or not row.get('runtime_request_id')
            or digest(response) != row.get('response_digest')
            or response.get('execution_status') != 'EXECUTION_SUCCEEDED'):
        raise Denied('EXECUTION_PROOF_REQUIRED')
    record = response['execution_record']
    spans = [normalize(s) for s in record['spans']]
    ids = [s['spanId'] for s in spans]
    if (not 1 <= len(ids) <= 100 or len(set(ids)) != len(ids)
            or any(s['traceId'] != response['trace_id'] or s['scope']['name'] != SCOPE
                   or s['attributes'].get('session.id') != row['run_ref'].ljust(33, '0') for s in spans)
            or set(record['content']) != set(ids)):
        raise Denied('EXECUTION_SPAN_BINDING')
    roots = [s for s in spans if s['name'] == 'run']
    if len(roots) != 1 or roots[0].get('parentSpanId'):
        raise Denied('EXECUTION_ROOT_REQUIRED')
    root = roots[0]
    if (root['attributes'].get('foundation.manifest_digest') != row['manifest_digest']
            or root['attributes'].get('foundation.foundation_digest') != row['foundation_digest']
            or root['attributes'].get('foundation.status') != 'SUCCEEDED'):
        raise Denied('EXECUTION_MANIFEST_BINDING')
    for s in spans:
        if s is not root and (s.get('parentSpanId') != root['spanId']
                or not root['startTimeUnixNano'] <= s['startTimeUnixNano'] < s['endTimeUnixNano'] <= root['endTimeUnixNano']):
            raise Denied('UNSEEN_PARENT_OR_SPAN_WINDOW')
    if any(s['name'] not in ('run', 'model', 'tool') for s in spans):
        raise Denied('EXECUTION_OPERATION')
    for kind in ('model', 'tool'):
        selected = [s for s in spans if s['name'] == kind]
        count = sum(k.startswith(kind + ':') for k in row['calls'])
        if len(selected) != count or count != response[kind + '_calls']:
            raise Denied('EXECUTION_CALL_COVERAGE')
        if any(not s['attributes'].get('foundation.request_id') for s in selected):
            raise Denied('SDK_REQUEST_CORRELATION_REQUIRED')
    usage = row.get('usage')
    models = [s for s in spans if s['name'] == 'model']
    if usage is not None and any(usage.get(k) != sum(s['attributes'].get('foundation.' + k, -10000001) for s in models)
                                 for k in ('input_tokens', 'output_tokens')):
        raise Denied('PROVIDER_USAGE_CORRELATION')
    if response.get('usage') != row.get('usage'):
        raise Denied('PROVIDER_USAGE_MISMATCH')
    return spans, root


def query_spans(logs, query_id, row):
    result = logs.get_query_results(queryId=query_id)
    if result.get('status') in ('Running', 'Scheduled'):
        raise ExportWaiting('TRACE_QUERY_WAITING')
    if (result.get('status') != 'Complete' or result.get('nextToken')
            or len(result.get('results', [])) > 100
            or result.get('statistics', {}).get('recordsMatched', 0) > len(result.get('results', []))):
        raise Denied('TRACE_QUERY_FAILED_OR_TRUNCATED')
    spans, _ = execution(row)
    expected = {s['spanId']: s for s in spans}
    found = {}
    for record in result.get('results', []):
        fields = {f['field']: f['value'] for f in record}
        if len(fields) != len(record) or '@message' not in fields:
            raise Denied('TRACE_QUERY_FIELDS')
        actual = normalize(json.loads(fields['@message']))
        pin = expected.get(actual['spanId'])
        if not pin or actual['spanId'] in found:
            raise Denied('TRACE_QUERY_MIXED_OR_DUPLICATE')
        # CloudWatch may add resource/duration/status fields. All emitted fields
        # (including SDK request IDs, timestamps, parent and metadata) must match.
        if any(actual.get(k) != v for k, v in pin.items() if k != 'attributes') or any(
                actual['attributes'].get(k) != v for k, v in pin['attributes'].items()):
            raise Denied('TRACE_QUERY_CORRELATION')
        found[actual['spanId']] = actual
    if set(found) != set(expected):
        raise ExportWaiting('TRACE_COVERAGE_WAITING')
    return [found[s['spanId']] for s in spans]


def content_for(row, observed):
    _, root = execution(row)
    private = row['response']['execution_record']['content']
    output = row['response']['output']
    if any(set(b) != {'type', 'text'} or b['type'] != 'text' for b in output):
        raise Denied('REPORT_OUTPUT_FORMAT')
    text = '\n'.join(b['text'] for b in output)
    if private[root['spanId']].get('gen_ai.task.output') != text or private[root['spanId']].get('gen_ai.task.input') != row['stored_input']:
        raise Denied('REPORT_CONTENT_BINDING')
    # IDs are content-addressed local references, not fetched URLs or invented citations.
    sources = [{'id': 'input-' + digest(row['stored_input']), 'text': row['stored_input']}]
    spans = copy.deepcopy(observed)
    allow = {'run': {'gen_ai.task.input', 'gen_ai.task.output'},
             'model': {'gen_ai.input.messages', 'gen_ai.output.messages', 'gen_ai.system_instructions'},
             'tool': {'gen_ai.tool.name', 'gen_ai.tool.call.arguments', 'gen_ai.tool.call.result'}}
    for span in spans:
        attrs = private[span['spanId']]
        if set(attrs) != allow[span['name']] or any(not isinstance(v, str) for v in attrs.values()):
            raise Denied('PRIVATE_SPAN_CONTENT')
        # Attach content ONLY to its actually observed span in private S3/Evaluate.
        span['attributes'].update(attrs)
        if span['name'] == 'tool':
            sources.append({'id': 'tool-' + span['spanId'], 'text': attrs['gen_ai.tool.call.result']})
    if any(not s['text'].strip() for s in sources):
        raise Denied('SOURCE_CONTENT_EMPTY')
    report = Report(text=text, citations=[]).model_dump()
    value = {'binding': binding(row), 'report': report, 'sources': sources,
             'source_ids': [s['id'] for s in sources], 'trace_ids': [root['traceId']],
             'sessionSpans': spans, 'complete': True, 'truncated': False}
    if row.get('usage') is not None:
        model = row['approved']['config']['model']
        value['provider'] = {'route': model['route'], 'route_version': model['version'], 'usage': row['usage']}
    return value, root


class RunEvidenceExporter:
    def __init__(self, *, s3, cloudwatch, bucket, prefix):
        if s3 is None or cloudwatch is None or not bucket or not prefix.endswith('/'):
            raise Denied('EXPORT_DEPENDENCIES_REQUIRED')
        self.s3, self.logs, self.bucket, self.prefix = s3, cloudwatch, bucket, prefix

    def export(self, store, job_id, owner):
        key = 'foundation-run:' + job_id
        with store.tx() as db:
            row = runs.get(db, key)
            runs.current(db, row)
            self.owner(db, row, owner)
            spans, root = execution(row)
            fence = digest([binding(row), row['approved'], row['response_digest'], row['calls']])
            state = row.get('evidence_export')
            if state and state['fence'] != fence:
                raise Denied('EXPORT_FENCE_CHANGED')
            if state and state['status'] == 'COMPLETE':
                if row['collection_grant']['input_digest'] != digest(row['collection_input']):
                    raise Denied('EXPORT_GRANT_CHANGED')
                content = json.loads(read_pinned(self.s3, self.bucket, self.prefix, binding(row), row['collection_input']))
                if content['binding'] != binding(row):
                    raise Denied('EXPORT_CONTENT_BINDING')
                return row
            if state and state['status'] == 'STARTING':
                raise Denied('EXPORT_QUERY_START_UNCERTAIN')
            if state and (state['polls'] >= 12 or time.time() >= state['deadline']):
                raise Denied('TRACE_WAIT_EXHAUSTED')
            if not state:
                state = {'fence': fence, 'status': 'STARTING', 'polls': 0,
                         'deadline': min(row['deadline'], time.time() + 120)}
                row['evidence_export'] = state
                runs.put(db, key, row)
        if state['status'] == 'STARTING':
            # Actual exporter sends to this fixed x-aws-log-group, never Runtime URL input.
            query = self.logs.start_query(logGroupName='/governed-agent-builder/foundation-m0',
                startTime=root['startTimeUnixNano'] // 1000000000 - 1,
                endTime=root['endTimeUnixNano'] // 1000000000 + 2,
                queryString='fields @message | filter traceId = "' + root['traceId'] + '" | limit 101', limit=101)
            query_id = query['queryId']
            if not query_id:
                raise Denied('TRACE_QUERY_ID_REQUIRED')
            with store.tx() as db:
                current = runs.get(db, key); runs.current(db, current); self.owner(db, current, owner)
                if current.get('evidence_export') != state:
                    raise Denied('EXPORT_CAS')
                state = {**state, 'status': 'QUERYING', 'query_id': query_id}
                current['evidence_export'] = state; runs.put(db, key, current)
            raise ExportWaiting('TRACE_QUERY_WAITING')
        with store.tx() as db:
            current = runs.get(db, key); runs.current(db, current); self.owner(db, current, owner)
            if current.get('evidence_export') != state:
                raise Denied('EXPORT_CAS')
            state = {**state, 'polls': state['polls'] + 1}
            current['evidence_export'] = state; runs.put(db, key, current)
        observed = query_spans(self.logs, state['query_id'], row)
        content, root = content_for(row, observed)
        if self.s3.get_bucket_versioning(Bucket=self.bucket).get('Status') != 'Enabled':
            raise Denied('VERSIONED_EVIDENCE_REQUIRED')
        block = self.s3.get_public_access_block(Bucket=self.bucket)['PublicAccessBlockConfiguration']
        if not all(block.get(k) is True for k in ('BlockPublicAcls', 'IgnorePublicAcls', 'BlockPublicPolicy', 'RestrictPublicBuckets')):
            raise Denied('PRIVATE_EVIDENCE_REQUIRED')
        pin = persist(self.s3, self.bucket, self.prefix, binding(row), 'run-export', content)
        pin.update(binding=binding(row), source_ids=content['source_ids'], source_digest=digest(content['sources']),
                   span_ids=[[s['traceId'], s['spanId']] for s in observed], query_id=state['query_id'],
                   trace_query_id=state['query_id'], format=FORMAT)
        cfg = row['approved'].get('agentcore_evaluation')
        if cfg and (len(cfg['case_ids']) != 1 or cfg['native']['level'] != 'TRACE'):
            raise Denied('M0_ONE_TRACE_CASE_REQUIRED')
        grant = {'approval_digest': digest(row['approved']), 'binding': binding(row),
                 'input_digest': digest(pin), 'cases': [] if not cfg else [{'case_id': cfg['case_ids'][0],
                 'trace_id': root['traceId'], 'span_id': root['spanId'], 'session_id': job_id.ljust(33, '0')}]}
        with store.tx() as db:
            current = runs.get(db, key); runs.current(db, current); self.owner(db, current, owner)
            if (current.get('evidence_export') != state or current.get('response_digest') != row['response_digest']
                    or digest([binding(current), current['approved'], current['response_digest'], current['calls']]) != fence
                    or current.get('collection_input') or current.get('collection_grant')):
                raise Denied('EXPORT_CAS')
            current.update(collection_input=pin, collection_grant=grant,
                           evidence_export={**state, 'status': 'COMPLETE'})
            if not cfg:
                current['evidence_source'] = {'kind': 'RUN_EXPORT_ONLY', 'binding': binding(row), 'input_digest': digest(pin)}
            runs.put(db, key, current)
        return current

    @staticmethod
    def owner(db, row, owner):
        job = db.select('jobs', where=[('id', '=', row['run_ref'])]).fetchone()
        if (not owner or not job or job['stage'] != 'EVALUATING'
                or runs.get(db, 'foundation-evaluating:' + row['run_ref']) != owner
                or owner.get('expires', 0) <= time.time()):
            raise Denied('EXPORT_WORKER_LEASE_REQUIRED')
