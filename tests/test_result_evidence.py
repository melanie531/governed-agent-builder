"""Synthetic SDK responses only. No cloud calls or live acceptance evidence."""
import copy
import hashlib
import io
import json
from types import SimpleNamespace
import pytest
from backend.result_evidence import AgentCoreEvidenceRepository, EvidenceReader, binding, project
from foundation_harness.config import digest


def attach_receipt(row, evidence):
    """Synthetic collector output only. Not a native pipeline implementation."""
    evaluator = {'evaluatorId': 'synthetic-evaluator', 'evaluatorConfig': {'synthetic': True}}
    receipt = {'pipeline': 'AGENTCORE_EVALUATE_V1', 'pipeline_run_id': 'synthetic-pipeline-run',
        'binding': binding(row), 'evaluator_id': evaluator['evaluatorId'],
        'evaluator_version': digest(evaluator), 'native_evaluator_config': evaluator,
        'rubric_digest': binding(row)['rubric_digest'], 'dataset_digest': binding(row)['dataset_digest'],
        'cases': [{'case_id': 'case-1', 'request_id': 'synthetic-request-id',
            'trace_id': 'a'*32, 'span_id': 'b'*16, 'status': 'PASS',
            'evaluate_request': {'evaluatorId': evaluator['evaluatorId'],
                'evaluationInput': {'sessionSpans': [{'synthetic': True}]},
                'evaluationTarget': {'spanIds': ['b'*16]}},
            'evaluate_response': {'evaluationResults': [{'evaluatorId': evaluator['evaluatorId'],
                'context': {'spanContext': {'sessionId': 'synthetic-session', 'traceId': 'a'*32, 'spanId': 'b'*16}},
                'value': 1.0}]}}]}
    raw = json.dumps(receipt).encode()
    spec = {'id': evidence['evaluations'][0]['id'], 'required': 1, 'case_ids': ['case-1'],
        'receipt_id': 'synthetic-receipt', 'version_id': 'immutable-v1',
        'sha256': hashlib.sha256(raw).hexdigest(), 'receipt_digest': digest(receipt),
        'evaluator_id': evaluator['evaluatorId'], 'evaluator_version': digest(evaluator)}
    row.setdefault('evidence_source', {})['evaluations'] = [spec]
    evidence['agentcore_receipts'] = [receipt]
    return raw


class SyntheticRepository(AgentCoreEvidenceRepository):
    def __init__(self, raw): self.raw = raw
    def read_verified_receipt(self, *, receipt_id, version_id):
        assert (receipt_id, version_id) == ('synthetic-receipt', 'immutable-v1')
        return self.raw


def sample():
    row = dict(run_ref='job-synthetic', owner='alex', workspace='research', agent='agent-synthetic',
               version=1, definition_digest='d'*64, manifest_digest='m'*64, epoch=1,
               runtime={'runtime_arn':'runtime-synthetic', 'runtime_version':'3'},
               state='FINISHED', settled=True, response={'trace_id':'a'*32})
    row['approved'] = {'policy_version':1, 'config':{'model':{'route':'synthetic', 'version':'1'},
        'evaluation':{'dataset':{'digest':'dataset'}, 'rubric':{'digest':'rubric'}}},
        'model_rate_schedule':{'route':'synthetic','route_version':'1','version':'test-v1',
          'source':'synthetic-test-rates','date':'2026-09-12','currency':'USD','token_basis':'exclusive',
          'per_million':{'input_tokens':'1','output_tokens':'2'}}}
    evidence = {'binding':binding(row), 'readback':'SERVER_READBACK_V1',
      'report':{'text':'<img src=x onerror=alert(1)> Answer [c1]',
                'citations':[{'id':'c1','title':'Synthetic source','source_id':'source-1'}]},
      'source_ids':['source-1'], 'trace_ids':['a'*32],
      'evaluations':[{'id':'eval-synthetic','status':'PASS','completed':1,'required':1,'judge_complete':True}],
      'provider':{'route':'synthetic','route_version':'1','usage':{'input_tokens':100,'output_tokens':20}}}
    attach_receipt(row, evidence)
    return row, evidence


