"""Offline real engine/OTel spans + SDK Stubber + private versioned Moto S3."""
import copy
import json
import time
from dataclasses import replace

import pytest
from botocore.stub import Stubber

from backend import foundation_runs as runs
from backend.foundation_jobs import FoundationJobs
from backend.run_evidence_exporter import RunEvidenceExporter, ExportWaiting, execution, normalize, content_for
from backend.result_evidence import binding, project
from foundation_harness.config import digest
from tests.test_evaluation_collector import setup, no_network, response
from tests.test_foundation_executor import setup as engine_setup, run, message, config


@pytest.fixture
def real_run(setup):
    s = setup
    engine, original, authority, transport, budget = engine_setup()
    authority.binding = replace(original, runtime_session=s.row['run_ref'].ljust(33, '0'))
    # ControlledAuthority stores the binding directly.
    result = run(engine, authority.binding, budget)
    assert result['status'] == 'SUCCEEDED'
    row = copy.deepcopy(s.row)
    row.pop('collection_input'); row.pop('collection_grant')
    row.update(state='FINISHED', settled=True, response=result, response_digest=digest(result),
        runtime_request_id='synthetic-invoke-request', usage=result['usage'],
        manifest_digest=original.manifest_digest, foundation_digest=original.foundation_digest,
        stored_input='Synthetic question', calls={'model:model-1': 'CLAIMED'}, deadline=time.time()+300)
    s.row = row
    with s.store.tx() as db:
        runs.put(db, 'foundation-run:'+row['run_ref'], row)
    s.exporter = RunEvidenceExporter(s3=s.s3, cloudwatch=s.logs, bucket=s.bucket, prefix='evidence/')
    s.jobs = FoundationJobs(None,None,None,enabled=True,evidence_exporter=s.exporter,
                           evidence_collector=s.collector,evidence_reader=s.reader)
    s.spans, s.root = execution(row)
    yield s


def query(s, spans=None, status='Complete'):
    return {'status':status, 'results': [[{'field':'@message', 'value':json.dumps(span)}]
                                       for span in (s.spans if spans is None else spans)]}


def start(s, stub):
    stub.add_response('start_query', {'queryId':'synthetic-query'}, {
        'logGroupName':'/governed-agent-builder/foundation-m0',
        'startTime':s.root['startTimeUnixNano']//1000000000-1,
        'endTime':s.root['endTimeUnixNano']//1000000000+2,
        'queryString':'fields @message | filter traceId = "'+s.root['traceId']+'" | limit 101', 'limit':101})
    s.jobs.step(s.store,s.row['run_ref'])
    with s.store.tx() as db:
        assert db.select('jobs').fetchone()['stage']=='EVALUATING'
        assert not runs.get(db,'foundation-evaluating:'+s.row['run_ref'])
        assert not runs.get(db,'foundation-collect:'+s.row['run_ref'])


def test_real_engine_export_collect_reader_chain(real_run):
    s=real_run
    frozen=copy.deepcopy(s.row['approved'])
    content,_=content_for(s.row,s.spans)
    with Stubber(s.logs) as logs, Stubber(s.control) as ctrl, Stubber(s.ac) as ac:
        start(s,logs)
        for _ in range(3):logs.add_response('get_query_results',query(s),{'queryId':'synthetic-query'})
        ctrl.add_response('get_evaluator',s.native,{'evaluatorId':s.native['evaluatorId']})
        r=response(s);r['evaluationResults'][0]['context']['spanContext'].update(
            sessionId=s.row['run_ref'].ljust(33,'0'),traceId=s.root['traceId'])
        ac.add_response('evaluate',r,{'evaluatorId':s.native['evaluatorId'],
            'evaluationInput':{'sessionSpans':content['sessionSpans']},
            'evaluationTarget':{'traceIds':[s.root['traceId']]}})
        s.jobs.step(s.store,s.row['run_ref'])
        ac.assert_no_pending_responses();logs.assert_no_pending_responses()
    with s.store.tx() as db:
        row=runs.get(db,'foundation-run:'+s.row['run_ref'])
        assert db.select('jobs').fetchone()['stage']=='EVIDENCE_CHECK'
        assert row['approved']==frozen
        assert row['collection_grant']['input_digest']==digest(row['collection_input'])
        assert project(row,row['evidence'])['report']['text']=='Synthetic answer'
        assert row['evidence_export']['status']=='COMPLETE'
    # A duplicate export neither rewrites immutable content nor calls Evaluate.
    before=s.s3.list_object_versions(Bucket=s.bucket)['Versions']
    with s.store.tx() as db:
        db.update('jobs',{'stage':'EVALUATING'})
        owner={'token':'retry','expires':time.time()+100}
        runs.put(db,'foundation-evaluating:'+row['run_ref'],owner)
    assert s.exporter.export(s.store,row['run_ref'],owner)==row
    assert s.s3.list_object_versions(Bucket=s.bucket)['Versions']==before


