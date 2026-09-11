"""Offline SDK Stubber/Moto only. All evidence, configuration and scores synthetic."""
import copy
import io
import json
import socket
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import boto3
import pytest
from botocore.stub import Stubber
from moto import mock_aws
from backend import foundation_runs as runs
from backend.evaluation_collector import (EvaluationCollector, PinnedSpanReader, configured_evidence,
    persist, read_pinned, snapshot)
from backend.result_evidence import AgentCoreEvidenceRepository, binding, project
from backend.store import Store
from foundation_harness.config import digest
from tests.test_result_evidence import sample


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*a, **kw): raise AssertionError('NETWORK_FORBIDDEN')
    monkeypatch.setattr(socket.socket, 'connect', denied)


def client(service):
    # Explicit synthetic credentials are test-only and bypass credential lookup.
    return boto3.client(service, region_name='us-west-2', aws_access_key_id='testing', aws_secret_access_key='testing')


@pytest.fixture
def setup(tmp_path, monkeypatch):
    with mock_aws():
        s3 = client('s3'); bucket = 'synthetic-evidence'
        s3.create_bucket(Bucket=bucket, CreateBucketConfiguration={'LocationConstraint':'us-west-2'})
        s3.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={'Status':'Enabled'})
        s3.put_public_access_block(Bucket=bucket, PublicAccessBlockConfiguration={k:True for k in
            ('BlockPublicAcls','IgnorePublicAcls','BlockPublicPolicy','RestrictPublicBuckets')})
        row, content = sample(); row.pop('evidence_source')
        content.pop('agentcore_receipts'); content.pop('readback'); content.pop('evaluations')
        content.update(complete=True, truncated=False, sources=[{'id':'source-1','text':'Protected passage'}],
            sessionSpans=[{'traceId':'a'*32,'spanId':'b'*16,'attributes':{'report':content['report']['text'], 'source':'Protected passage'}}])
        pin = persist(s3,bucket,'evidence/',binding(row),'input',content)
        row['collection_input'] = {**pin,'binding':binding(row),'source_ids':['source-1'],
            'source_digest':digest(content['sources']), 'span_ids':[['a'*32,'b'*16]],
            'query_id':'spans-query', 'trace_query_id':'traces-query'}
        now=datetime(2026,9,12,tzinfo=timezone.utc)
        native={'evaluatorId':'Builtin.Helpfulness','evaluatorArn':'arn:aws:bedrock-agentcore:::evaluator/Builtin.Helpfulness',
          'evaluatorName':'Builtin.Helpfulness','evaluatorConfig':{'llmAsAJudge':{'instructions':'Synthetic only',
          'ratingScale':{'numerical':[{'definition':'Synthetic pass','value':1.0,'label':'pass'}]},
          'modelConfig':{'bedrockEvaluatorModelConfig':{'modelId':'synthetic'}}}},
          'level':'TRACE','status':'ACTIVE','createdAt':now,'updatedAt':now}
        source={'native':snapshot(native),'source':'synthetic-reviewed-source'}
        cfg={'id':'eval-synthetic','native':snapshot(native),'purpose':'report_quality',
          'source_record_key':'foundation-evaluator:synthetic','source_record_digest':digest(source),
          'rubric_digest':'rubric','dataset_digest':'dataset','case_ids':['case-1'],
          'decision':{'kind':'numeric','min':0,'max':1,'pass_min':1}}
        row['approved']['agentcore_evaluation']=cfg
        seal_grant(row)
        store=Store(str(tmp_path/'collector.sqlite'))
        with store.tx() as db:
            seal_grant(row);runs.put(db,'foundation-run:'+row['run_ref'],row)
            runs.put(db,cfg['source_record_key'],source)
            db.insert('jobs',{'id':row['run_ref'],'stage':'EVALUATING'})
        # Authority validation separately covered by existing wiring/admission tests.
        monkeypatch.setattr(runs,'current',lambda db,r: None)
        logs=client('logs'); ac=client('bedrock-agentcore'); control=client('bedrock-agentcore-control')
        collector,reader=configured_evidence({'enabled':True,'bucket':bucket,'prefix':'evidence/',
            'span_contract':'PINNED_COMPLETE_SPANS_V1'},s3=s3,cloudwatch=logs,agentcore=ac,control=control)
        yield SimpleNamespace(**locals())