def reader(row, evidence, mutate=lambda x:x):
    receipt_raw = json.dumps(evidence['agentcore_receipts'][0]).encode()
    specs = row['evidence_source']['evaluations']
    raw=json.dumps(evidence).encode()
    row['evidence_source']={'binding':binding(row),'key':'evidence/run.json','version_id':'v1',
        'sha256':hashlib.sha256(raw).hexdigest(),'source_ids':['source-1'],'query_id':'query-synthetic',
        'evaluations':specs}
    response={'VersionId':'v1','Metadata':{'binding-digest':digest(binding(row))},'Body':io.BytesIO(raw)}
    mutate(response)
    return EvidenceReader(bucket='synthetic-bucket', prefix='evidence/',
      s3=SimpleNamespace(get_object=lambda **kw:response),
      cloudwatch=SimpleNamespace(get_query_results=lambda **kw:{'status':'Complete','results':[[
        {'field':'binding_digest','value':digest(binding(row))},{'field':'trace_id','value':'a'*32}]]}),
      agentcore_evidence=SyntheticRepository(receipt_raw))


def test_readback_to_projection():
    row,e=sample(); r=reader(row,e); value=project(row,r(row))
    assert value['status']=='AVAILABLE'
    assert value['cost']['estimated_model_inference_cost']=='0.00014'
    assert value['report']['text']==e['report']['text'] # React escapes text, no HTML interpretation
    assert 'binding' not in value and 'owner' not in json.dumps(value)


@pytest.mark.parametrize('field',['owner','workspace','runtime_arn','runtime_version','version','definition_digest','policy_version','dataset_digest','rubric_digest'])
def test_wrong_binding(field):
    row,e=sample(); e['binding'][field]='wrong'
    assert project(row,e)['status']=='BLOCKED'


@pytest.mark.parametrize('change', ['trace','judge','coverage','url','ready','malformed'])
def test_adversarial_content(change):
    row,e=sample()
    if change=='trace': e['trace_ids']=['b'*32]
    if change=='judge': e['evaluations'][0]['judge_complete']=False
    if change=='coverage': e['evaluations'][0]['completed']=0
    if change=='unknowncitation': e['report']['text']='Unsupported [unknown]'
    if change=='url': e['report']['citations'][0]['URL']='https://untrusted.invalid'
    if change=='ready': e.pop('readback'); e['ready']=True
    if change=='malformed': e['report']='not a report'
    p=project(row,e)
    assert p['status']=='BLOCKED' and p['report'] is None and p['cost']['status']=='UNKNOWN'


@pytest.mark.parametrize('change',['version','metadata','hash','sdk','eval','trace','prefix'])
def test_sdk_failures_are_sanitized(change):
    row,e=sample(); r=reader(row,e)
    if change=='version': r=reader(row,e,lambda x:x.update(VersionId='wrong'))
    if change=='metadata': r=reader(row,e,lambda x:x.update(Metadata={}))
    if change=='hash': row['evidence_source']['sha256']='0'*64
    if change=='sdk': r.s3.get_object=lambda **kw: (_ for _ in ()).throw(RuntimeError('private secret prompt'))
    if change=='eval': r.agentcore_evidence.raw = b'{}'
    if change=='trace': r.cloudwatch.get_query_results=lambda **kw:{'status':'Running'}
    if change=='prefix': row['evidence_source']['key']='other/run.json'
    assert r(row) is None


@pytest.mark.parametrize('change',['missing_usage','bad_usage','missing_rate','wrong_rate','cache','nan'])
def test_unknown_cost_not_zero(change):
    row,e=sample()
    if change=='missing_usage': e['provider'].pop('usage')
    if change=='bad_usage': e['provider']['usage']['input_tokens']=True
    if change=='missing_rate': row['approved'].pop('model_rate_schedule')
    if change=='wrong_rate': row['approved']['model_rate_schedule']['route_version']='2'
    if change=='cache': e['provider']['usage']['cached_read_tokens']=50
    if change=='nan': row['approved']['model_rate_schedule']['per_million']['input_tokens']='NaN'
    cost=project(row,e)['cost']
    assert cost['status']=='UNKNOWN' and cost['estimated_model_inference_cost'] is None


