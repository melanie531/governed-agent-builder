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