def seal_grant(row):
    # Synthetic trusted exporter only. Never amend the immutable approval.
    row['collection_grant'] = {'approval_digest':digest(row['approved']), 'binding':binding(row),
        'input_digest':digest(row['collection_input']),
        'cases':[{'case_id':'case-1','trace_id':'a'*32,'span_id':'b'*16,'session_id':'synthetic-session'}]}


def query(s, trace_only=False):
    fields=[{'field':'binding_digest','value':digest(binding(s.row))},{'field':'trace_id','value':'a'*32}]
    if not trace_only: fields.append({'field':'span_id','value':'b'*16})
    return {'status':'Complete','results':[fields]}


def response(s):
    return {'evaluationResults':[{'evaluatorId':s.native['evaluatorId'],'evaluatorArn':s.native['evaluatorArn'],
       'evaluatorName':s.native['evaluatorName'],'context':{'spanContext':{'sessionId':'synthetic-session','traceId':'a'*32}},
       'value':1.0}], 'ResponseMetadata':{'RequestId':'synthetic-sdk-request','HTTPStatusCode':200}}


@contextmanager
def stubs(s, mutate=lambda r:None, tool=False):
    with Stubber(s.control) as c, Stubber(s.logs) as l, Stubber(s.ac) as a:
        c.add_response('get_evaluator',s.native,{'evaluatorId':s.native['evaluatorId']})
        l.add_response('get_query_results',query(s),{'queryId':'spans-query'})
        req={'evaluatorId':s.native['evaluatorId'],'evaluationInput':{'sessionSpans':s.content['sessionSpans']},
             'evaluationTarget':{'spanIds':['b'*16]} if tool else {'traceIds':['a'*32]}}
        r=response(s); mutate(r)
        a.add_response('evaluate',r,req)
        yield l,a


def test_success_trace_exact_sdk_and_idempotent_readback(setup):
    s=setup
    with stubs(s) as (logs,ac):
        row=s.collector.collect(s.store,s.row['run_ref'])
        assert s.collector.collect(s.store,s.row['run_ref'])==row
        logs.add_response('get_query_results',query(s,True),{'queryId':'traces-query'})
        result=s.reader(row)
        assert project(row,result)['status']=='AVAILABLE'
        assert result['agentcore_receipts'][0]['cases'][0]['evaluate_request']['evaluationTarget']=={'traceIds':['a'*32]}
        ac.assert_no_pending_responses()
    spec=row['evidence_source']['evaluations'][0]
    repo=AgentCoreEvidenceRepository(s3=s.s3,bucket=s.bucket,prefix='evidence/',row=row)
    raw=repo.read_verified_receipt(receipt_id=spec['receipt_id'],version_id=spec['version_id'])
    assert json.loads(raw)['cases'][0]['status']=='PASS'
    row['owner']='wrong'
    with pytest.raises(Exception):repo.read_verified_receipt(receipt_id=spec['receipt_id'],version_id=spec['version_id'])


@pytest.mark.parametrize('kind',['empty','error','no_score','wrong_id','wrong_trace','wrong_session','duplicate','fail'])
def test_native_results_fail_closed(setup,kind):
    s=setup
    def change(r):
        e=r['evaluationResults'][0]
        if kind=='empty':r['evaluationResults']=[]
        if kind=='error':e.update(errorCode='SyntheticError',errorMessage='synthetic')
        if kind=='no_score':e.pop('value')
        if kind=='wrong_id':e['evaluatorId']='Builtin.Correctness'
        if kind=='wrong_trace':e['context']['spanContext']['traceId']='c'*32
        if kind=='wrong_session':e['context']['spanContext']['sessionId']='other'
        if kind=='duplicate':r['evaluationResults'].append(copy.deepcopy(e))
        if kind=='fail':e['value']=0.0
    with stubs(s,change):
        if kind=='fail':
            row=s.collector.collect(s.store,s.row['run_ref'])
            pin=row['evidence_source']; raw=read_pinned(s.s3,s.bucket,'evidence/',binding(row),pin)
            assert json.loads(raw)['evaluations'][0]['status']=='FAIL'
        else:
            with pytest.raises(Exception):s.collector.collect(s.store,s.row['run_ref'])
            with s.store.tx() as db:assert 'evidence_source' not in runs.get(db,'foundation-run:'+s.row['run_ref'])
            with pytest.raises(Exception,match='UNCERTAIN'):s.collector.collect(s.store,s.row['run_ref'])


