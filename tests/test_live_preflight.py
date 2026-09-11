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
