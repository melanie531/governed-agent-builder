import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path
import pytest
from foundations.web_research import digest
from runtime.web_research.app import invoke, configured_adapters
from runtime.web_research.gateways import GatewayError, ModelGateway, ToolGateway, gateway_endpoint
from runtime.web_research.harness import check_report, run, validate_manifest
from scripts.web_research_smoke import example_manifest, offline, StubTransport
from tests.conftest import login


def resign(m):
    d=m['definition'];d['digest']=digest({k:v for k,v in d.items() if k!='digest'})


def test_offline_real_protocol_harness_propagates_actual_sources_and_prompt():
    result=offline('Unique source content, not expected answers.')
    report=result['result']; requests=result['captured_requests']
    assert report['mode']=='offline-stub' and not report['production_ready']
    assert not report['evaluation']['passed'] and report['evaluation']['judge']=='NOT_CONFIGURED'
    assert report['telemetry']=={'tool_gateway':[{}],'model_gateway':{}}
    assert requests[0]['body']['params']['name']=='research___web_fetch'
    prompt=requests[1]['body']; user=json.loads(prompt['messages'][0]['content'])
    assert 'Unique source content' in user['untrusted_source_evidence'][0]['text']
    assert user['instructions']==example_manifest()['definition']['prompt']
    assert user['question']==example_manifest()['definition']['research']['question']
    assert 'NEVER_MODEL_INPUT' not in json.dumps(prompt)
    assert 'UNTRUSTED DATA' in prompt['system'] and 'tools' not in prompt
    assert 'approved_skill' in user


def test_provided_model_response_not_a_canned_answer():
    first=offline(); report=first['result']['report']; report['report']='A different supplied report ['+report['citations'][0]['citationID']+']'
    result=offline(model_response=json.dumps(report))
    assert result['result']['report']==report

@pytest.mark.parametrize('change', ['question','url','prompt','skill','version','dataset_ref','rubric_ref','tool'])
def test_definition_and_version_pins(change):
    m=example_manifest();d=m['definition']
    if change=='question': d['research']['question']='Tampered question'
    elif change=='url': d['research']['urls']=['https://evil.test/']
    elif change=='prompt': d['prompt']='Tampered instructions'
    elif change=='skill': m['bindings']['cited-brief']['artifact_digest']='0'*64
    elif change=='version': m['bindings']['approved-fetch']['version']='latest'
    elif change in ('dataset_ref','rubric_ref'): d[change]='sha256:wrong';resign(m)
    else: m['bindings']['approved-fetch']['read_only']=False
    with pytest.raises(ValueError): validate_manifest(m)


def test_skill_not_arbitrary_code_and_incompatible_format():
    m=example_manifest();m['bindings']['cited-brief']['instruction']='exec arbitrary code'
    with pytest.raises(ValueError):validate_manifest(m)
    m=example_manifest();m['definition']['research']['report_format']='html';resign(m)
    with pytest.raises(ValueError):validate_manifest(m)

@pytest.mark.parametrize('route',['openai/gpt-5','bedrock/amazon.nova-pro','anthropic.claude-sonnet-4-6','external/claude-sonnet-4-6'])
def test_disallowed_model_routes(route):
    with pytest.raises(GatewayError):ModelGateway(None,example_manifest()['bindings']['approved-claude']['endpoint'],route,'us-west-2',512)

@pytest.mark.parametrize('url',['https://bedrock-runtime.us-west-2.amazonaws.com/inference/v1/messages',
    'https://api.anthropic.com/v1/messages','http://x.gateway.bedrock-agentcore.us-west-2.amazonaws.com/mcp',
    'https://x.gateway.bedrock-agentcore.us-west-2.amazonaws.com/mcp?secret=x'])
def test_no_direct_bedrock_or_external_endpoint(url):
    with pytest.raises(GatewayError):gateway_endpoint(url,'us-west-2','/mcp')


def test_provider_failure_never_fixture_output():
    class Failed:
        def fetch(self,url):raise GatewayError('failure')
    with pytest.raises(GatewayError):run(example_manifest(),Failed(),None)
    assert invoke({'definition_digest':'missing'})['status']=='NOT_CONFIGURED_OR_FAILED'
    assert invoke({'definition_digest':'x','urls':['https://evil.test/']})['status']=='DENIED'
    with pytest.raises(GatewayError):configured_adapters(example_manifest(),{},approved=True)


