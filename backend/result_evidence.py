"""Bounded result readback and public projection. No discovery, SDK construction or writes.

Evidence descriptors MUST be written by a trusted server-side collector, never
copied from Runtime responses. This module is intentionally not auto-configured.
"""

import hashlib
import json
import re
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from foundation_harness.config import digest


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Citation(Strict):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')
    title: str = Field(min_length=1, max_length=300)
    source_id: str = Field(pattern=r'^[A-Za-z0-9_.:-]{1,160}$')


class Report(Strict):
    text: str = Field(min_length=1, max_length=24000)
    citations: list[Citation] = Field(max_length=40)


class Evaluation(Strict):
    id: str = Field(pattern=r'^[A-Za-z0-9_.:/-]{1,240}$')
    status: Literal['PASS', 'FAIL', 'INCOMPLETE']
    completed: int = Field(ge=0, le=100)
    required: int = Field(ge=1, le=100)
    judge_complete: bool


class Usage(Strict):
    input_tokens: int = Field(ge=0, le=10000000)
    output_tokens: int = Field(ge=0, le=10000000)
    cached_read_tokens: int | None = Field(default=None, ge=0, le=10000000)
    cached_write_tokens: int | None = Field(default=None, ge=0, le=10000000)


def binding(row):
    cfg = row['approved']['config']
    return {**{k: row[k] for k in ('run_ref', 'owner', 'workspace', 'agent', 'version',
                                  'definition_digest', 'manifest_digest', 'epoch')},
            'runtime_arn': row['runtime']['runtime_arn'],
            'runtime_version': row['runtime']['runtime_version'],
            'policy_version': row['approved']['policy_version'],
            'dataset_digest': cfg['evaluation']['dataset']['digest'],
            'rubric_digest': cfg['evaluation']['rubric']['digest']}


class AgentCoreEvidenceRepository:
    """Fixed-store S3 repository, scoped to a protected job descriptor per read.

    No clients are constructed here. Synthetic subclasses remain usable offline.
    """
    def __init__(self, *, s3, bucket, prefix, row=None):
        if not bucket or not prefix or not prefix.endswith('/'):
            raise ValueError('FIXED_EVIDENCE_STORE_REQUIRED')
        self.s3, self.bucket, self.prefix, self.row = s3, bucket, prefix, row

    def for_row(self, row):
        return AgentCoreEvidenceRepository(s3=self.s3, bucket=self.bucket,
                                           prefix=self.prefix, row=row)

    def read_verified_receipt(self, *, receipt_id: str, version_id: str) -> bytes:
        row = self.row
        src = row['evidence_source']
        expected = binding(row)
        if src['binding'] != expected:
            raise ValueError('RECEIPT_GRANT')
        grants = [p for p in src['evaluations'] if p['receipt_id'] == receipt_id
                  and p['version_id'] == version_id]
        if len(grants) != 1:
            raise ValueError('RECEIPT_GRANT')
        pin = grants[0]
        from .evaluation_collector import read_pinned
        return read_pinned(self.s3, self.bucket, self.prefix, expected,
                           {**pin, 'key': receipt_id})


