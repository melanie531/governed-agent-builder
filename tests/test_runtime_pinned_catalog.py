"""Synthetic contract tests; no credentials, cloud client, or live permission claims."""
import copy
import json
import pytest
from backend.runtime_model_catalog import RuntimeModelCatalog, route_revision, validate_source
from backend.live_catalog import DIGEST_FORMAT, configured_catalog

ACCOUNT='9988'+'77665544'; REGION='us-west-2'; GID='gab-foundation-synthetic'; TID='SyntheticTarget'
ARN=f'arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT}:gateway/{GID}'
MODEL='us.anthropic.claude-haiku-4-5-20251001-v1:0'
RESPONSE='anthropic.claude-haiku-4-5-20251001-v1:0'

def fixture():
 g={'gatewayArn':ARN,'status':'READY','authorizerType':'AWS_IAM','roleArn':f'arn:aws:iam::{ACCOUNT}:role/synthetic',
    'policyEngineConfiguration':{'arn':f'arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT}:policy-engine/synthetic','mode':'ENFORCE'}}
 t={'gatewayArn':ARN,'targetId':TID,'status':'READY','name':'bedrockrt',
    'targetConfiguration':{'http':{'passthrough':{'endpoint':f'https://bedrock-runtime.{REGION}.amazonaws.com/anthropic','protocolType':'INFERENCE','schema':{'source':{'inlinePayload':'synthetic-schema'}}}}},
    'credentialProviderConfigurations':[{'credentialProviderType':'GATEWAY_IAM_ROLE','credentialProvider':{'iamCredentialProvider':{'service':'bedrock','region':REGION}}}],
    'metadataConfiguration':{'allowedRequestHeaders':['anthropic-version','content-type']}}
 b={'target_name':'bedrockrt','request_model':MODEL,'response_models':[RESPONSE],'path':'/v1/messages'}
 rid=f'model:{GID}:{TID}:{MODEL}';v=route_revision(g,t,b)
 s={'approved':True,'gateway_id':GID,'gateway_arn':ARN,'target_id':TID,'region':REGION,'bindings':[b],
    'exposure':{rid:{'approved':True,'version':v,'digest_format':DIGEST_FORMAT,'approval_sha256':v,'workspaces':['research'],
                     'requestable':True,'owner':'Synthetic owner','data_handling':'Synthetic review'}}}
 return g,t,s

class Client:
 def __init__(self,g,t):self.g,self.t=g,t;self.calls=[]
 def get_gateway(self,**kw):self.calls.append(('get_gateway',kw));return copy.deepcopy(self.g)
 def get_gateway_target(self,**kw):self.calls.append(('get_gateway_target',kw));return copy.deepcopy(self.t)


def test_full_profile_colon_live_target_not_enumeration_or_authorization():
 g,t,s=fixture();validate_source(s,ACCOUNT,REGION);c=Client(g,t)
 row,=RuntimeModelCatalog(c,s).records()
 assert row['model_id']==MODEL and row['id'].endswith(':0')
 assert row['fixture'] is False and row['discoverable'] is True
 assert not row['integration_ready'] and not row['execution_ready']
 assert row['execution_binding']['status']=='unverified'
 assert len(c.calls)==2
 assert row['discovery_method']=='operator-pinned-route-with-live-target-readback'

@pytest.mark.parametrize('mutation',[
 lambda g,t,s:g['policyEngineConfiguration'].update(mode='LOG_ONLY'),
 lambda g,t,s:g.update(authorizerType='NONE'),
 lambda g,t,s:g.update(protocolType='MCP'),
 lambda g,t,s:g.update(interceptorConfigurations=[{}]),
 lambda g,t,s:t['targetConfiguration']['http']['passthrough'].update(endpoint='https://example.com/anthropic'),
 lambda g,t,s:t['credentialProviderConfigurations'][0]['credentialProvider']['iamCredentialProvider'].update(service='wrong'),
 lambda g,t,s:t['metadataConfiguration'].update(allowedRequestHeaders=['content-type']),
 lambda g,t,s:t.update(name='different'),
 lambda g,t,s:t.update(status='UPDATING'),
 lambda g,t,s:t['targetConfiguration']['http']['passthrough']['schema']['source'].update(inlinePayload='changed'),
 lambda g,t,s:s['bindings'][0]['response_models'].append('anthropic.claude-other'),
])
def test_drift_fails_closed(mutation):
 g,t,s=fixture();mutation(g,t,s)
 with pytest.raises(ValueError):RuntimeModelCatalog(Client(g,t),s).records()

@pytest.mark.parametrize('mutation',[
 lambda s:s.update(gateway_arn=ARN.replace(ACCOUNT,'0000'+'00000000')),
 lambda s:s['bindings'][0].update(path='/v1/models'),
 lambda s:s['bindings'][0].update(request_model='short-alias'),
 lambda s:s['bindings'].append(copy.deepcopy(s['bindings'][0])),
 lambda s:s.update(exposure={}),
])
def test_invalid_source_rejected(mutation):
 _,_,s=fixture();mutation(s)
 with pytest.raises(ValueError):validate_source(s,ACCOUNT,REGION)


def test_empty_legacy_model_gateways_can_use_runtime_source_without_fake_list():
 g,t,s=fixture();c=Client(g,t)
 class Session:
  region_name=REGION
  def client(self,service,**kw):assert service=='sts';return self
  def get_caller_identity(self):return {'Account':ACCOUNT}
 config={'schema_version':2,'approved':True,'binding':{'expected_account':ACCOUNT,'region':REGION,'owner_approval':'Synthetic'},
         'model_gateways':[],'registries':[],'runtime_model_routes':[s]}
 catalog=configured_catalog(config,client_factory=lambda service,region:c,session=Session())
 row,=catalog.records();assert row['model_id']==MODEL
 assert catalog.source_status()['ModelGateway']['connection_state']=='connected'
 assert len(c.calls)==2