def test_unknown_citations_and_model_html_remains_plain_data():
    output=offline()['result']; report=output['report']; sources=output['source_evidence']
    malicious='<img src="https://evil.test/pixel" onerror="alert(1)">'
    report['report']=malicious+' ['+sources[0]['citationID']+']'
    with pytest.raises(GatewayError):check_report(json.dumps(report),sources)
    report['report']='<script>alert(1)</script> ['+sources[0]['citationID']+']'
    assert check_report(json.dumps(report),sources)['report'].startswith('<script>')
    report['citations'][0]['citationID']='src-unknown'
    with pytest.raises(GatewayError):check_report(json.dumps(report),sources)


def test_unknown_url_fails():
    output=offline()['result'];report=output['report'];report['citations'][0]['URL']='https://evil.test/'
    with pytest.raises(GatewayError):check_report(json.dumps(report),output['source_evidence'])


def test_schema_and_ui_remain_not_configured(client,payload):
    login(client)
    foundation=client.get('/api/foundations/web-research').json()
    assert foundation['integration_status']=='NOT_CONFIGURED'
    payload.update(foundation_id='web-research',source='approved-public-web',research=example_manifest()['definition']['research'])
    assert client.post('/api/agents',json=payload).status_code==422
    payload['foundation_id']='research'
    assert client.post('/api/agents',json=payload).status_code==422


def test_offline_startup_no_credentials_or_network(monkeypatch):
    import boto3
    monkeypatch.setattr(boto3,'Session',lambda *a,**k:pytest.fail('credentials accessed'))
    assert invoke({'definition_digest':'x'})['production_ready'] is False
    assert offline()['result']['report']


def test_runner_defaults_offline():
    p=subprocess.run([sys.executable,'scripts/web_research_smoke.py'],capture_output=True,text=True)
    assert p.returncode==0 and json.loads(p.stdout)['result']['mode']=='offline-stub'
    p=subprocess.run([sys.executable,'scripts/web_research_smoke.py','--live'],capture_output=True,text=True)
    assert p.returncode==1

@pytest.mark.parametrize('case',['rpc-error','rpc-id','tool-error','bad-json','model-fail','model-tool','model-truncated'])
def test_gateway_protocol_errors(case):
    class Bad:
        def post(self,url,body,headers=None):
            if case=='rpc-error':return {'id':body['id'],'error':{'message':'upstream'}},{}
            if case=='rpc-id':return {'id':'wrong','result':{}},{}
            if case=='tool-error':return {'id':body['id'],'result':{'isError':True}},{}
            if case=='bad-json':return {'id':body['id'],'result':{'content':[{'type':'text','text':'not json'}]}},{}
            if case=='model-fail':raise GatewayError('failure')
            if case=='model-tool':return {'type':'message','stop_reason':'end_turn','content':[{'type':'tool_use'}]},{}
            return {'type':'message','stop_reason':'max_tokens','content':[{'type':'text','text':'partial'}]},{}
    m=example_manifest()['bindings']
    with pytest.raises(GatewayError):
        if case.startswith('model'):
            ModelGateway(Bad(),m['approved-claude']['endpoint'],m['approved-claude']['route'],'us-west-2',512).report('system','user')
        else:
            ToolGateway(Bad(),m['approved-fetch']['endpoint'],'research___web_fetch','us-west-2').fetch('https://example.com/report')


def test_iam_signing_uses_agentcore_service_without_real_credentials(monkeypatch):
    import httpx
    from botocore.credentials import Credentials
    from runtime.web_research.gateways import IAMTransport
    class Session:
        def get_credentials(self):return Credentials('synthetic-access','synthetic-secret','synthetic-session')
    calls=[]
    def handle(request):
        calls.append(request)
        return httpx.Response(200,json={'ok':True},headers={'x-amzn-requestid':'synthetic-request'})
    original=httpx.Client
    def client(**kwargs):
        assert kwargs['trust_env'] is False and kwargs['follow_redirects'] is False
        return original(transport=httpx.MockTransport(handle),**kwargs)
    monkeypatch.setattr(httpx,'Client',client)
    value,ids=IAMTransport('us-west-2',session=Session()).post(example_manifest()['bindings']['approved-fetch']['endpoint'],{'test':1})
    assert value=={'ok':True} and ids=={'x-amzn-requestid':'synthetic-request'}
    assert '/us-west-2/bedrock-agentcore/aws4_request' in calls[0].headers['authorization']