def validate_receipts(row, content):
    """Normalized contract only; provenance depends on the trusted collector/store.

    Native Evaluate has results, not job completion or an evaluator version field.
    evaluator_version is the digest of the collector's immutable native config
    snapshot, not an invented AWS version. Rubric mapping is collector-owned.
    """
    specs = row['evidence_source']['evaluations']
    receipts = content['agentcore_receipts']
    if not 1 <= len(specs) <= 20 or len(receipts) != len(specs):
        raise ValueError('AGENTCORE_RECEIPTS')
    summaries = []
    for spec, receipt in zip(specs, receipts):
        if (not spec['receipt_id'] or not spec['version_id']
                or digest(receipt) != spec['receipt_digest']
                or receipt['binding'] != binding(row)
                or receipt['pipeline'] != 'AGENTCORE_EVALUATE_V1'
                or not receipt['pipeline_run_id']
                or receipt['evaluator_id'] != spec['evaluator_id']
                or receipt['evaluator_version'] != spec['evaluator_version']
                or digest(receipt['native_evaluator_config']) != spec['evaluator_version']
                or receipt['native_evaluator_config']['evaluatorId'] != spec['evaluator_id']
                or receipt['rubric_digest'] != binding(row)['rubric_digest']
                or receipt['dataset_digest'] != binding(row)['dataset_digest']):
            raise ValueError('AGENTCORE_PIPELINE_BINDING')
        cases = receipt['cases']
        if (not 1 <= len(cases) <= 100 or len(cases) != spec['required']
                or len({c['case_id'] for c in cases}) != len(cases)
                or [c['case_id'] for c in cases] != spec['case_ids']):
            raise ValueError('AGENTCORE_COVERAGE')
        for case in cases:
            request, response = case['evaluate_request'], case['evaluate_response']
            if (not case['request_id'] or case['trace_id'] not in content['trace_ids']
                    or not re.fullmatch(r'[a-f0-9]{16}', case['span_id'])
                    or request['evaluatorId'] != spec['evaluator_id']
                    or request['evaluationTarget'] != (
                        {'traceIds': [case['trace_id']]} if receipt.get('level') == 'TRACE'
                        else {'spanIds': [case['span_id']]})
                    or not request['evaluationInput']['sessionSpans']
                    or case['status'] not in ('PASS', 'FAIL')
                    or not response['evaluationResults']):
                raise ValueError('AGENTCORE_NATIVE_RESULT')
            # Scores/labels must come from actual native results; PASS is the
            # collector's pinned rubric decision, never a Runtime assertion.
            for result in response['evaluationResults']:
                context = result['context']['spanContext']
                if (result['evaluatorId'] != spec['evaluator_id']
                        or (receipt.get('level') != 'TRACE' and context.get('spanId') != case['span_id'])
                        or context['traceId'] != case['trace_id']
                        or not context['sessionId']
                        or result.get('errorCode') or result.get('errorMessage')
                        or (result.get('value') is None and result.get('label') is None)):
                    raise ValueError('AGENTCORE_NATIVE_RESULT')
        summaries.append({'id': spec['id'], 'status': 'FAIL' if any(
            c['status'] == 'FAIL' for c in cases) else 'PASS',
            'completed': len(cases), 'required': spec['required'],
            'judge_complete': 'codeBased' not in receipt['native_evaluator_config']['evaluatorConfig']})
    if len({x['id'] for x in summaries}) != len(summaries) or content['evaluations'] != summaries:
        raise ValueError('AGENTCORE_SUMMARY')


def checked(row, evidence):
    """Validate fetched content, not Runtime ready flags. Unknown fields never project."""
    expected = binding(row)
    if not isinstance(evidence, dict) or evidence.get('binding') != expected:
        raise ValueError('BINDING')
    if set(evidence) - {'binding', 'report', 'source_ids', 'trace_ids', 'provider', 'evaluations', 'readback', 'agentcore_receipts'}:
        raise ValueError('EVIDENCE_FIELDS')
    if evidence.get('readback') != 'SERVER_READBACK_V1':
        raise ValueError('READBACK')
    export_only = row.get('evidence_source', {}).get('kind') == 'RUN_EXPORT_ONLY'
    if export_only:
        pin = row['collection_input']
        grant = row['collection_grant']
        if (row['evidence_source']['input_digest'] != digest(pin)
                or grant['input_digest'] != digest(pin) or grant['binding'] != expected
                or grant['approval_digest'] != digest(row['approved'])
                or evidence['evaluations'] or evidence['agentcore_receipts']):
            raise ValueError('EXPORT_ONLY_GRANT')
    else:
        validate_receipts(row, evidence)
    if any('codeBased' in r['native_evaluator_config']['evaluatorConfig'] for r in evidence['agentcore_receipts']):
        from .code_evaluator import ADAPTER
        if any(r.get('code_adapter') != ADAPTER
               for r in evidence['agentcore_receipts']
               if 'codeBased' in r['native_evaluator_config']['evaluatorConfig']):
            raise ValueError('DETERMINISTIC_ADAPTER_REQUIRED')
    report = Report.model_validate(evidence['report'])
    traces = evidence['trace_ids']
    if (not isinstance(traces, list) or not 1 <= len(traces) <= 20
            or any(not isinstance(t, str) or not re.fullmatch(r'[a-f0-9]{32}', t) for t in traces)
            or len(set(traces)) != len(traces)
            or row.get('response', {}).get('trace_id') not in traces):
        raise ValueError('TRACE')
    evaluations = [Evaluation.model_validate(e) for e in evidence['evaluations']]
    if (not (0 if export_only else 1) <= len(evaluations) <= 20 or len({e.id for e in evaluations}) != len(evaluations)
            or any(e.completed != e.required
                   or e.status == 'INCOMPLETE' for e in evaluations)):
        raise ValueError('EVALUATION_INCOMPLETE')
    return report, evaluations, traces


