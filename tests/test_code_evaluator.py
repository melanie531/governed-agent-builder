"""Synthetic only: injected immutable S3 responses/Moto and blocked sockets."""
import copy
import io
import socket
from types import SimpleNamespace

import pytest
from backend import foundation_runs as runs
from backend.code_evaluator import ADAPTER, CodeEvaluator
from backend.evaluation_collector import snapshot
from backend.result_evidence import checked, project, validate_receipts
from foundation_harness.config import digest
from tests.test_evaluation_collector import setup, response, rewrite_input, read_pinned, seal_grant


@pytest.fixture(autouse=True)
def no_sockets(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError('NETWORK_FORBIDDEN')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket.socket, 'connect_ex', denied)
    monkeypatch.setattr(socket, 'create_connection', denied)


@pytest.fixture
def code(setup):
    s = setup
    s.native['evaluatorConfig'] = {'codeBased': {'lambdaConfig': {
        'lambdaArn': 'arn:aws:lambda:us-west-2:' + '1234' + '56789012:function:synthetic'}}}
    s.boundary = object()  # service-entry capability, NOT a user token or event field
    s.handler = CodeEvaluator(store=s.store, s3=s.s3, bucket=s.bucket, prefix='evidence/',
                              authenticate_service=lambda ctx: ctx is s.boundary)
    with s.store.tx() as db:
        row = runs.get(db, 'foundation-run:' + s.row['run_ref'])
        cfg = row['approved']['agentcore_evaluation']
        cfg.update(native=snapshot(s.native), code_adapter=ADAPTER,
                   decision={'kind':'categorical','labels':['PASS','FAIL'],'pass_labels':['PASS']})
        source = {'native':cfg['native']}
        cfg['source_record_digest'] = digest(source)
        runs.put(db, cfg['source_record_key'], source)
        seal_grant(row)
        runs.put(db, 'foundation-run:' + row['run_ref'], row)
    s.collector.code_evaluator = s.handler
    s.collector.control = SimpleNamespace(get_evaluator=lambda **kw: s.native)
    s.collector.span_reader = lambda row: copy.deepcopy(s.content)
    s.event = {'schemaVersion':'1.0', 'evaluatorId':s.native['evaluatorId'],
               'evaluatorName':s.native['evaluatorName'], 'evaluationLevel':'TRACE',
               'evaluationInput':{'sessionSpans':copy.deepcopy(s.content['sessionSpans'])},
               'evaluationReferenceInputs':{}, 'evaluationTarget':{'traceIds':['a'*32]}}
    s.calls = []
    def evaluate(**request):
        s.calls.append(request)
        result = s.handler.handle(s.event, s.boundary)
        assert set(result) == {'label','value','explanation'}
        native = response(s)
        native['evaluationResults'][0].update(result)
        return native
    s.collector.agentcore = SimpleNamespace(meta=s.ac.meta, evaluate=evaluate)
    return s


def register(s):
    with s.store.tx() as db:
        row, cfg, fence = s.collector.context(db, s.row['run_ref'])
        s.handler.register(db, row, cfg, fence)
        runs.put(db, 'foundation-collect:' + row['run_ref'], {'status':'CLAIMED','fence':fence})


def denied(s, event=None, context=None):
    result = s.handler.handle(s.event if event is None else event, s.boundary if context is None else context)
    assert set(result) == {'errorCode','errorMessage'}
    assert 'Protected passage' not in str(result)


@pytest.mark.parametrize('fail', [False, True])
def test_native_trace_pass_fail_and_cached_retry(code, fail):
    s = code
    if fail:
        rewrite_input(s, lambda c: c['report']['citations'][0].update(source_id='unknown'))
    register(s)
    result = s.handler.handle(s.event, s.boundary)
    assert result['label'] == ('FAIL' if fail else 'PASS')
    assert result['value'] == (0.0 if fail else 1.0)
    class NoRead:
        def get_object(self, **kw):
            raise AssertionError('cached retry must not read content')
    s.handler.s3 = NoRead()
    assert s.handler.handle(s.event, s.boundary) == result


