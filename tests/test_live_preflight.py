import pytest
from scripts.live_preflight import check_sdk, readonly, validate_manifest


def test_offline_sdk_preflight():
    check_sdk()


def test_target_mismatch_stops_before_any_resource_read():
    class Identity:
        def get_caller_identity(self):return {'Account':'1111'+'00000000'}
    class Session:
        def client(self,name):
            assert name=='sts'
            return Identity()
    with pytest.raises(ValueError,match='identity mismatch'):
        readonly({'target_account':'2222'+'00000000'},Session())


def test_missing_manifest_and_first_party_models_blocked():
    with pytest.raises(ValueError):validate_manifest({})
    account='9988'+'77665544'
    manifest=dict(region='us-west-2',target_account=account,model_gateway_id='synthetic-model',tool_gateway_id='synthetic-tool',registry_id='synthetic-registry',runtime_role_arn=f'arn:aws:iam::{account}:role/synthetic',artifact_bucket='synthetic-bucket',bedrock_model_id='amazon.nova-test')
    with pytest.raises(ValueError):validate_manifest(manifest)


def preflight_session(calls):
    account = '9988' + '77665544'
    class Client:
        def __init__(self, name): self.name = name
        def get_caller_identity(self): return {'Account': account}
        def get_gateway(self, **kw): return {'status': 'READY', 'gatewayArn': f'arn:aws:bedrock-agentcore:us-west-2:{account}:gateway/x'}
        def list_gateway_targets(self, **kw): return {'items': [{'status': 'READY'}]}
        def get_registry(self, **kw): return {'registryArn': f'arn:aws:bedrock-agentcore:us-west-2:{account}:registry/x'}
        def get_inference_profile(self, **kw): calls.append(('profile', kw))
        def get_foundation_model(self, **kw): calls.append(('foundation', kw))
        def get_role(self, **kw): return {'Role': {'Arn': f'arn:aws:iam::{account}:role/synthetic'}}
        def head_bucket(self, **kw): return {}
    class Session:
        def client(self, name, **kw): return Client(name)
    return account, Session()


def test_au_prefixed_bedrock_model_is_recognized_as_an_inference_profile():
    calls = []
    account, session = preflight_session(calls)
    manifest = dict(region='us-west-2', target_account=account, model_gateway_id='synthetic-model', tool_gateway_id='synthetic-tool',
                    registry_id='synthetic-registry', runtime_role_arn=f'arn:aws:iam::{account}:role/synthetic',
                    artifact_bucket='synthetic-bucket', bedrock_model_id='au.anthropic.claude-test')
    validate_manifest(manifest)
    readonly(manifest, session)
    assert calls == [('profile', {'inferenceProfileIdentifier': 'au.anthropic.claude-test'})]
