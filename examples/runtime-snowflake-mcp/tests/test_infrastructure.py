import json

from infrastructure import templates
from tests.test_oauth_infrastructure import CONFIG
from snowflake_mcp.identity import USER_TOKEN_HEADER


def test_runtime_is_separate_private_iam_and_scoped_to_one_gateway():
    artifacts, runtime = templates(
        prefix="customer-snowflake", account="123456789012", region="us-east-1",
        gateway_role_arn="arn:aws:iam::123456789012:role/customer-gateway",
        snowflake_account="org-account", snowflake_role="MCP_READER", warehouse="READ_WH", oauth=CONFIG)
    bucket = next(r for r in artifacts["Resources"].values() if r["Type"] == "AWS::S3::Bucket")
    assert all(bucket["Properties"]["PublicAccessBlockConfiguration"].values())
    assert bucket["Properties"]["BucketEncryption"]
    resources = runtime["Resources"]
    server = next(r for r in resources.values() if r["Type"] == "AWS::BedrockAgentCore::Runtime")
    assert server["Properties"]["ProtocolConfiguration"] == "MCP"
    assert "AuthorizerConfiguration" not in server["Properties"]
    assert server["Properties"]["Tags"]["auto-delete"] == "no"
    assert server["Properties"]["AgentRuntimeArtifact"]["CodeConfiguration"]["Code"]["S3"]["VersionId"] == {"Ref": "ArtifactVersion"}
    policy = next(r for r in resources.values() if r["Type"] == "AWS::IAM::Policy" and "Roles" in r["Properties"])
    assert policy["Properties"]["Roles"] == ["customer-gateway"]
    statement = policy["Properties"]["PolicyDocument"]["Statement"][0]
    assert statement["Action"] == "bedrock-agentcore:InvokeAgentRuntime"
    assert statement["Resource"] != "*"
    text = json.dumps(runtime)
    assert server["Properties"]["RequestHeaderConfiguration"]["RequestHeaderAllowlist"] == [USER_TOKEN_HEADER]
    assert server["Properties"]["EnvironmentVariables"]["OAUTH_WORKLOAD_NAME"] == "customer-studio-mcp-users-snowflake"
    assert "PAT" not in text and "Password" not in text
    assert "AWS::Lambda::Url" not in text