def test_collector_registration_before_evaluate_and_missing_judge(code):
    s = code
    row = s.collector.collect(s.store, s.row['run_ref'])
    assert s.collector.collect(s.store, s.row['run_ref']) == row
    assert len(s.calls) == 1
    raw = read_pinned(s.s3, s.bucket, 'evidence/', row['evidence_source']['binding'], row['evidence_source'])
    import json
    evidence = json.loads(raw)
    spec = row['evidence_source']['evaluations'][0]
    receipt = json.loads(read_pinned(s.s3, s.bucket, 'evidence/', row['evidence_source']['binding'], spec))
    evidence.update(readback='SERVER_READBACK_V1', agentcore_receipts=[receipt])
    assert evidence['evaluations'][0]['judge_complete'] is False
    validate_receipts(row, evidence)
    checked(row, evidence)
    projection = project(row, evidence)
    assert projection['status'] == 'AVAILABLE'
    assert projection['reason'] == 'SEMANTIC_JUDGE_NOT_RUN'
    assert projection['required_judge_passed'] is False


def test_forged_external_references_ignored(code):
    s = code; register(s)
    s.event['evaluationReferenceInputs'] = {'url':'https://untrusted.invalid/private',
        'bucket':'other', 'owner':'forged', 'job_id':'other', 'version':99}
    assert s.handler.handle(s.event, s.boundary)['label'] == 'PASS'


@pytest.mark.parametrize('kind', ['missing_mapping','missing_boundary','fake_auth','wrong_evaluator',
    'wrong_name','wrong_trace','ambiguous','malformed_spans','changed_spans','oversize','wrong_schema',
    'wrong_level','wrong_session'])
def test_invalid_events_fail_closed(code, kind):
    s = code
    if kind != 'missing_mapping': register(s)
    if kind == 'missing_boundary': s.handler.authenticate_service = None
    if kind == 'fake_auth':
        s.event['authenticated'] = True
        return denied(s, context=object())
    if kind == 'wrong_evaluator': s.event['evaluatorId'] = 'other'
    if kind == 'wrong_name': s.event['evaluatorName'] = 'other'
    if kind == 'wrong_trace': s.event['evaluationTarget']['traceIds'] = ['c'*32]
    if kind == 'ambiguous': s.event['evaluationTarget']['traceIds'].append('c'*32)
    if kind == 'malformed_spans': s.event['evaluationInput']['sessionSpans'] = [{}]
    if kind == 'changed_spans': s.event['evaluationInput']['sessionSpans'][0]['attributes']['source'] = 'forged'
    if kind == 'oversize': s.event['padding'] = 'x' * (6 * 1024 * 1024)
    if kind == 'wrong_schema': s.event['schemaVersion'] = '2.0'
    if kind == 'wrong_level': s.event['evaluationLevel'] = 'TOOL_CALL'
    if kind == 'wrong_session': s.event['evaluationInput']['sessionSpans'][0]['sessionId'] = 'wrong'
    denied(s)


@pytest.mark.parametrize('field', ['owner','version','run_ref','epoch'])
def test_cross_job_version_and_stale_authority(code, field):
    s = code; register(s)
    with s.store.tx() as db:
        row = runs.get(db, 'foundation-run:' + s.row['run_ref'])
        row[field] = 'wrong'
        runs.put(db, 'foundation-run:' + s.row['run_ref'], row)
    denied(s)


def test_revoked_authority_denies_even_cached_result(code, monkeypatch):
    s = code; register(s)
    assert s.handler.handle(s.event, s.boundary)['label'] == 'PASS'
    def revoked(*args): raise ValueError('revoked')
    monkeypatch.setattr(runs, 'current', revoked)
    denied(s)


@pytest.mark.parametrize('kind', ['truncated','partial','empty_source','bad_hash','oversize','wrong_version'])
def test_incomplete_content_and_injected_s3_fail_closed(code, kind):
    s = code
    if kind in ('truncated','partial','empty_source'):
        def change(c):
            if kind == 'truncated': c['truncated'] = True
            if kind == 'partial': c['complete'] = False
            if kind == 'empty_source': c['sources'][0]['text'] = ''
        rewrite_input(s, change)
    register(s)
    if kind in ('bad_hash','oversize','wrong_version'):
        pin = s.row['collection_input']
        raw = b'x' * (65537 if kind == 'oversize' else 1)
        s.handler.s3 = SimpleNamespace(get_object=lambda **kw: {'Body':io.BytesIO(raw),
            'ContentLength':len(raw), 'VersionId':'wrong' if kind == 'wrong_version' else pin['version_id'],
            'Metadata':{'binding-digest':digest(pin['binding']), 'sha256':pin['sha256']}})
    denied(s)