class EvidenceReader:
    """Only exact server-registered S3 object versions and existing Logs query IDs.

    AgentCore evaluation evidence is an explicitly injected repository contract.
    The opt-in collector factory supplies the concrete S3 receipt repository.
    """
    def __init__(self, *, s3, cloudwatch, agentcore_evidence, bucket, prefix):
        if not isinstance(agentcore_evidence, AgentCoreEvidenceRepository):
            raise TypeError('AGENTCORE_EVIDENCE_REPOSITORY_REQUIRED')
        self.s3, self.cloudwatch = s3, cloudwatch
        self.agentcore_evidence = agentcore_evidence
        self.bucket, self.prefix = bucket, prefix

    def __call__(self, row):
        try:
            src = row['evidence_source']
            expected = binding(row)
            if src['binding'] != expected or not self.prefix or not self.prefix.endswith('/'):
                raise ValueError('SOURCE_BINDING')
            if src.get('kind') == 'RUN_EXPORT_ONLY':
                from .evaluation_collector import PinnedSpanReader
                content = PinnedSpanReader(s3=self.s3, cloudwatch=self.cloudwatch, bucket=self.bucket, prefix=self.prefix)(row)
                evidence = {k: content[k] for k in ('binding', 'report', 'source_ids', 'trace_ids')}
                if 'provider' in content:
                    evidence['provider'] = content['provider']
                evidence.update(evaluations=[], agentcore_receipts=[], readback='SERVER_READBACK_V1')
                checked(row, evidence)
                return evidence
            key = src['key']
            if not key.startswith(self.prefix) or '..' in key.split('/') or '://' in key:
                raise ValueError('SOURCE_KEY')
            response = self.s3.get_object(Bucket=self.bucket, Key=key, VersionId=src['version_id'])
            stream = response['Body']
            try:
                raw = stream.read(65537)
            finally:
                stream.close()
            if (len(raw) > 65536 or response.get('VersionId') != src['version_id']
                    or response.get('Metadata', {}).get('binding-digest') != digest(expected)
                    or hashlib.sha256(raw).hexdigest() != src['sha256']):
                raise ValueError('CONTENT_BINDING')
            content = json.loads(raw)
            # A fetched document cannot grant itself readback authority.
            content.pop('readback', None)
            if content.get('source_ids') != src['source_ids']:
                raise ValueError('SOURCE_IDS')
            if row.get('collection_input', {}).get('format') == 'CLOUDWATCH_SPAN_JSON_V1':
                from .run_evidence_exporter import query_spans
                actual = query_spans(self.cloudwatch, src['query_id'], row)
                if {s['traceId'] for s in actual} != set(content['trace_ids']):
                    raise ValueError('TRACE_CONTENT')
            else:
                query = self.cloudwatch.get_query_results(queryId=src['query_id'])
                if query.get('status') != 'Complete' or query.get('nextToken') or not 1 <= len(query['results']) <= 20:
                    raise ValueError('TRACE_QUERY')
                traces = []
                for record in query['results']:
                    fields = {x['field']: x['value'] for x in record}
                    if len(fields) != len(record) or fields.get('binding_digest') != digest(expected):
                        raise ValueError('TRACE_BINDING')
                    traces.append(fields['trace_id'])
                if set(traces) != set(content['trace_ids']):
                    raise ValueError('TRACE_CONTENT')
            if not 1 <= len(src['evaluations']) <= 20:
                raise ValueError('EVALUATION_CAP')
            # Discard all inline receipt claims. Fetch only server-pinned objects.
            content['agentcore_receipts'] = []
            repository = (self.agentcore_evidence.for_row(row)
                          if type(self.agentcore_evidence) is AgentCoreEvidenceRepository
                          else self.agentcore_evidence)
            for spec in src['evaluations']:
                raw_receipt = repository.read_verified_receipt(
                    receipt_id=spec['receipt_id'], version_id=spec['version_id'])
                if (not isinstance(raw_receipt, bytes) or len(raw_receipt) > 65536
                        or hashlib.sha256(raw_receipt).hexdigest() != spec['sha256']):
                    raise ValueError('AGENTCORE_RECEIPT_CONTENT')
                content['agentcore_receipts'].append(json.loads(raw_receipt))
            content['readback'] = 'SERVER_READBACK_V1'
            checked(row, content)
            return content
        except Exception:
            # Never persist provider exception strings, private prompts or SDK bodies.
            return None