@pytest.mark.parametrize('field',['owner','run_ref','runtime_version','version','rubric_digest','dataset_digest'])
def test_repository_owner_and_binding_isolation(setup,field):
    s=setup; expected=binding(s.row); pin=s.row['collection_input']
    altered={**expected,field:'wrong'}
    with pytest.raises(Exception):read_pinned(s.s3,s.bucket,'evidence/',altered,pin)


@pytest.mark.parametrize('kind',['digest','version','bucket','prefix','metadata','oversize'])
def test_immutable_s3_validation(setup,kind):
    s=setup; pin=copy.deepcopy(s.row['collection_input'])
    if kind=='digest':pin['sha256']='0'*64
    if kind=='version':pin['version_id']='null'
    if kind=='bucket':pin['bucket']='other'
    if kind=='prefix':pin['key']='outside/object'
    if kind in ('metadata','oversize'):
        r=s.s3.put_object(Bucket=s.bucket,Key=pin['key'],Body=b'x'* (65537 if kind=='oversize' else 1))
        pin['version_id']=r['VersionId']
    with pytest.raises(Exception):read_pinned(s.s3,s.bucket,'evidence/',binding(s.row),pin)


def test_runtime_forgery_ignored_missing_source(setup):
    s=setup
    with s.store.tx() as db:
        row=copy.deepcopy(s.row); row.pop('collection_input')
        row['response'].update(evidence_source={'ready':True},collection_input=s.row['collection_input'],evaluationResults=[{'value':1}])
        runs.put(db,'foundation-run:'+row['run_ref'],row)
    with pytest.raises(Exception):s.collector.collect(s.store,s.row['run_ref'])


def test_failed_final_cas_has_no_descriptor_or_released_result(setup):
    s=setup
    class FailedCAS:
        count=0
        @contextmanager
        def tx(self):
            self.count+=1
            with s.store.tx() as db:
                yield db
                if self.count==2:raise RuntimeError('synthetic CAS conflict')
    with stubs(s), pytest.raises(RuntimeError,match='CAS'):
        s.collector.collect(FailedCAS(),s.row['run_ref'])
    with s.store.tx() as db:
        row=runs.get(db,'foundation-run:'+s.row['run_ref'])
        assert 'evidence_source' not in row and 'evidence' not in row
        assert db.select('jobs').fetchone()['stage']=='EVALUATING'


def test_disabled_and_missing_dependencies():
    assert configured_evidence({})==(None,None)
    with pytest.raises(Exception):configured_evidence({'enabled':True})


def test_code_event_mapping_is_explicit_gate(setup):
    s=setup; s.native['evaluatorConfig']={'codeBased':{'lambdaConfig':{'lambdaArn':'arn:aws:lambda:us-west-2:'+'1234'+'56789012:function:synthetic'}}}
    with s.store.tx() as db:
        row=runs.get(db,'foundation-run:'+s.row['run_ref']); cfg=row['approved']['agentcore_evaluation']
        cfg['native']=snapshot(s.native); source={'native':cfg['native']};cfg['source_record_digest']=digest(source)
        runs.put(db,cfg['source_record_key'],source);seal_grant(row);runs.put(db,'foundation-run:'+row['run_ref'],row)
    with Stubber(s.control) as c:
        c.add_response('get_evaluator',s.native,{'evaluatorId':s.native['evaluatorId']})
        with pytest.raises(Exception,match='EVENT_REFERENCE_MAPPING'):s.collector.collect(s.store,s.row['run_ref'])


def rewrite_input(s, change):
    change(s.content)
    pin=persist(s.s3,s.bucket,'evidence/',binding(s.row),'input',s.content)
    with s.store.tx() as db:
        row=runs.get(db,'foundation-run:'+s.row['run_ref'])
        row['collection_input'].update(pin)
        seal_grant(row)
        seal_grant(row);runs.put(db,'foundation-run:'+row['run_ref'],row)


