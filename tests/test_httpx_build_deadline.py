"""Real HTTPX preparation, mocked network boundary; zero AWS traffic."""
from types import SimpleNamespace
import httpx
import pytest
from botocore.credentials import Credentials
from foundation_harness.transport import IAMTransport,capture_deadline
URL='https://gab-foundation-model-m0-example.gateway.bedrock-agentcore.us-west-2.amazonaws.com/bedrockrt/v1/messages'

@pytest.mark.parametrize('delay,expected',[(61,0),(3,1)])
def test_httpx_build_deadline(monkeypatch,delay,expected):
    now=[100];sent=[];built=[]
    original_init=httpx.AsyncClient.__init__
    original_build=httpx.AsyncClient.build_request
    def wire(request):
        sent.append(request.extensions['timeout'])
        return httpx.Response(200,json={'synthetic':True})
    def init(self,*args,**kwargs):
        kwargs['transport']=httpx.MockTransport(wire)
        original_init(self,*args,**kwargs)
    def build(self,*args,**kwargs):
        request=original_build(self,*args,**kwargs)
        now[0]+=delay;built.append(True)
        return request
    monkeypatch.setattr(httpx.AsyncClient,'__init__',init)
    monkeypatch.setattr(httpx.AsyncClient,'build_request',build)
    transport=IAMTransport(SimpleNamespace(get_credentials=lambda:Credentials('SYNTHETIC','synthetic-secret')))
    with capture_deadline(110,lambda:now[0]):
        if expected:
            value,_=transport.post(URL,{'synthetic':True},{},10)
            assert value=={'synthetic':True}
        else:
            with pytest.raises(TimeoutError):transport.post(URL,{'synthetic':True},{},10)
    assert built==[True]
    assert len(sent)==expected
    if expected:assert all(x==7 for x in sent[0].values())


def test_deadline_crossed_after_prep_before_dispatch_zero_sends(monkeypatch):
    """FINAL-GATE boundary: deadline crosses AFTER request preparation / the
    before_send hook but BEFORE the actual transport dispatch. The real send count
    MUST be 0 (spy/counter on the actual MockTransport dispatch, not ticket state).
    Regression for: deadline check happening too early / not at the last gate.
    """
    now=[100];sent=[]
    def wire(request):
        sent.append(request.extensions.get('timeout'))
        return httpx.Response(200,json={'synthetic':True})
    original_init=httpx.AsyncClient.__init__
    def init(self,*args,**kwargs):
        # Append a request hook that runs AFTER the transport's own before_send and
        # advances the clock past the deadline, modelling prep/connection time
        # elapsing right before dispatch. This is the exact defect window.
        hooks=dict(kwargs.get('event_hooks') or {})
        request_hooks=list(hooks.get('request',[]))
        async def elapse_before_dispatch(request):
            now[0]+=61  # capture_deadline is 110; now 100 -> 161 (crossed)
        request_hooks.append(elapse_before_dispatch)
        hooks['request']=request_hooks
        kwargs['event_hooks']=hooks
        kwargs['transport']=httpx.MockTransport(wire)
        original_init(self,*args,**kwargs)
    monkeypatch.setattr(httpx.AsyncClient,'__init__',init)
    transport=IAMTransport(SimpleNamespace(get_credentials=lambda:Credentials('SYNTHETIC','synthetic-secret')))
    with capture_deadline(110,lambda:now[0]):
        with pytest.raises(TimeoutError):
            transport.post(URL,{'synthetic':True},{},10)
    assert sent==[]  # ACTUAL final dispatch fired ZERO times


def test_deadline_crossed_during_build_zero_sends(monkeypatch):
    """Deadline crosses DURING httpx request construction (build_request). The final
    gate must still yield 0 real sends.
    """
    now=[100];sent=[]
    original_init=httpx.AsyncClient.__init__
    original_build=httpx.AsyncClient.build_request
    def wire(request):
        sent.append(1);return httpx.Response(200,json={'synthetic':True})
    def init(self,*args,**kwargs):
        kwargs['transport']=httpx.MockTransport(wire);original_init(self,*args,**kwargs)
    def build(self,*args,**kwargs):
        request=original_build(self,*args,**kwargs);now[0]+=61;return request
    monkeypatch.setattr(httpx.AsyncClient,'__init__',init)
    monkeypatch.setattr(httpx.AsyncClient,'build_request',build)
    transport=IAMTransport(SimpleNamespace(get_credentials=lambda:Credentials('SYNTHETIC','synthetic-secret')))
    with capture_deadline(110,lambda:now[0]):
        with pytest.raises(TimeoutError):
            transport.post(URL,{'synthetic':True},{},10)
    assert sent==[]  # zero real dispatches