def test_mapping_cannot_rebind(code):
    s = code; register(s)
    with pytest.raises(Exception, match='MAPPING_CONFLICT'): register(s)


def test_unconfigured_code_adapter_remains_blocked(code):
    s = code; s.handler.authenticate_service = None
    with pytest.raises(Exception, match='EVENT_REFERENCE_MAPPING'):
        s.collector.collect(s.store, s.row['run_ref'])
    assert s.calls == []


def test_missing_report_fails_closed(code):
    s = code
    rewrite_input(s, lambda c: c.pop('report'))
    register(s)
    denied(s)


def test_multiple_trace_span_set_is_ambiguous(code):
    s = code; register(s)
    s.event['evaluationInput']['sessionSpans'].append({'traceId':'c'*32,'spanId':'d'*16})
    denied(s)


def test_native_result_without_handler_cache_is_not_trusted(code):
    s = code
    def forged(**kwargs):
        result = response(s)
        result['evaluationResults'][0].update(label='PASS', value=1.0)
        return result
    s.collector.agentcore.evaluate = forged
    with pytest.raises(Exception, match='CODE_RESULT_REQUIRED'):
        s.collector.collect(s.store, s.row['run_ref'])
    with pytest.raises(Exception, match='UNCERTAIN'):
        s.collector.collect(s.store, s.row['run_ref'])


@pytest.mark.parametrize('defect', ['none', 'unknown_source', 'duplicate', 'unresolved'])
def test_deterministic_failurevisibility_reader_to_job(code, defect):
    from botocore.stub import Stubber
    from tests.test_evaluation_collector import query
    from backend.foundation_jobs import FoundationJobs
    s = code
    def change(c):
        if defect == 'unknown_source': c['report']['citations'][0]['source_id'] = 'unknown'
        if defect == 'duplicate': c['report']['citations'].append(copy.deepcopy(c['report']['citations'][0]))
        if defect == 'unresolved':
            c['report']['text'] += ' [unknown]'
            c['sessionSpans'][0]['attributes']['report'] = c['report']['text']
    rewrite_input(s, change)
    s.event['evaluationInput']['sessionSpans'] = copy.deepcopy(s.content['sessionSpans'])
    jobs = FoundationJobs(None,None,None,enabled=True,evidence_collector=s.collector,evidence_reader=s.reader)
    with Stubber(s.logs) as logs:
        logs.add_response('get_query_results',query(s,True),{'queryId':'traces-query'})
        jobs.step(s.store,s.row['run_ref'])
    with s.store.tx() as db:
        row = runs.get(db,'foundation-run:'+s.row['run_ref'])
        p = project(row,row['evidence'])
        assert p['status'] == 'AVAILABLE'
        assert p['report']['text'] == s.content['report']['text']
        assert p['required_judge_passed'] is False
        if defect != 'none':
            assert p['quality_status'] == 'FAIL' and p['citation_status'] == 'INVALID'
            assert p['report']['citations'] == []
        # Independent synthetic Linux proof lets us exercise the actual final gate.
        fields = dict(package_digest='synthetic', manifest_digest='m', admission_digest='a',
                      artifact_source_digest='s', artifact_version='v', target='linux-arm64-python3.13')
        runs.put(db,'foundation-artifact:'+row['definition_digest'],fields)
        runs.put(db,'foundation-linux:synthetic',{**fields,'status':'PASS','execution':'ACTUAL_LINUX',
            'validator_identity':'synthetic','evidence_digest':'synthetic','entrypoint_passed':True})
        row['response']['execution_status'] = 'EXECUTION_SUCCEEDED'
        runs.put(db,'foundation-run:'+row['run_ref'],row)
    jobs.step(s.store,s.row['run_ref'])
    with s.store.tx() as db:
        assert db.select('jobs').fetchone()['stage'] == 'BLOCKED'
    assert len(s.calls) == 1
