import pytest
from botocore.session import Session
from backend.aws_adapter import AWSConfig, AgentCoreGatewayAdapter, AgentCoreRuntimeAdapter, IntegrationNotConfigured

def test_unconfigured_aws_fails_actionably():
    with pytest.raises(IntegrationNotConfigured,match='Missing AWS configuration'):
        AgentCoreRuntimeAdapter(AWSConfig())

def test_two_gateway_adapters_remain_explicitly_unconnected():
    for purpose in ('Model','Tool'):
        with pytest.raises(IntegrationNotConfigured,match='not integrated'):
            AgentCoreGatewayAdapter(purpose,'https://placeholder.invalid').catalog()

def test_runtime_sdk_operation_shape_offline():
    # Load installed SDK metadata only: no client, credentials or network calls.
    service=Session().get_service_model('bedrock-agentcore')
    operation=service.operation_model('InvokeAgentRuntime')
    assert {'agentRuntimeArn','qualifier','runtimeSessionId','payload','contentType','accept'}.issubset(operation.input_shape.members)
    assert 'response' in operation.output_shape.members

def test_runtime_adapter_payload_with_in_memory_fake():
    account='0000'+'00000000'
    arn=f'arn:aws:bedrock-agentcore:us-west-2:{account}:runtime/synthetic'
    config=AWSConfig(region='us-west-2',runtime_arn=arn,runtime_qualifier='TEST',model_gateway_url='https://model.invalid',tool_gateway_url='https://tool.invalid',allow_aws_calls=True)
    class Fake:
        def invoke_agent_runtime(self,**kwargs):return kwargs
    result=AgentCoreRuntimeAdapter(config,Fake()).invoke('synthetic-digest','Aurora','s'*33)
    assert result['qualifier']=='TEST' and b'synthetic-digest' in result['payload']