def cost_projection(row, evidence):
    result = {'status': 'UNKNOWN', 'estimated_model_inference_cost': None,
              'usage': None, 'currency': None, 'pricing_source': None, 'pricing_date': None,
              'rate_version': None, 'model_route': None, 'model_route_version': None,
              'unallocated': ['Runtime', 'Browser', 'tools', 'storage', 'telemetry', 'evaluation'],
              'scope': 'Model inference estimate only; not an invoice or all-service total.'}
    try:
        provider = evidence['provider']
        model = row['approved']['config']['model']
        if (provider['route'], provider['route_version']) != (model['route'], model['version']):
            return result
        usage = Usage.model_validate(provider['usage']).model_dump(exclude_none=True)
        result.update(usage=usage, model_route=model['route'], model_route_version=model['version'])
        rate = row['approved']['model_rate_schedule']
        if (rate['route'], rate['route_version']) != (model['route'], model['version']):
            return result
        if (rate['token_basis'] != 'exclusive' or not rate['version'] or not rate['source']
                or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', rate['date'])
                or not re.fullmatch(r'[A-Z]{3}', rate['currency'])):
            return result
        # Optional cache dimensions cannot silently disappear when rate semantics require them.
        if set(rate['per_million']) != set(usage):
            return result
        rates = {k: Decimal(str(v)) for k, v in rate['per_million'].items()}
        if any(not v.is_finite() or v < 0 for v in rates.values()):
            return result
        amount = sum(Decimal(usage[k]) * rates[k] for k in usage) / Decimal(1000000)
        result.update(status='ESTIMATED', estimated_model_inference_cost=str(amount),
                      currency=rate['currency'], pricing_source=rate['source'], pricing_date=rate['date'],
                      rate_version=rate['version'])
    except Exception:
        pass
    return result


def citation_quality(report, source_ids):
    ids = [c.id for c in report.citations]
    references = set(re.findall(r'\[([^\[\]\n]+)\]', report.text))
    return (len(set(ids)) == len(ids) and references <= set(ids)
            and all(c.source_id in source_ids for c in report.citations))


def project(row, evidence):
    base = {'schema_version': 'result-evidence-v1', 'source': 'live', 'status': 'BLOCKED',
            'reason': 'EVIDENCE_UNAVAILABLE', 'quality_status': 'UNKNOWN',
            'required_judge_passed': False, 'citation_status': 'UNKNOWN', 'report': None, 'evaluations': [], 'trace_ids': [],
            'cost': cost_projection(row, {}), 'version': row['version'],
            'definition_digest': row['definition_digest'], 'execution_status': 'UNKNOWN'}
    try:
        report, evaluations, traces = checked(row, evidence)
        citations_valid = citation_quality(report, evidence['source_ids'])
        judge_passed = bool(evaluations) and all(e.judge_complete and e.status == 'PASS' for e in evaluations)
        quality = ('FAIL' if not citations_valid or any(e.status == 'FAIL' for e in evaluations)
                   else 'PASS' if judge_passed else 'INCOMPLETE')
        display_report = report.model_dump()
        if not citations_valid:
            display_report['citations'] = []  # never endorse invalid source references
        base.update(status='AVAILABLE',
                    reason=('INVALID_CITATIONS' if not citations_valid else
                            'SEMANTIC_JUDGE_NOT_RUN' if not evaluations or any(not e.judge_complete for e in evaluations) else None),
                    quality_status=quality, required_judge_passed=judge_passed,
                    citation_status='VALID' if citations_valid else 'INVALID', report=display_report,
                    evaluations=[e.model_dump() for e in evaluations], trace_ids=traces,
                    cost=cost_projection(row, evidence),
                    execution_status=('EXECUTION_SUCCEEDED' if row.get('state') == 'FINISHED'
                                      and row.get('settled') is True else 'UNKNOWN'))
    except Exception:
        pass
    return base
