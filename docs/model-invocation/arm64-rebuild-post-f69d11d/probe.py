import sys,json,platform
sys.path.insert(0,'/artifact')
import httpx,pydantic_core
from foundation_harness.transport import IAMTransport,capture_deadline
from botocore.credentials import Credentials
from types import SimpleNamespace
assert platform.machine()=='aarch64'
assert httpx.__file__.startswith('/artifact/') and pydantic_core.__file__.startswith('/artifact/')
URL='https://gab-foundation-model-m0-example.gateway.bedrock-agentcore.us-west-2.amazonaws.com/bedrockrt/v1/messages'
original_init=httpx.AsyncClient.__init__;original_build=httpx.AsyncClient.build_request
results=[]
for delay,expected in [(61,0),(3,1)]:
 now=[100];sent=[];built=[]
 def wire(request):
  sent.append(request.extensions['timeout']);return httpx.Response(200,json={'synthetic':True})
 def init(self,*args,**kwargs):
  kwargs['transport']=httpx.MockTransport(wire);original_init(self,*args,**kwargs)
 def build(self,*args,**kwargs):
  request=original_build(self,*args,**kwargs);now[0]+=delay;built.append(True);return request
 httpx.AsyncClient.__init__=init;httpx.AsyncClient.build_request=build
 t=IAMTransport(SimpleNamespace(get_credentials=lambda:Credentials('SYNTHETIC','synthetic-secret')))
 rejected=False
 try:
  with capture_deadline(110,lambda:now[0]):t.post(URL,{'synthetic':True},{},10)
 except TimeoutError:rejected=True
 assert rejected==(expected==0)
 assert built==[True] and len(sent)==expected
 if expected:assert all(x==7 for x in sent[0].values())
 results.append({'build_delay':delay,'httpx_transport_calls':len(sent),'deadline_rejected':rejected,'timeouts':sent})
httpx.AsyncClient.__init__=original_init;httpx.AsyncClient.build_request=original_build
print(json.dumps({'arch':platform.machine(),'python':platform.python_version(),'httpx':httpx.__version__,'packaged_dependencies':True,'real_httpx_request_preparation':True,'network':'disabled/mocktransport','cases':results,'cloud_runtime_tested':False,'inference_calls':0}))