@pytest.mark.parametrize('kind',['running','missing','duplicate','mixed','request','parent','truncated','pagination'])
def test_bounded_query_no_evaluate(real_run,kind):
    s=real_run
    spans=copy.deepcopy(s.spans)
    if kind=='missing':spans.pop()
    if kind=='duplicate':spans.append(spans[0])
    if kind=='mixed':spans[0]['traceId']='f'*32
    if kind=='request':spans[0]['attributes']['foundation.request_id']='wrong'
    if kind=='parent':spans[0]['parentSpanId']='f'*16
    q=query(s,spans,status='Running' if kind=='running' else 'Complete')
    if kind=='truncated':q['statistics']={'recordsMatched':101.0}
    with Stubber(s.logs) as logs:
        start(s,logs)
        if kind=='pagination':
            # SDK version may not expose pagination in its modeled response;
            # exercise defensive validation independently of Stubber validation.
            old=s.logs.get_query_results
            s.logs.get_query_results=lambda **kw:{**q,'nextToken':'unexpected'}
        else:logs.add_response('get_query_results',q,{'queryId':'synthetic-query'})
        s.jobs.step(s.store,s.row['run_ref'])
        if kind=='pagination':s.logs.get_query_results=old
    with s.store.tx() as db:
        row=runs.get(db,'foundation-run:'+s.row['run_ref'])
        assert not row.get('collection_input') and not row.get('evidence_source')
        assert not runs.get(db,'foundation-collect:'+row['run_ref'])
        assert db.select('jobs').fetchone()['stage']==('EVALUATING' if kind in ('running','missing') else 'EVIDENCE_CHECK')


@pytest.mark.parametrize('kind',['digest','calls','request','parent','manifest','workspace','noowner'])
def test_execution_authority_before_query(real_run,kind,monkeypatch):
    s=real_run;row=copy.deepcopy(s.row)
    if kind=='digest':row['response_digest']='wrong'
    if kind=='calls':row['calls']['model:model-2']='CLAIMED'
    if kind=='request':row['runtime_request_id']=None
    if kind=='parent':row['response']['execution_record']['spans'][-1]['parentSpanId']='f'*16;row['response_digest']=digest(row['response'])
    if kind=='manifest':row['manifest_digest']='wrong'
    if kind=='workspace':
        def current(db,r):
            if r['workspace']!='original':raise ValueError('CURRENT_DEFINITION_DENIED')
        monkeypatch.setattr(runs,'current',current)
    owner={'token':'trusted','expires':time.time()+100}
    with s.store.tx() as db:
        runs.put(db,'foundation-run:'+row['run_ref'],row)
        if kind!='noowner':runs.put(db,'foundation-evaluating:'+row['run_ref'],owner)
    with pytest.raises(Exception):s.exporter.export(s.store,row['run_ref'],owner)


def test_wait_budget_and_stage_race(real_run):
    s=real_run
    with Stubber(s.logs) as logs:
        start(s,logs)
    with s.store.tx() as db:
        row=runs.get(db,'foundation-run:'+s.row['run_ref']);row['evidence_export']['polls']=12
        runs.put(db,'foundation-run:'+row['run_ref'],row)
    s.jobs.step(s.store,row['run_ref'])
    with s.store.tx() as db:
        assert db.select('jobs').fetchone()['stage']=='EVIDENCE_CHECK'


def test_active_owner_preserves_evidence(real_run):
    s=real_run
    with s.store.tx() as db:
        row=runs.get(db,'foundation-run:'+s.row['run_ref']);row['evidence']={'preserve':True}
        runs.put(db,'foundation-run:'+row['run_ref'],row)
        runs.put(db,'foundation-evaluating:'+row['run_ref'],{'token':'other','expires':time.time()+100})
    s.jobs.step(s.store,row['run_ref'])
    with s.store.tx() as db:
        assert runs.get(db,'foundation-run:'+row['run_ref'])==row
        assert db.select('jobs').fetchone()['stage']=='EVALUATING'


def test_revocation_during_s3_write_never_grants(real_run,monkeypatch):
    s=real_run
    original=s.s3.put_object
    def revoke(**kw):
        result=original(**kw)
        with s.store.tx() as db:
            runs.put(db,'foundation-evaluating:'+s.row['run_ref'],{'token':'new-owner','expires':time.time()+100})
        return result
    monkeypatch.setattr(s.s3,'put_object',revoke)
    with Stubber(s.logs) as logs:
        start(s,logs);logs.add_response('get_query_results',query(s),{'queryId':'synthetic-query'})
        s.jobs.step(s.store,s.row['run_ref'])
    with s.store.tx() as db:
        row=runs.get(db,'foundation-run:'+s.row['run_ref'])
        assert not row.get('collection_input') and not row.get('evidence_source')
        assert db.select('jobs').fetchone()['stage']=='EVALUATING'


