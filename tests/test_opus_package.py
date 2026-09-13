"""Execute imports from generated source ZIP, not checkout. Dependencies are host venv."""
import json
import os
import subprocess
import sys
import zipfile
from scripts.package_foundation import save_config, package, source_digest
from foundation_harness.config import load_config,digest
from .test_opus_modelclient import opus_config
from .test_foundation_executor import config,setup,run,message


def deployed_haiku():
    raw=config();raw['model'].update(transport='runtime-passthrough',
        route='bedrockrt/us.anthropic.claude-haiku-4-5-20251001-v1:0',
        endpoint='https://gab-foundation-model-m0-example.gateway.bedrock-agentcore.us-west-2.amazonaws.com/bedrockrt/v1/messages',
        requestModel='us.anthropic.claude-haiku-4-5-20251001-v1:0',
        responseModels=['anthropic.claude-haiku-4-5-20251001-v1:0'])
    return raw


def test_deployed_haiku_manifest_roundtrip_and_client():
    raw=deployed_haiku()
    assert load_config(raw,digest(raw)).model_dump(mode='json')==raw
    e,b,a,t,budget=setup(raw,[{**message(),'model':raw['model']['responseModels'][0]}])
    assert run(e,b,budget)['status']=='SUCCEEDED'
    assert t.calls[0][1]['model']==raw['model']['requestModel']


def test_generated_zip_imports_and_executes_codec_and_models(tmp_path):
    raw=opus_config();raw['foundation']['digest']=source_digest()
    saved=save_config(raw,tmp_path/'config');dest=tmp_path/'foundation.zip'
    package(saved,dest,mode='base')
    with zipfile.ZipFile(dest) as z:
        assert 'foundation_harness/opus_messages.py' in z.namelist()
    script='''
import sys,json
sys.path.insert(0,sys.argv[1])
from foundation_harness import opus_messages as codec
from foundation_harness.config import load_config,digest
from foundation_harness.model_client import ModelClient
assert sys.argv[1] in codec.__file__,codec.__file__
raw=json.loads(sys.argv[2]);haiku=json.loads(sys.argv[3])
for c in [raw,haiku]:assert load_config(c,digest(c)).model_dump(mode='json')==c
body=codec.build_request('us.anthropic.claude-opus-5','s','q',256)
assert body['thinking']=={'type':'disabled'}
r={'type':'message','model':'synthetic','stop_reason':'end_turn','content':[{'type':'text','text':'fixture response'}],'usage':{'input_tokens':1,'output_tokens':2}}
assert codec.read_response(r,('synthetic',),256)=='fixture response'
print('ZIP_IMPORT_AND_CODEC_PASS')
'''
    env=os.environ.copy();env.pop('PYTHONPATH',None)
    r=subprocess.run([sys.executable,'-I','-c',script,str(dest),json.dumps(raw),json.dumps(deployed_haiku())],cwd=tmp_path,env=env,text=True,capture_output=True)
    assert r.returncode==0,r.stderr
    assert 'ZIP_IMPORT_AND_CODEC_PASS' in r.stdout
