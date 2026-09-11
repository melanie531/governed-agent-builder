"""SDK metadata and in-memory test doubles, never cloud execution."""
import pytest
from botocore.session import Session
from botocore.validate import validate_parameters
from backend.runtime_deployment import DeploymentPolicy, RuntimeDeploymentAdapter, LiveBudget, frozen_manifest, require_live_evidence
from backend.aws_adapter import IntegrationNotConfigured
from .conftest import login, create

ACCOUNT='9988'+'77665544'
ROLE=f'arn:aws:iam::{ACCOUNT}:role/synthetic-runtime'
ARN=f'arn:aws:bedrock-agentcore:us-west-2:{ACCOUNT}:runtime/synthetic-abc'

class Control:
    def __init__(self):self.calls=[]
    def operation(self,name,kw):
        validate_parameters(kw,Session().get_service_model('bedrock-agentcore-control').operation_model(name).input_shape)
        self.calls.append((name,kw))
        return {'agentRuntimeId':'synthetic-abc','agentRuntimeArn':ARN,'agentRuntimeVersion':'3','status':'CREATING'}
    def create_agent_runtime(self,**kw):return self.operation('CreateAgentRuntime',kw)
    def update_agent_runtime(self,**kw):return self.operation('UpdateAgentRuntime',kw)
    def get_agent_runtime(self,**kw):
        response=self.operation('GetAgentRuntime',kw)
        return {**response,'status':'READY','environmentVariables':{'DEFINITION_DIGEST':'a'*64,'MANIFEST_DIGEST':'b'*64}}


def adapter(allow=True):
    client=Control()
    policy=DeploymentPolicy('us-west-2',ACCOUNT,frozenset([ROLE]),'synthetic-artifact-bucket',frozenset(['synthetic-abc']),allow)
    return RuntimeDeploymentAdapter(client,policy)


def arguments():
    return dict(name='synthetic_agent',role=ROLE,artifact_key='approved/main.zip',artifact_version='immutable-synthetic-version',definition_digest='a'*64,manifest_digest='b'*64,
                network_configuration={'networkMode':'VPC','networkModeConfig':{'subnets':['subnet-synthetic'],'securityGroups':['sg-synthetic']}})


@pytest.mark.parametrize('update',[False,True])
def test_submit_get_exact_sdk_shapes(update):
    a=adapter();args=arguments()
    if update:args['runtime_id']='synthetic-abc'
    binding=a.submit(**args)
    assert binding['stage']=='WAIT_RUNTIME' and binding['runtime_version']=='3'
    ready=a.readiness(binding)
    assert ready['stage']=='INVOKE'
    assert a.client.calls[-1][1]['agentRuntimeVersion']=='3'
    request=a.client.calls[0][1]
    assert request['agentRuntimeArtifact']['codeConfiguration']['code']['s3']['versionId']=='immutable-synthetic-version'
    assert request['environmentVariables']['DEFINITION_DIGEST']=='a'*64


@pytest.mark.parametrize('change',[{'role':'arn:unapproved'},{'artifact_version':''},{'runtime_id':'someone-elses-runtime'},{'network_configuration':{'networkMode':'PUBLIC'}}])
def test_mutation_safety_guards(change):
    a=adapter()
    with pytest.raises((ValueError,IntegrationNotConfigured)):a.submit(**{**arguments(),**change})
    assert a.client.calls==[]


def test_no_mutations_default():
    a=adapter(False)
    with pytest.raises(IntegrationNotConfigured):a.submit(**arguments())
    assert a.client.calls==[]


def test_readiness_digest_mismatch():
    a=adapter();b=a.submit(**arguments());b['definition_digest']='c'*64
    with pytest.raises(IntegrationNotConfigured):a.readiness(b)


@pytest.mark.parametrize('budget',[LiveBudget(),LiveBudget(True,0,100),LiveBudget(True,21,100),LiveBudget(True,1,4097)])
def test_budget_guard(budget):
    with pytest.raises(IntegrationNotConfigured):budget.validate()


def test_positive_budget():
    LiveBudget(True,1,128).validate()


def test_frozen_definition_propagation_and_missing_evidence(client,payload):
    login(client);definition=create(client,payload)
    bindings={k:{'approved':True,'version':v,'read_only':True,'instruction':'Cite known sources'} for k,v in definition['component_versions'].items()}
    manifest=frozen_manifest(definition,bindings)
    assert manifest['definition']['prompt']==payload['prompt']
    assert manifest['definition']['dataset']==payload['dataset']
    assert manifest['definition']['rubric']==payload['rubric']
    assert manifest['bindings']['citations']['instruction']=='Cite known sources'
    assert not manifest['execution_ready']
    with pytest.raises(ValueError):frozen_manifest({**definition,'prompt':'tampered instructions'},bindings)
    with pytest.raises(ValueError):frozen_manifest(definition,{})
    with pytest.raises(IntegrationNotConfigured):require_live_evidence(None,definition,'3')
    evidence={'mode':'live','definition_digest':definition['digest'],'dataset_ref':definition['dataset_ref'],'rubric_ref':definition['rubric_ref'],'runtime_version':'3','trace_reference':'synthetic-only','judge_reference':'synthetic-only'}
    assert require_live_evidence(evidence,definition,'3')['passed'] is False
    with pytest.raises(IntegrationNotConfigured):require_live_evidence(evidence,definition,'4')
