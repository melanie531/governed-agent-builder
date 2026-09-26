import json

import pytest

from infra.mcp_servers import configure_app, credential_template
from infra.serverless import template


def settings():
    return {"account": "123456789012", "region": "us-west-2", "gateway_id": "test-gateway",
            "mcp_creation": {"provisioning_secret_arns": [
                "arn:aws:secretsmanager:us-west-2:123456789012:secret:governed-agent-builder-serverless/snowflake-mcp-provisioner-Ab1234"],
                "reader_secret_arns": ["arn:aws:secretsmanager:us-west-2:123456789012:secret:governed-agent-builder-serverless/snowflake-pat-Ab1234"],
                "credential_provider_arns": ["arn:aws:bedrock-agentcore:us-west-2:123456789012:token-vault/default/apikeycredentialprovider/snowflake"]}}


def test_only_worker_gets_scoped_provisioning_permissions():
    resources = template()["Resources"]
    configure_app(resources, settings())
    policies = resources["WorkerRole"]["Properties"]["Policies"]
    policy = next(p["PolicyDocument"] for p in policies if p["PolicyName"] == "McpCreation")
    assert all(s["Resource"] != "*" for s in policy["Statement"])
    actions = {a for s in policy["Statement"] for a in s["Action"]}
    assert actions == {"secretsmanager:GetSecretValue", "bedrock-agentcore:GetGateway",
                       "bedrock-agentcore:ListGatewayTargets", "bedrock-agentcore:GetGatewayTarget",
                       "bedrock-agentcore:CreateGatewayTarget", "bedrock-agentcore:InvokeGateway",
                       "bedrock-agentcore:GetWorkloadAccessToken", "bedrock-agentcore:GetResourceApiKey"}
    assert not any("McpCreation" == p["PolicyName"] for p in resources["BusinessRole"]["Properties"]["Policies"])
    assert resources["RoleSwitchers"]["Properties"]["GroupName"] == "studio-role-switcher"
    assert resources["Worker"]["Properties"]["Timeout"] == 240
    assert resources["Jobs"]["Properties"]["VisibilityTimeout"] >= 6 * 240
    statements = policy["Statement"]
    identity = "arn:aws:bedrock-agentcore:us-west-2:123456789012:workload-identity-directory/default/workload-identity/test-gateway"
    assert any(identity in s["Resource"] for s in statements if "bedrock-agentcore:GetWorkloadAccessToken" in s["Action"])


def test_mcp_creation_does_not_reduce_existing_worker_limits():
    resources = template()["Resources"]
    resources["Worker"]["Properties"]["Timeout"] = 300
    resources["Jobs"]["Properties"]["VisibilityTimeout"] = 1800
    configure_app(resources, settings())
    assert resources["Worker"]["Properties"]["Timeout"] == 300
    assert resources["Jobs"]["Properties"]["VisibilityTimeout"] == 1800


def test_provisioning_secret_is_retained_prefixed_and_has_no_value_in_iac():
    body = credential_template()
    secret = body["Resources"]["McpProvisionerPAT"]
    assert secret["DeletionPolicy"] == secret["UpdateReplacePolicy"] == "Retain"
    assert secret["Properties"]["Name"] == "governed-agent-builder-serverless/snowflake-mcp-provisioner"
    assert {"Key": "auto-delete", "Value": "no"} in secret["Properties"]["Tags"]
    assert "SecretString" not in json.dumps(body)


def test_cross_account_secret_is_rejected_before_synthesis():
    value = settings()
    value["mcp_creation"]["provisioning_secret_arns"][0] = value["mcp_creation"]["provisioning_secret_arns"][0].replace("123456789012", "999999999999")
    with pytest.raises(ValueError):
        configure_app(template()["Resources"], value)