@pytest.mark.parametrize('kind',['truncated','partial','source','missing_report'])
def test_bad_span_export_and_unknown_usage(setup,kind):
    s=setup
    def change(c):
        if kind=='truncated':c['truncated']=True
        if kind=='partial':c['sessionSpans']=[]
        if kind=='source':c['sources'][0]['text']='replaced'
        if kind=='missing_report':c['sessionSpans'][0]['attributes'].pop('report')
        if kind=='usage':c['provider'].pop('usage')
    rewrite_input(s,change)
    with Stubber(s.control) as ctrl, Stubber(s.logs) as logs, Stubber(s.ac) as ac:
        ctrl.add_response('get_evaluator',s.native,{'evaluatorId':s.native['evaluatorId']})
        if kind in ('missing_report','usage'):logs.add_response('get_query_results',query(s),{'queryId':'spans-query'})
        if kind=='usage':ac.add_response('evaluate',response(s),{'evaluatorId':s.native['evaluatorId'],
            'evaluationInput':{'sessionSpans':s.content['sessionSpans']},'evaluationTarget':{'traceIds':['a'*32]}})
        with pytest.raises(Exception):s.collector.collect(s.store,s.row['run_ref'])
        with s.store.tx() as db:assert 'evidence_source' not in runs.get(db,'foundation-run:'+s.row['run_ref'])


@pytest.mark.parametrize('field',['source_record_digest','rubric_digest','dataset_digest'])
def test_approved_binding_changes_rejected_before_sdk(setup,field):
    s=setup
    with s.store.tx() as db:
        row=runs.get(db,'foundation-run:'+s.row['run_ref']);row['approved']['agentcore_evaluation'][field]='wrong'
        seal_grant(row);runs.put(db,'foundation-run:'+row['run_ref'],row)
    with pytest.raises(Exception):s.collector.collect(s.store,s.row['run_ref'])


def test_report_cannot_target_tool_call(setup):
    s=setup;s.native['level']='TOOL_CALL'
    with s.store.tx() as db:
        row=runs.get(db,'foundation-run:'+s.row['run_ref']);cfg=row['approved']['agentcore_evaluation'];cfg['native']=snapshot(s.native)
        source={'native':cfg['native']};cfg['source_record_digest']=digest(source);runs.put(db,cfg['source_record_key'],source)
        seal_grant(row);runs.put(db,'foundation-run:'+row['run_ref'],row)
    with Stubber(s.control) as c:
        c.add_response('get_evaluator',s.native,{'evaluatorId':s.native['evaluatorId']})
        with pytest.raises(Exception,match='REPORT_REQUIRES_TRACE'):s.collector.collect(s.store,s.row['run_ref'])


def test_native_sdk_error_not_retried(setup):
    s=setup
    with Stubber(s.control) as c, Stubber(s.logs) as l, Stubber(s.ac) as a:
        c.add_response('get_evaluator',s.native,{'evaluatorId':s.native['evaluatorId']})
        l.add_response('get_query_results',query(s),{'queryId':'spans-query'})
        a.add_client_error('evaluate',service_error_code='ValidationException',service_message='synthetic')
        with pytest.raises(Exception):s.collector.collect(s.store,s.row['run_ref'])
        with pytest.raises(Exception,match='UNCERTAIN'):s.collector.collect(s.store,s.row['run_ref'])


def test_query_pagination_fails_closed(setup):
    s=setup
    with Stubber(s.logs) as logs:
        q=query(s);q['status']='Running'
        logs.add_response('get_query_results',q,{'queryId':'spans-query'})
        with pytest.raises(Exception,match='QUERY_INCOMPLETE'):s.collector.span_reader(s.row)


def test_tool_call_exact_native_target(setup):
    s=setup;s.native['level']='TOOL_CALL'
    with s.store.tx() as db:
        row=runs.get(db,'foundation-run:'+s.row['run_ref']);cfg=row['approved']['agentcore_evaluation']
        cfg.update(native=snapshot(s.native),purpose='tool_quality')
        source={'native':cfg['native']};cfg['source_record_digest']=digest(source)
        runs.put(db,cfg['source_record_key'],source);seal_grant(row);runs.put(db,'foundation-run:'+row['run_ref'],row)
    def change(r):r['evaluationResults'][0]['context']['spanContext']['spanId']='b'*16
    with stubs(s,change,tool=True):
        row=s.collector.collect(s.store,s.row['run_ref'])
        assert row['evidence_source']['evaluations'][0]['required']==1


def test_config_readback_drift(setup):
    s=setup;s.native['level']='TOOL_CALL'
    with Stubber(s.control) as c:
        c.add_response('get_evaluator',s.native,{'evaluatorId':s.native['evaluatorId']})
        with pytest.raises(Exception,match='CONFIG_CHANGED'):s.collector.collect(s.store,s.row['run_ref'])