def test_report_available_without_semantic_judge_or_rate(real_run):
    s=real_run
    with s.store.tx() as db:
        row=runs.get(db,'foundation-run:'+s.row['run_ref'])
        row['approved'].pop('agentcore_evaluation');row['approved'].pop('model_rate_schedule',None)
        runs.put(db,'foundation-run:'+row['run_ref'],row)
    with Stubber(s.logs) as logs:
        start(s,logs)
        for _ in range(2):logs.add_response('get_query_results',query(s),{'queryId':'synthetic-query'})
        s.jobs.step(s.store,s.row['run_ref'])
    with s.store.tx() as db:
        row=runs.get(db,'foundation-run:'+s.row['run_ref']);p=project(row,row['evidence'])
        assert p['status']=='AVAILABLE' and p['reason']=='SEMANTIC_JUDGE_NOT_RUN'
        assert p['required_judge_passed'] is False and p['quality_status']=='INCOMPLETE'
        assert p['cost']['estimated_model_inference_cost'] is None
        assert not runs.get(db,'foundation-collect:'+row['run_ref'])


def test_actual_tool_content_and_no_raw_content_in_operational_spans():
    use=[{'type':'tool_use','id':'call-one','name':'fixture___lookup','input':{'key':'sample'}}]
    tool=config()['tools'][0]
    responses=[message(use,'tool_use'),{'protocolVersion':'2025-03-26','capabilities':{'tools':{}}},
        {},{'tools':[{'name':tool['name'],'inputSchema':tool['inputSchema']}]},
        {'content':[{'type':'text','text':'Private real tool content'}]},message()]
    e,b,a,t,budget=engine_setup(responses=responses)
    result=run(e,b,budget)
    assert result['status']=='SUCCEEDED'
    spans=result['execution_record']['spans']
    assert 'Private real tool content' not in json.dumps(spans)
    assert 'Synthetic question' not in json.dumps(spans)
    private=result['execution_record']['content']
    toolspan=next(s for s in spans if s['name']=='tool')
    assert private[toolspan['spanId']]['gen_ai.tool.call.result']=='Private real tool content'
    assert toolspan['attributes']['foundation.request_id']=='synthetic-request'


def test_pinned_public_format_sample():
    from pathlib import Path
    import hashlib
    path=Path('tests/fixtures/cloudwatch-public-span.json')
    raw=path.read_bytes()
    # Public AWS sample is representative/partial, NOT a complete run fixture.
    doc=Path('docs/RUN-EVIDENCE-EXPORTER.md').read_text()
    assert hashlib.sha256(raw).hexdigest() in doc
    span=normalize(json.loads(raw))
    assert span['scope']['name']=='strands.telemetry.tracer'


def test_usage_must_match_actual_span_provider_values(real_run):
    row=copy.deepcopy(real_run.row)
    row['usage']['input_tokens']+=1
    row['response']['usage']=row['usage'];row['response_digest']=digest(row['response'])
    with pytest.raises(Exception,match='USAGE_CORRELATION'):execution(row)


def test_expired_owner_reclaimed_for_query_continuation(real_run):
    s=real_run
    with s.store.tx() as db:
        runs.put(db,'foundation-evaluating:'+s.row['run_ref'],{'token':'expired','expires':0})
    with Stubber(s.logs) as logs:start(s,logs)


@pytest.mark.parametrize('value',[{'api_key':'synthetic-only'}, {'password':'synthetic-only'},
    {'authorization':'Bearer ' + 'synthetic-' + 'only-token'}, {'text':'-----BEGIN ' + 'PRIVATE ' + 'KEY-----'}])
def test_private_capture_rejects_credential_content(value):
    from foundation_harness.telemetry import Telemetry
    t=Telemetry.local()
    with t.span('run') as span:
        with pytest.raises(ValueError,match='CREDENTIAL_CONTENT_DENIED'):
            t.content(span,{'gen_ai.task.input':json.dumps(value)})
    assert not t.private


@pytest.mark.parametrize('value',[None,{},'', 'x'*16385])
def test_input_cap_precedes_content_capture(value):
    e,b,a,t,budget=engine_setup()
    with pytest.raises(ValueError,match='INPUT_TEXT_OR_BYTE_CAP'):
        e.run(b.run_ref,b,value,budget)
    assert not e.telemetry.private and not t.calls
