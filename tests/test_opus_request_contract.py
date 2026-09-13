"""Bounded Opus contract tests: synthetic responses, zero network calls."""
import pytest
from foundation_harness.opus_messages import build_request, read_response


def test_exact_profile_and_no_thinking_tools_or_streaming():
    body=build_request('us.anthropic.claude-opus-5','Short answer.', 'Explain rain.',256)
    assert body=={'model':'us.anthropic.claude-opus-5','system':'Short answer.',
        'messages':[{'role':'user','content':'Explain rain.'}], 'max_tokens':256,
        'stream':False,'thinking':{'type':'disabled'}}


@pytest.mark.parametrize('tokens',[0,257,True,1.5])
def test_output_budget_rejects_invalid_values(tokens):
    with pytest.raises(ValueError):build_request('us.anthropic.claude-opus-5','s','q',tokens)


@pytest.mark.parametrize('model',['global.anthropic.claude-opus-5','anthropic.claude-opus-5','us.anthropic.claude-sonnet-5'])
def test_model_must_equal_reviewed_profile(model):
    with pytest.raises(ValueError):build_request(model,'s','q',16)


def test_response_identity_is_explicit_and_text_only():
    response={'type':'message','model':'synthetic-response','stop_reason':'end_turn',
              'content':[{'type':'text','text':'Synthetic answer.'}],
              'usage':{'input_tokens':10,'output_tokens':5}}
    assert read_response(response,('synthetic-response',),256)=='Synthetic answer.'
    with pytest.raises(ValueError):read_response(response,(),256)
    with pytest.raises(ValueError):read_response(response,('synthetic',),256)
    for block in [{'type':'thinking','thinking':'not expected'},{'type':'tool_use','name':'tool'}]:
        with pytest.raises(ValueError):read_response({**response,'content':[block]},('synthetic-response',),256)
    with pytest.raises(ValueError):read_response({**response,'usage':{'input_tokens':10,'output_tokens':257}},('synthetic-response',),256)