def test_evaluating_stage_collects_then_reads(setup):
    from backend.foundation_jobs import FoundationJobs
    s=setup
    jobs=FoundationJobs(None,None,None,enabled=True,evidence_collector=s.collector,evidence_reader=s.reader)
    with stubs(s) as (logs,ac):
        logs.add_response('get_query_results',query(s,True),{'queryId':'traces-query'})
        jobs.step(s.store,s.row['run_ref'])
    with s.store.tx() as db:
        row=runs.get(db,'foundation-run:'+s.row['run_ref'])
        assert db.select('jobs').fetchone()['stage']=='EVIDENCE_CHECK'
        assert project(row,row['evidence'])['status']=='AVAILABLE'


def test_evaluating_missing_collector_inputs_never_releases(setup):
    from backend.foundation_jobs import FoundationJobs
    s=setup
    with s.store.tx() as db:
        row=copy.deepcopy(s.row);row.pop('collection_input');runs.put(db,'foundation-run:'+row['run_ref'],row)
    jobs=FoundationJobs(None,None,None,enabled=True,evidence_collector=s.collector,evidence_reader=s.reader)
    jobs.step(s.store,s.row['run_ref'])
    with s.store.tx() as db:
        row=runs.get(db,'foundation-run:'+s.row['run_ref'])
        assert row['evidence'] is None and 'evidence_source' not in row
        assert project(row,row['evidence'])['status']=='BLOCKED'
        assert db.select('jobs').fetchone()['stage']!='LIVE_PASS'


@pytest.mark.parametrize('missing', ['provider', 'usage', 'rates'])
def test_missingprice_keeps_descriptor_and_quality(setup, missing):
    s = setup
    if missing != 'rates':
        rewrite_input(s, lambda c: c.pop('provider') if missing == 'provider' else c['provider'].pop('usage'))
    else:
        with s.store.tx() as db:
            row = runs.get(db, 'foundation-run:' + s.row['run_ref'])
            row['approved'].pop('model_rate_schedule'); seal_grant(row)
            runs.put(db, 'foundation-run:' + row['run_ref'], row)
    with stubs(s) as (logs, ac):
        row = s.collector.collect(s.store, s.row['run_ref'])
        logs.add_response('get_query_results', query(s, True), {'queryId':'traces-query'})
        p = project(row, s.reader(row))
    assert p['status'] == 'AVAILABLE' and p['quality_status'] == 'PASS'
    assert p['cost']['status'] == 'UNKNOWN'
    assert p['cost']['estimated_model_inference_cost'] is None


def test_concurrentcollector_two_step_interleaving(setup):
    from backend.foundation_jobs import FoundationJobs
    s = setup
    worker = FoundationJobs(None, None, None, enabled=True, evidence_collector=s.collector, evidence_reader=s.reader)
    original = s.collector.span_reader
    calls = []
    def interleave(row):
        calls.append('A claimed')
        with s.store.tx() as db:
            assert runs.get(db, 'foundation-collect:' + row['run_ref'])['status'] == 'CLAIMED'
            before = runs.get(db, 'foundation-run:' + row['run_ref'])
        worker.step(s.store, row['run_ref'])  # B overlaps A's committed claim
        with s.store.tx() as db:
            assert db.select('jobs').fetchone()['stage'] == 'EVALUATING'
            assert runs.get(db, 'foundation-run:' + row['run_ref']) == before
        return original(row)
    s.collector.span_reader = interleave
    with stubs(s) as (logs, ac):
        logs.add_response('get_query_results', query(s, True), {'queryId':'traces-query'})
        worker.step(s.store, s.row['run_ref'])
        ac.assert_no_pending_responses()
    with s.store.tx() as db:
        row = runs.get(db, 'foundation-run:' + s.row['run_ref'])
        assert db.select('jobs').fetchone()['stage'] == 'EVIDENCE_CHECK'
        assert project(row, row['evidence'])['status'] == 'AVAILABLE'
    assert calls == ['A claimed']


def test_outer_transition_observed_stage_fence(setup):
    from backend.foundation_jobs import FoundationJobs
    s = setup
    def changed(row):
        with s.store.tx() as db:
            current = runs.get(db, 'foundation-run:' + row['run_ref'])
            current['evidence'] = {'preserved': True}
            runs.put(db, 'foundation-run:' + row['run_ref'], current)
            db.update('jobs', {'stage':'BLOCKED'}, where=[('id','=',row['run_ref'])])
        return None
    FoundationJobs(None,None,None,enabled=True,evidence_reader=changed).step(s.store,s.row['run_ref'])
    with s.store.tx() as db:
        assert db.select('jobs').fetchone()['stage'] == 'BLOCKED'
        assert runs.get(db,'foundation-run:'+s.row['run_ref'])['evidence'] == {'preserved':True}