def test_react_renders_malicious_report_as_text(tmp_path):
    # Compile actual component using the already installed esbuild, render with React SSR.
    script=r'''
import {transformSync} from 'esbuild';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
const require=createRequire(import.meta.url);
const code=transformSync(readFileSync('src/ResearchReport.tsx','utf8'),{loader:'tsx',format:'cjs',jsx:'automatic'}).code;
const m={exports:{}};new Function('require','module','exports',code)(require,m,m.exports);
console.log(renderToStaticMarkup(React.createElement(m.exports.ResearchReport,{report:{report:'<img src="https://evil.test/pixel" onerror="alert(1)"><script>alert(1)</script>',citations:[]}})));
'''
    p=subprocess.run(['node','--input-type=module','-e',script],cwd='frontend',capture_output=True,text=True)
    assert p.returncode==0,p.stderr
    assert '<img' not in p.stdout and '<script' not in p.stdout and '&lt;img' in p.stdout


def test_source_package_is_executable_and_disabled(tmp_path):
    import zipfile
    from scripts.package_web_research import package
    path=tmp_path/'source.zip'
    result=package(example_manifest(),path)
    assert not result['deployment_ready']
    with zipfile.ZipFile(path) as z:
        assert 'main.py' in z.namelist() and 'tools/web_fetch/handler.py' in z.namelist()
        assert not json.loads(z.read('runtime/web_research/execution.json'))['human_approved']
        assert 'runtime/web_research/requirements.lock' in z.namelist()
        assert not any('.env' in n or 'sqlite' in n for n in z.namelist())


def test_saved_research_draft_version_refs_and_no_fixture_jobs(client,payload):
    login(client)
    payload.update(foundation_id='web-research',model_id='',tools=[],skills=['cited-brief'],
                   component_versions={'cited-brief':'1.0.0'},source='approved-public-web',
                   research=example_manifest()['definition']['research'])
    response=client.post('/api/agents',json=payload)
    assert response.status_code==201,response.text
    first=response.json()
    assert first['integration_status']=='NOT_CONFIGURED'
    assert first['research']==payload['research']
    assert first['prompt_ref']=='sha256:'+digest(payload['prompt'])
    assert first['dataset_ref']=='sha256:'+digest(payload['dataset'])
    assert first['skill_artifact']['artifact_digest']
    url='/api/agents/'+first['agent_id']
    assert client.post(url+'/deploy-test',json={'version':1,'idempotency_key':'research-test'}).status_code==503
    assert client.get(url+'/export').status_code==503
    payload['base_version']=1;payload['research']['question']='A changed research question'
    second=client.post(url+'/versions',json=payload).json()
    assert second['version']==2 and second['digest']!=first['digest']
    versions=client.get(url).json()['versions']
    assert len(versions)==2 and {v['digest'] for v in versions}=={first['digest'],second['digest']}
    assert client.post(url+'/versions',json=payload).status_code==409

@pytest.mark.parametrize('raw', ['null', '[]', '{}', 'not JSON', '{"report":"uncited","citations":[]}',
    '{"report":42,"citations":[]}', '{"report":"[src-forged]","citations":[null]}'])
def test_malformed_report_fails_closed(raw):
    with pytest.raises(GatewayError):
        check_report(raw, offline()['result']['source_evidence'])


@pytest.mark.parametrize('approved', ['true', 'false', 1])
def test_manifest_requires_boolean_approval(approved):
    m = example_manifest()
    m['bindings']['approved-fetch']['approved'] = approved
    with pytest.raises(ValueError):
        validate_manifest(m)


def test_package_excludes_unrelated_operator_files(tmp_path, monkeypatch):
    import zipfile
    import scripts.package_web_research as packager
    # Model an operator directory without writing any credentials or source-tree debris.
    def forbidden(self, *a, **k):
        raise AssertionError('Packaging must not recursively collect operator files')
    monkeypatch.setattr(Path, 'rglob', forbidden)
    target = tmp_path / 'source.zip'
    packager.package(example_manifest(), target)
    with zipfile.ZipFile(target) as z:
        assert 'runtime/web_research/Dockerfile' in z.namelist()
        assert 'docs/WEB-RESEARCH-SOURCE.md' in z.namelist()
        assert len(z.namelist()) == 24
    with pytest.raises(FileExistsError):
        packager.package(example_manifest(), target)