def test_owner_scoped_api_projection(app,client,payload):
    from tests.conftest import login,create,enqueue
    from backend import foundation_runs as runs
    login(client); definition=create(client,payload); job=enqueue(client,definition)
    row,e=sample(); row.update(run_ref=job,agent=definition['agent_id'],owner=definition['owner'],workspace=definition['workspace'])
    e['binding']=binding(row); attach_receipt(row,e); row['evidence']=e; row['stored_input']='PRIVATE-PROMPT'
    with app.state.store.tx() as db:
        runs.put(db,'foundation-run:'+job,row)
        db.update('jobs',{'stage':'BLOCKED','result':json.dumps({'mode':'live','raw':'PRIVATE-PROMPT'})},where=[('id','=',job)])
    result=client.get('/api/jobs/'+job)
    assert result.status_code==200 and result.json()['result']['result_evidence']['status']=='AVAILABLE'
    assert 'PRIVATE-PROMPT' not in result.text and 'stored_input' not in result.text
    for identity in ['sam','admin']:
        login(client,identity)
        assert client.get('/api/jobs/'+job).status_code==404


def test_reader_rejects_underreported_required_coverage():
    row,e=sample(); r=reader(row,e); row['evidence_source']['evaluations'][0]['required']=2
    assert r(row) is None


def test_cache_usage_is_priced_only_with_explicit_exclusive_rates():
    row,e=sample(); e['provider']['usage']['cached_read_tokens']=50
    row['approved']['model_rate_schedule']['per_million']['cached_read_tokens']='0.1'
    assert project(row,e)['cost']['estimated_model_inference_cost']=='0.000145'


def test_generic_bedrock_job_client_is_not_accepted_or_called():
    calls = []
    generic = SimpleNamespace(get_evaluation_job=lambda **kw: calls.append(kw))
    with pytest.raises(TypeError, match='AGENTCORE_EVIDENCE_REPOSITORY_REQUIRED'):
        EvidenceReader(s3=None, cloudwatch=None, agentcore_evidence=generic, bucket='test', prefix='evidence/')
    assert calls == []


@pytest.mark.parametrize('field', ['pipeline', 'pipeline_run_id', 'native_evaluator_config',
    'evaluator_version', 'dataset_digest', 'rubric_digest', 'cases'])
def test_normalized_receipt_without_pipeline_binding_fails_closed(field):
    row,e = sample()
    e['agentcore_receipts'][0].pop(field)
    row['evidence_source']['evaluations'][0]['receipt_digest'] = digest(e['agentcore_receipts'][0])
    assert project(row,e)['status'] == 'BLOCKED'


def test_runtime_pass_and_normalized_summary_are_not_evidence():
    row,e = sample()
    e.pop('agentcore_receipts')
    e.update(ready=True, execution_status='EXECUTION_SUCCEEDED')
    assert project(row,e)['status'] == 'BLOCKED'
    row,e = sample(); row.pop('evidence_source')
    assert project(row,e)['status'] == 'BLOCKED'


def test_offline_agentcore_schema_is_not_bedrock_job_schema():
    from botocore.session import Session
    model = Session().get_service_model('bedrock-agentcore')
    assert 'GetEvaluationJob' not in model.operation_names
    operation = model.operation_model('Evaluate')
    assert 'evaluatorId' in operation.input_shape.members
    assert 'evaluationResults' in operation.output_shape.members


@pytest.mark.parametrize('field', ['url', 'script', 'ready'])
def test_unsafe_extra_evidence_fields_hidden(field):
    row,e = sample(); e[field] = 'untrusted'
    p = project(row,e)
    assert p['status'] == 'BLOCKED' and p['report'] is None


def test_report_rendering_uses_escaped_text_not_links():
    from pathlib import Path
    source = Path('frontend/src/ResultEvidence.tsx').read_text()
    assert '<pre>{value.report.text}</pre>' in source
    assert 'dangerouslySetInnerHTML' not in source and 'href=' not in source