REAL_CURRENT = __import__('backend.foundation_runs', fromlist=['current']).current


@pytest.mark.parametrize('outcome', ['pass', 'fail', 'deterministic', 'bad_citations'])
def test_originalauthoritycases_real_current_immutable_approval(setup, app, client, payload, tmp_path, monkeypatch, outcome):
    import time
    from tests.conftest import login, create
    from tests.test_foundation_wiring import install
    from backend.foundation_approval import finalized_artifact
    from backend.foundation_jobs import FoundationJobs
    s = setup
    monkeypatch.setattr(runs, 'current', REAL_CURRENT)
    login(client); definition = create(client, payload)
    install(app.state.store, definition, tmp_path)
    s.store = app.state.store
    is_code = outcome in ('deterministic', 'bad_citations')
    if is_code:
        from backend.code_evaluator import CodeEvaluator, ADAPTER
        s.native['evaluatorConfig'] = {'codeBased': {'lambdaConfig': {
            'lambdaArn':'arn:aws:lambda:us-west-2:'+'1234'+'56789012:function:synthetic'}}}
        s.cfg.update(native=snapshot(s.native), code_adapter=ADAPTER,
                     decision={'kind':'categorical','labels':['PASS','FAIL'],'pass_labels':['PASS']})
        s.source = {'native':snapshot(s.native),'source':'synthetic-reviewed-source'}
        s.cfg['source_record_digest'] = digest(s.source)
        boundary = object()
        adapter = CodeEvaluator(store=s.store,s3=s.s3,bucket=s.bucket,prefix='evidence/',
                                authenticate_service=lambda ctx: ctx is boundary)
        s.collector.code_evaluator = adapter
        original_evaluate = s.ac.evaluate
        def evaluate(**request):
            native_result = adapter.handle({'schemaVersion':'1.0', 'evaluatorId':s.native['evaluatorId'],
                'evaluatorName':s.native['evaluatorName'],'evaluationLevel':'TRACE',
                'evaluationInput':request['evaluationInput'],'evaluationTarget':request['evaluationTarget']}, boundary)
            assert native_result['label'] == ('FAIL' if outcome == 'bad_citations' else 'PASS')
            return original_evaluate(**request)
        monkeypatch.setattr(s.ac,'evaluate',evaluate)
    if outcome == 'bad_citations':
        s.content['report']['citations'][0]['source_id'] = 'unknown'
    with s.store.tx() as db:
        db.executescript('CREATE TABLE IF NOT EXISTS principals(id TEXT PRIMARY KEY, body TEXT, expires REAL);')
        approved = runs.get(db, 'foundation-approved:' + definition['digest'])
        cfg = copy.deepcopy(s.cfg)
        cfg.update(rubric_digest=approved['config']['evaluation']['rubric']['digest'],
                   dataset_digest=approved['config']['evaluation']['dataset']['digest'])
        # Synthetic evaluator review occurs BEFORE artifact finalization/run grant.
        approved['agentcore_evaluation'] = cfg
        runs.put(db, 'foundation-approved:' + definition['digest'], approved)
        artifact = runs.get(db, 'foundation-artifact:' + definition['digest'])
        artifact['approval_digest'] = digest(approved)
        runs.put(db, 'foundation-artifact:' + definition['digest'], artifact)
        row = copy.deepcopy(s.row)
        row.update(agent=definition['agent_id'], definition_digest=definition['digest'],
                   owner=definition['owner'], workspace=definition['workspace'],
                   approved=finalized_artifact(db, approved), epoch=approved['epoch'],
                   manifest_digest=approved['manifest_digest'], deadline=time.time()+300)
        frozen = copy.deepcopy(row['approved'])
        s.content['binding'] = binding(row)
        pin = persist(s.s3,s.bucket,'evidence/',binding(row),'input',s.content)
        row['collection_input'].update(pin, binding=binding(row))
        seal_grant(row)
        runs.put(db, cfg['source_record_key'], s.source)
        runs.put(db, 'foundation-run:' + row['run_ref'], row)
        row['response']['execution_status'] = 'EXECUTION_SUCCEEDED'
        runs.put(db, 'foundation-run:' + row['run_ref'], row)
        db.insert('jobs', {'id':row['run_ref'],'agent':row['agent'],'version':row['version'],'stage':'EVALUATING'})
        runs.current(db, row)
    s.row = row
    jobs = FoundationJobs(None,None,None,enabled=True,evidence_collector=s.collector,evidence_reader=s.reader)
    def native_outcome(response):
        item = response['evaluationResults'][0]
        item['value'] = 0.0 if outcome in ('fail','bad_citations') else 1.0
        if is_code: item['label'] = 'FAIL' if outcome == 'bad_citations' else 'PASS'
    with stubs(s, native_outcome) as (logs, ac):
        logs.add_response('get_query_results',query(s,True),{'queryId':'traces-query'})
        jobs.step(s.store,row['run_ref'])
    jobs.step(s.store,row['run_ref'])  # real EVIDENCE_CHECK final decision
    response = client.get('/api/jobs/' + row['run_ref'])
    assert response.status_code == 200, response.text
    public = response.json()
    assert public['stage'] == ('LIVE_PASS' if outcome == 'pass' else 'BLOCKED')
    assert public['result']['passed'] is (outcome == 'pass')
    visible = public['result']['result_evidence']
    assert visible['status'] == 'AVAILABLE'
    assert visible['report']['text'] == s.content['report']['text']
    assert visible['trace_ids'] == ['a'*32]
    assert [e['id'] for e in visible['evaluations']] == ['eval-synthetic']
    assert visible['cost']['status'] == 'UNKNOWN'
    assert visible['cost']['estimated_model_inference_cost'] is None
    if is_code:
        assert visible['required_judge_passed'] is False
        assert visible['reason'] == ('INVALID_CITATIONS' if outcome == 'bad_citations' else 'SEMANTIC_JUDGE_NOT_RUN')
    if outcome in ('fail','bad_citations'): assert visible['quality_status'] == 'FAIL'
    for private in ('agentcore_receipts','evaluate_request','evaluate_response','sessionSpans','stored_input','Protected passage'):
        assert private not in response.text
    # Assert the actual API field shape consumed by current local source UI, not legacy aliases.
    from pathlib import Path
    main = Path('frontend/src/main.tsx').read_text()
    view = Path('frontend/src/ResultEvidence.tsx').read_text()
    assert 'result_evidence?:ResultEvidence' in main
    assert '<ResultEvidenceView value={job?.result?.result_evidence}/>' in main
    for consumed in ('value.report.text','value.trace_ids','value.evaluations','cell:e=>e.id'):
        assert consumed in view
    for other in ('sam','admin'):
        login(client,other)
        assert client.get('/api/jobs/' + row['run_ref']).status_code == 404
    login(client)
    with s.store.tx() as db:
        current = runs.get(db,'foundation-run:'+row['run_ref'])
        runs.current(db,current)
        assert current['approved'] == frozen
        assert project(current,current['evidence'])['status'] == 'AVAILABLE'
        assert runs.get(db,'foundation-artifact:'+definition['digest'])['approval_digest'] == digest(approved)
        current['collection_grant']['approval_digest'] = 'forged'
        runs.put(db,'foundation-run:'+row['run_ref'],current)
        db.update('jobs', {'stage':'EVALUATING'}, where=[('id','=',row['run_ref'])])
        with pytest.raises(Exception,match='EXPORT_GRANT'): s.collector.context(db,row['run_ref'])
        current['approved']['agentcore_evaluation']['id'] = 'forged'
        with pytest.raises(Exception,match='APPROVED_ARTIFACT_REVOKED'): runs.current(db,current)


def test_inflight_collector_does_not_advance_outer_step(setup):
    from backend.foundation_jobs import FoundationJobs
    from backend.evaluation_collector import CollectionInFlight
    s = setup
    with s.store.tx() as db:
        row,cfg,fence = s.collector.context(db,s.row['run_ref'])
        runs.put(db,'foundation-collect:'+row['run_ref'],{'status':'CLAIMED','fence':fence})
    with pytest.raises(CollectionInFlight): s.collector.collect(s.store,row['run_ref'])
    FoundationJobs(None,None,None,enabled=True,evidence_collector=s.collector,evidence_reader=s.reader).step(s.store,row['run_ref'])
    with s.store.tx() as db:
        assert db.select('jobs').fetchone()['stage'] == 'EVALUATING'
        assert runs.get(db,'foundation-run:'+row['run_ref']) == row
