import base64
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from infra.foundation import template
from infra.model_gate import overlay, inspect_change_set, ADDED, MODIFIED
from tools.model_gate.handler import handler, APPROVED_MODEL, MAX_BODY_BYTES


def event(payload=None, raw=None, **request):
    if raw is None:
        raw = json.dumps(payload or {'model': APPROVED_MODEL, 'messages': [{'role': 'user', 'content': 'Synthetic check'}],
                                     'max_tokens': 16, 'stream': False}).encode()
    return {'interceptorInputVersion': '1.0', 'http': {'gatewayRequest': {
        'path': '/inference/v1/messages', 'httpMethod': 'POST', 'body': base64.b64encode(raw).decode(), **request}}}


def denied(e):
    out = handler(e, None)
    assert set(out['http']) == {'transformedGatewayResponse'}
    response = out['http']['transformedGatewayResponse']
    assert response['statusCode'] == 403
    assert json.loads(base64.b64decode(response['body'])) == {'error': {'code': 'MODEL_GATE_DENIED'}}


def test_allow_exact_nonstream():
    out = handler(event(), None)
    request = out['http']['transformedGatewayRequest']
    body = json.loads(base64.b64decode(request['body']))
    assert body['model'] == APPROVED_MODEL and body['stream'] is False
    assert set(request) == {'body'}


@pytest.mark.parametrize('raw', [b'', b'{', b'null', b'[]', b'NaN', b'\xff', b'{} {}',
    b'{"model":"a","model":"b"}', b'['*1200+b']'*1200, b' '* (MAX_BODY_BYTES+1)])
def test_bad_json_bytes(raw):
    denied(event(raw=raw))


@pytest.mark.parametrize('body', ['$$$', 'e30=\n', 'e30', '====', {}, None, 123])
def test_base64(body):
    denied(event(body=body))


@pytest.mark.parametrize('patch', [
    {'model':'anthropic.claude-haiku-4-5'}, {'model':'other/anthropic.claude-haiku-4-5'},
    {'model':APPROVED_MODEL+'/'}, {'model':'claude/anthropic.claude-sonnet-4-6'},
    {'stream':True}, {'stream':0}, {'stream':None}, {'stream':'false'},
    {'max_tokens':True}, {'max_tokens':0}, {'max_tokens':257}, {'max_tokens':1.0},
    {'tools':[]}, {'thinking':{}}, {'metadata':{}}, {'caller':'admin'},
    {'messages':[]}, {'messages':[{'role':'system','content':'x'}]},
    {'messages':[{'role':'user','content':[{'type':'image'}]}]}, {'system':[]},
])
def test_controls(patch):
    e = event(); payload = json.loads(base64.b64decode(e['http']['gatewayRequest']['body']))
    denied(event(payload={**payload, **patch}))


@pytest.mark.parametrize('path', ['/v1/messages', '/inference/v1/responses', '/mcp',
    '/inference/v1/messages/', '/inference/v1/messages?stream=true', '/inference/v1/%6dessages'])
def test_paths(path):
    denied(event(path=path))


def test_method_protocol_shape():
    denied(event(httpMethod='GET'))
    for value in [None, [], {}, {'interceptorInputVersion':'2.0'}, {'mcp':{}}]:
        denied(value)
    e=event(); e['http']['gatewayResponse']={}; denied(e)


def test_forged_headers_do_not_admit_or_log(caplog):
    e=event(headers={'caller':'secret-sentinel', 'Authorization':'secret-sentinel'})
    e['http']['gatewayRequest']['path']='/mcp'
    denied(e)
    handler(event(), SimpleNamespace(client_context=SimpleNamespace(custom={'REQUEST_ID':'secret-sentinel'})))
    assert 'secret-sentinel' not in caplog.text


def test_overlay_preserves_guards_and_all_other_resources():
    old=template(); new=overlay(old)
    assert new['Resources']['ModelGateway']['Properties']['PolicyEngineConfiguration']==old['Resources']['ModelGateway']['Properties']['PolicyEngineConfiguration']
    assert new['Resources']['ModelRole']['Properties']['Policies'][0]['PolicyDocument']['Statement'][:-1]==old['Resources']['ModelRole']['Properties']['Policies'][0]['PolicyDocument']['Statement']
    for k,v in old['Resources'].items():
        if k not in MODIFIED: assert new['Resources'][k]==v
    assert set(new['Resources'])-set(old['Resources'])==ADDED
    assert new['Resources']['ModelGateLogs']['Properties']['RetentionInDays']==7
    assert new['Resources']['ModelGateway']['Properties']['InterceptorConfigurations'][0]['InputConfiguration']=={'PassRequestHeaders':False}
    assert template()==old


def changes():
    return {'Status':'CREATE_COMPLETE','ExecutionStatus':'AVAILABLE','Changes':[
        {'ResourceChange': {'LogicalResourceId':n,'Action':'Add' if n in ADDED else 'Modify',
         'Replacement':'False','Details':[]}} for n in sorted(ADDED|MODIFIED)]}


def test_changeset_guard():
    assert inspect_change_set(changes())
    for field,value in [('Replacement','True'),('Action','Remove'),('LogicalResourceId','ToolsGateway')]:
        c=changes(); r=next(x['ResourceChange'] for x in c['Changes'] if x['ResourceChange']['LogicalResourceId']=='ModelRole')
        r[field]=value
        with pytest.raises(ValueError): inspect_change_set(c)
    c=changes(); c['NextToken']='next'
    with pytest.raises(ValueError): inspect_change_set(c)
