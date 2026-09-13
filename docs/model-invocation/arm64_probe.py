import sys,json,platform,hashlib
from pathlib import Path
sys.path.insert(0,'/artifact')
import pydantic_core
from foundation_harness.config import load_config,digest
from foundation_harness.opus_messages import build_request,read_response
from foundation_harness.transport import IAMTransport
from botocore.credentials import Credentials
assert platform.machine()=='aarch64'
assert pydantic_core.__file__.startswith('/artifact/')
raw=json.loads(Path('/artifact/runtime/custom_foundation/harness.json').read_text());cfg=load_config(raw,digest(raw))
assert cfg.model_dump(mode='json')==raw
class Session:
 def get_credentials(self):return Credentials('SYNTHETIC_KEY','synthetic_secret','synthetic_session')
class Wire(IAMTransport):
 async def _send(self,url,data,headers,timeout):
  assert url==cfg.model.endpoint
  auth=next(v for k,v in headers.items() if k.lower()=='authorization');assert '/bedrock-agentcore/aws4_request' in auth
  b=json.loads(data);assert b['thinking']=={'type':'disabled'}
  return {'type':'message','model':'synthetic-opus-response','stop_reason':'end_turn','content':[{'type':'text','text':'Synthetic ARM64 response'}],'usage':{'input_tokens':2,'output_tokens':4}},{}
body=build_request(cfg.model.requestModel,'Short answer.','Synthetic question',256)
r,_=Wire(Session()).post(cfg.model.endpoint,body,{'anthropic-version':'2023-06-01'},5)
assert read_response(r,cfg.model.responseModelAllowlist,256)=='Synthetic ARM64 response'
try:read_response(r,(),256)
except ValueError:pass
else:raise AssertionError('empty response identity accepted')
try:Wire(Session()).post('https://bedrock-runtime.us-west-2.amazonaws.com/anthropic/v1/messages',{}, {},5)
except ValueError:pass
else:raise AssertionError('direct Runtime accepted')
print(json.dumps({'arch':platform.machine(),'python':platform.python_version(),'packaged_native_import':True,'manifest_roundtrip':True,'codec_executed':True,'signed_passthrough_mock':True,'empty_allowlist_denied':True,'direct_runtime_denied':True,'network':'disabled','inference_calls':0}))
