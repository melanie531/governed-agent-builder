"""Synthetic Engine->ModelClient->transport regression; no live inference."""
import copy
import pytest
from foundation_harness.config import load_config,digest
from .test_foundation_executor import config,setup,run,message


def opus_config():
    raw=config();raw['model'].update(protocol='messages-passthrough',
        endpoint='https://gab-foundation-model-m0-example.gateway.bedrock-agentcore.us-west-2.amazonaws.com/bedrockrt/v1/messages',
        route='us.anthropic.claude-opus-5',requestModel='us.anthropic.claude-opus-5',
        responseModelAllowlist=['synthetic-opus-response'])
    raw['tools']=[];raw['allowedTools']=[];raw['skills']=[]
    raw['limits'].update(maxIterations=1,maxModelCalls=1,maxToolCalls=0,maxOutputTokens=256)
    return raw


def test_engine_sends_explicit_opus_binding_and_disables_thinking():
    raw=opus_config();response={**message(),'model':'synthetic-opus-response'}
    engine,binding,authority,transport,budget=setup(raw,[response])
    result=run(engine,binding,budget)
    assert result['status']=='SUCCEEDED' and result['model_calls']==1 and result['tool_calls']==0
    endpoint,body,headers=transport.calls[0]
    assert endpoint==raw['model']['endpoint']
    assert body['model']==raw['model']['requestModel'] and body['max_tokens']==256
    assert body['thinking']=={'type':'disabled'} and body['stream'] is False
    assert 'tools' not in body and 'anthropic_version' not in body
    assert headers['anthropic-version']=='2023-06-01'


@pytest.mark.parametrize('field',['requestModel','responseModelAllowlist'])
def test_missing_explicit_identity_fails(field):
    raw=opus_config();del raw['model'][field]
    with pytest.raises(ValueError):load_config(raw,digest(raw))


def test_changed_identity_invalidates_manifest():
    raw=opus_config();approved=digest(raw);raw['model']['responseModelAllowlist']=['different']
    with pytest.raises(ValueError,match='MANIFEST_DIGEST'):load_config(raw,approved)


@pytest.mark.parametrize('change',[{'maxModelCalls':2},{'maxToolCalls':1},{'maxIterations':2}])
def test_first_opus_budget_must_be_one_call_no_tools(change):
    raw=opus_config();raw['limits'].update(change)
    with pytest.raises(ValueError):load_config(raw,digest(raw))


def test_response_not_in_allowlist_fails_without_retry():
    engine,binding,authority,transport,budget=setup(opus_config(),[message()])
    result=run(engine,binding,budget)
    assert result['status']=='FAILED' and result['model_calls']==1 and len(transport.calls)==1


def test_manifest_roundtrip_and_unauthorized_dispatch():
    for raw in [config(),opus_config()]:
        assert load_config(raw,digest(raw)).model_dump(mode='json')==raw
    engine,binding,authority,transport,budget=setup(opus_config())
    authority.grants=frozenset()
    assert run(engine,binding,budget)['status']=='DENIED'
    assert not transport.calls


def test_direct_runtime_endpoint_rejected():
    raw=opus_config();raw['model']['endpoint']='https://bedrock-runtime.us-west-2.amazonaws.com/anthropic/v1/messages'
    with pytest.raises(ValueError):load_config(raw,digest(raw))
