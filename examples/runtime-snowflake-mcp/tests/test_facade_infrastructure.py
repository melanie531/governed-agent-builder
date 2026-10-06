import json

from infrastructure import templates
from facade_infrastructure import template


def test_gateway_mode_runtime_has_no_identity_permission_and_only_token_header():
    _, runtime = templates(prefix="example-snowflake", account="123456789012", region="us-east-1",
        gateway_role_arn="arn:aws:iam::123456789012:role/existing-gateway", snowflake_account="org-account",
        snowflake_role="READER", warehouse="READ_WH", auth_source="gateway")
    resources = runtime["Resources"]
    properties = next(r["Properties"] for r in resources.values() if r["Type"] == "AWS::BedrockAgentCore::Runtime")
    assert properties["RequestHeaderConfiguration"]["RequestHeaderAllowlist"] == [
        "X-Amzn-Bedrock-AgentCore-Runtime-Custom-Snowflake-Token"]
    assert properties["EnvironmentVariables"]["SNOWFLAKE_AUTH_SOURCE"] == "gateway"
    assert "GetResourceOauth2Token" not in json.dumps(runtime)
    assert "GetWorkloadAccessToken" not in json.dumps(runtime)
    assert "AuthorizerConfiguration" not in properties


def test_facade_has_no_anonymous_api_route_or_function_url_and_scoped_roles():
    value = template(prefix="example-snowflake", account="123456789012", region="us-east-1",
        runtime_arn="arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/example-123",
        snowflake_account="org-account", snowflake_role="READER", warehouse="READ_WH")
    resources = value["Resources"]
    for resource in resources.values():
        kind, props = resource["Type"], resource.get("Properties", {})
        assert kind != "AWS::Lambda::Url"
        if kind == "AWS::ApiGatewayV2::Route":
            assert props["AuthorizationType"] == "CUSTOM"
            assert props["AuthorizerId"]
        if kind == "AWS::ApiGatewayV2::Authorizer":
            assert props["AuthorizerResultTtlInSeconds"] == 0
        if kind == "AWS::IAM::Role":
            for policy in props.get("Policies", []):
                for statement in policy["PolicyDocument"]["Statement"]:
                    assert statement["Resource"] != "*"
        if kind in {"AWS::Lambda::Function", "AWS::IAM::Role", "AWS::Logs::LogGroup",
                    "AWS::SecretsManager::Secret", "AWS::ApiGatewayV2::Api", "AWS::ApiGatewayV2::Stage",
                    "AWS::CloudFront::Distribution"}:
            tags = props["Tags"]
            if isinstance(tags, list):
                tags = {t["Key"]: t["Value"] for t in tags}
            assert tags["auto-delete"] == "no", kind
    text = json.dumps(value)
    assert "resolve:secretsmanager:" in text
    assert "DataTraceEnabled" not in text
    distribution = next(r["Properties"] for r in resources.values() if r["Type"] == "AWS::CloudFront::Distribution")
    behavior = distribution["DistributionConfig"]["DefaultCacheBehavior"]
    assert behavior["ViewerProtocolPolicy"] == "https-only"
    assert behavior["CachePolicyId"] == "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"


