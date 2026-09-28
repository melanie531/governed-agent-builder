import copy
import json
from unittest.mock import Mock

import httpx
import pytest

from backend.mcp_cloud import McpCloud
from backend.mcp_servers import specification, validate_profile
from tests.test_mcp_servers import PROFILE

SETTINGS = {"account": "123456789012", "region": "us-west-2", "gateway_id": "test-gateway",
            "gateway_url": "https://test-gateway.gateway.bedrock-agentcore.us-west-2.amazonaws.com/mcp"}
STATE = {"id": "a" * 32, "server_name": "STUDIO_" + "A" * 20, "target_name": "studio-mcp-aaaaaaaaaaaa",
         "tool_ids": ["query_sql"], "endpoint": "https://org-account.snowflakecomputing.com/api/v2/databases/DEMO/schemas/PUBLIC/mcp-servers/STUDIO_" + "A" * 20}


def test_native_sql_api_uses_pat_and_only_server_creation():
    sent = []
    secret = Mock()
    secret.get_secret_value.return_value = {"SecretString": json.dumps({"pat": "synthetic-test-value"})}
    def transport(request):
        sent.append(request)
        return httpx.Response(200, json={"resultSetMetaData": {"rowType": [{"name": "status"}]}, "data": [["created"]]})
    cloud = McpCloud(SETTINGS, secrets=secret, control=Mock(), transport=httpx.MockTransport(transport))
    cloud.write("create", STATE, PROFILE)
    assert len(sent) == 1
    request = sent[0]
    assert str(request.url).startswith("https://org-account.snowflakecomputing.com/api/v2/statements?")
    assert request.headers["Authorization"] == "Bearer synthetic-test-value"
    assert request.headers["X-Snowflake-Authorization-Token-Type"] == "PROGRAMMATIC_ACCESS_TOKEN"
    body = json.loads(request.content)
    assert body["role"] == "MCP_CREATOR"
    assert body["statement"].startswith("CREATE MCP SERVER DEMO.PUBLIC.STUDIO_")
    assert "OR REPLACE" not in body["statement"] and "CREATE TABLE" not in body["statement"]
    assert json.dumps(specification(STATE, PROFILE), separators=(",", ":")) in body["statement"]
    assert "synthetic-test-value" not in body["statement"]


def test_sql_error_does_not_expose_response_or_pat():
    secret = Mock()
    secret.get_secret_value.return_value = {"SecretString": '{"pat":"synthetic-test-value"}'}
    cloud = McpCloud(SETTINGS, secrets=secret, control=Mock(),
                     transport=httpx.MockTransport(lambda request: httpx.Response(401, json={"message": "private-response"})))
    with pytest.raises(RuntimeError) as error:
        cloud.write("create", STATE, PROFILE)
    assert "private-response" not in str(error.value)
    assert "synthetic-test-value" not in str(error.value)


def test_gateway_target_uses_existing_provider_and_preserves_other_targets():
    control = Mock()
    control.get_gateway.return_value = {"gatewayId": SETTINGS["gateway_id"], "gatewayArn": "arn:aws:bedrock-agentcore:us-west-2:123456789012:gateway/test-gateway",
                                       "gatewayUrl": SETTINGS["gateway_url"], "authorizerType": "AWS_IAM", "status": "READY"}
    control.list_gateway_targets.return_value = {"items": [{"targetId": "old", "name": "snowflake"}]}
    cloud = McpCloud(SETTINGS, secrets=Mock(), control=control)
    assert cloud.read("connect", STATE, PROFILE) is None
    cloud.write("connect", STATE, PROFILE)
    args = control.create_gateway_target.call_args.kwargs
    assert args["gatewayIdentifier"] == SETTINGS["gateway_id"]
    assert args["name"] == STATE["target_name"]
    assert args["targetConfiguration"]["mcp"]["mcpServer"]["endpoint"] == STATE["endpoint"]
    credential = args["credentialProviderConfigurations"][0]["credentialProvider"]["apiKeyCredentialProvider"]
    assert credential["providerArn"] == PROFILE["credential_provider_arn"]
    assert credential["credentialParameterName"] == "Authorization" and credential["credentialPrefix"] == "Bearer"
    assert not control.delete_gateway_target.called and not control.update_gateway_target.called


@pytest.mark.parametrize("patch", [
    {"host": "attacker.example"}, {"database": 'DEMO"; DROP DATABASE X;--'},
    {"provisioning_secret_arn": PROFILE["provisioning_secret_arn"].replace("123456789012", "999999999999")},
    {"credential_provider_arn": PROFILE["credential_provider_arn"].replace("us-west-2", "us-east-1")},
    {"tools": [{**PROFILE["tools"][0], "config": {"read_only": False, "warehouse": "DEMO_WH", "query_timeout": 30}}]},
])
def test_operator_profile_still_requires_bound_resources(patch):
    with pytest.raises(ValueError):
        validate_profile({**copy.deepcopy(PROFILE), **patch}, SETTINGS)