def test_python_onboarding_reuses_authenticated_facade_with_dedicated_runtime_role_and_deployment_policy():
    value = template(prefix="studio-python", account="123456789012", region="us-east-1",
        python_onboarding={"table_name": "studio-state", "runtime_prefix": "studio_python",
            "deployment_prefix": "test-studio", "worker_role_name": "studio-worker",
            "artifact": {"bucket": "studio-private-releases", "key": "mcp/python/base.zip", "version_id": "base-v1"}})
    resources = value["Resources"]
    scope = "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/studio_python_*"
    route = next(r["Properties"] for r in resources.values() if r["Type"] == "AWS::ApiGatewayV2::Route")
    assert route["RouteKey"] == "ANY /mcp/{python_id}" and route["AuthorizationType"] == "CUSTOM"
    assert not any(r["Type"] == "AWS::Lambda::Url" for r in resources.values())
    runtime_role = next(r["Properties"] for r in resources.values()
        if r["Type"] == "AWS::IAM::Role" and r["Properties"].get("RoleName") == "studio-python-runtime")
    assert runtime_role["AssumeRolePolicyDocument"]["Statement"][0]["Condition"]["ArnLike"]["aws:SourceArn"] == scope
    text = json.dumps(runtime_role)
    assert "GetResourceOauth2Token" not in text and "GetSecretValue" not in text and "InvokeModel" not in text
    assert "mcp/python/*/runtime.zip" in text
    worker = next(r["Properties"] for r in resources.values() if r["Type"] == "AWS::IAM::ManagedPolicy")
    assert worker["Roles"] == ["studio-worker"]
    statements = worker["PolicyDocument"]["Statement"]
    listing = [s for s in statements if "s3:ListBucket" in s["Action"]]
    assert len(listing) == 1
    assert listing[0]["Resource"] == "arn:aws:s3:::studio-private-releases"
    assert listing[0]["Condition"] == {"StringLike": {"s3:prefix": "mcp/python/*/runtime.zip"}}
    tagging = next(s for s in statements if "s3:PutObjectTagging" in s["Action"])
    assert tagging["Resource"] == "arn:aws:s3:::studio-private-releases/mcp/python/*/runtime.zip"
    assert tagging["Condition"] == {"StringEquals": {"s3:RequestObjectTag/auto-delete": "no"}}
    upload = next(s for s in statements if "s3:PutObject" in s["Action"])
    assert upload["Condition"]["StringEquals"]["s3:x-amz-server-side-encryption"] == "AES256"
    assert upload["Condition"]["Null"]["s3:if-none-match"] == "false"
    creation = next(s for s in statements if "bedrock-agentcore:CreateAgentRuntime" in s["Action"])
    assert creation["Condition"]["StringEquals"]["aws:RequestTag/auto-delete"] == "no"
    assert creation["Condition"]["StringEquals"]["aws:RequestTag/deployment"] == "test-studio"
    assert all(s.get("Condition") for s in statements if s["Resource"] == "*")
    role = next(s for s in statements if "iam:PassRole" in s["Action"])
    assert role["Resource"] != "*" and role["Condition"]["StringEquals"]["iam:PassedToService"] == "bedrock-agentcore.amazonaws.com"
    for r in resources.values():
        if r["Type"] == "AWS::Lambda::Function":
            env = r["Properties"]["Environment"]["Variables"]
            assert env["STATE_TABLE"] == "studio-state"
            assert env["RUNTIME_ARN_PREFIX"] == scope[:-1]
        if r["Type"] == "AWS::IAM::Role":
            for policy in r["Properties"].get("Policies", []):
                for statement in policy["PolicyDocument"]["Statement"]:
                    assert statement["Resource"] != "*"


def test_package_hosting_can_deploy_in_an_account_without_cdk_bootstrap():
    value = template(prefix="fresh-studio", account="123456789012", region="us-west-2",
        python_onboarding={"table_name": "studio-state", "runtime_prefix": "studio_mcp",
            "deployment_prefix": "test-studio", "worker_role_name": "studio-worker",
            "artifact": {"bucket": "studio-private-releases", "key": "mcp/python/base.zip", "version_id": "base-v1"}})
    assert set(value["Parameters"]) == {"ArtifactBucket", "ArtifactKey", "ArtifactVersion"}
    assert "/cdk-bootstrap/" not in json.dumps(value)
    for resource in value["Resources"].values():
        if resource["Type"] == "AWS::Lambda::Function":
            assert resource["Properties"]["Code"] == {
                "S3Bucket": {"Ref": "ArtifactBucket"},
                "S3Key": {"Ref": "ArtifactKey"},
                "S3ObjectVersion": {"Ref": "ArtifactVersion"},
            }
