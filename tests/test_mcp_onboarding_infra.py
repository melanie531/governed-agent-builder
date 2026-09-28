import json

import pytest

from infra.mcp_onboarding import configure_app
from infra.serverless import template
from tests.test_mcp_onboarding import CONFIG


def settings():
    return {"account": "123456789012", "region": "us-west-2", "gateway_id": "preview-gateway",
            "mcp_onboarding": {**CONFIG, "secret_arns": [
                "arn:aws:secretsmanager:us-west-2:123456789012:secret:governed-agent-builder-serverless/data-Ab1234"]}}


def test_only_worker_receives_scoped_native_onboarding_permissions():
    resources = template()["Resources"]
    configure_app(resources, settings())
    policy = next(p["PolicyDocument"] for p in resources["WorkerRole"]["Properties"]["Policies"] if p["PolicyName"] == "McpOnboarding")
    assert all(s["Resource"] != "*" for s in policy["Statement"])
    assert "agent-registry:CreateRegistryRecord" in json.dumps(policy)
    assert "bedrock-agentcore:CreateGatewayTarget" in json.dumps(policy)
    assert "McpOnboarding" not in json.dumps(resources["BusinessRole"])
    assert resources["Jobs"]["Properties"]["VisibilityTimeout"] >= 6 * resources["Worker"]["Properties"]["Timeout"]


def test_registry_create_can_apply_required_tags_only_within_bound_registry():
    resources = template()["Resources"]
    configure_app(resources, settings())
    policy = next(p["PolicyDocument"] for p in resources["WorkerRole"]["Properties"]["Policies"] if p["PolicyName"] == "McpOnboarding")
    statement = next(s for s in policy["Statement"] if "agent-registry:TagResource" in s["Action"])
    assert statement["Resource"] == [CONFIG["registry_arn"] + "/record/*"]
    assert statement["Condition"]["StringEquals"] == {
        "aws:RequestTag/auto-delete": "no", "aws:RequestTag/project": "governed-agent-builder"}


def test_existing_gateway_permissions_are_reused_without_duplicate_inline_policies():
    resources = template()["Resources"]
    value = settings()
    gateway = f"arn:aws:bedrock-agentcore:{value['region']}:{value['account']}:gateway/{value['gateway_id']}"
    resources["WorkerRole"]["Properties"]["Policies"].append({
        "PolicyName": "ExistingGatewayAccess", "PolicyDocument": {"Version": "2012-10-17", "Statement": [
            {"Effect": "Allow", "Action": ["bedrock-agentcore:InvokeGateway"], "Resource": gateway},
            {"Effect": "Allow", "Action": ["bedrock-agentcore:GetGateway"], "Resource": [gateway, gateway + "/target/*"]},
        ]}})
    configure_app(resources, value)
    policy = next(p for p in resources["WorkerRole"]["Properties"]["Policies"] if p["PolicyName"] == "McpOnboarding")
    actions = [a for s in policy["PolicyDocument"]["Statement"] for a in s["Action"]]
    assert "bedrock-agentcore:InvokeGateway" not in actions and "bedrock-agentcore:GetGateway" not in actions
    assert "bedrock-agentcore:CreateGatewayTarget" in actions and "agent-registry:CreateRegistryRecord" in actions


def test_foreign_registry_or_secret_rejected_before_deployment():
    for key in ("registry_arn", "secret_arns"):
        value = settings()
        if key == "registry_arn":
            value["mcp_onboarding"][key] = CONFIG[key].replace("123456789012", "999999999999")
        else:
            value["mcp_onboarding"][key] = [value["mcp_onboarding"][key][0].replace("123456789012", "999999999999")]
        with pytest.raises(ValueError):
            configure_app(template()["Resources"], value)


def test_self_service_secrets_and_providers_are_scoped_and_require_retention_tags():
    resources, value = template()["Resources"], settings()
    value["mcp_onboarding"]["credential_prefix"] = "test-studio"
    configure_app(resources, value)
    policy = next(p["PolicyDocument"] for p in resources["BusinessRole"]["Properties"]["Policies"]
                  if p["PolicyName"] == "McpCredentialSetup")
    assert all(s["Resource"] != "*" for s in policy["Statement"])
    creation = [s for s in policy["Statement"] if any(a in (
        "secretsmanager:CreateSecret", "bedrock-agentcore:CreateApiKeyCredentialProvider") for a in s["Action"])]
    assert len(creation) == 2
    assert all(s["Condition"]["StringEquals"]["aws:RequestTag/auto-delete"] == "no" for s in creation)
    secret_read = next(s for s in policy["Statement"] if "secretsmanager:GetSecretValue" in s["Action"])
    assert secret_read["Resource"] == "arn:aws:secretsmanager:us-west-2:123456789012:secret:test-studio/mcp/*"
    assert "test-studio/mcp/*" in json.dumps(policy)
    lookup = next(s for s in policy["Statement"] if "bedrock-agentcore:GetApiKeyCredentialProvider" in s["Action"])
    lookup_resources = lookup["Resource"] if isinstance(lookup["Resource"], list) else [lookup["Resource"]]
    assert "arn:aws:bedrock-agentcore:us-west-2:123456789012:token-vault/default" in lookup_resources
    tagging = next(s for s in policy["Statement"] if "bedrock-agentcore:TagResource" in s["Action"])
    assert tagging["Resource"] == [
        "arn:aws:bedrock-agentcore:us-west-2:123456789012:token-vault/default",
        "arn:aws:bedrock-agentcore:us-west-2:123456789012:token-vault/default/apikeycredentialprovider/*"]
    assert "Condition" not in tagging  # Native dependent tagging has no request-tag context.
    provider_creation = next(s for s in creation if "bedrock-agentcore:CreateApiKeyCredentialProvider" in s["Action"])
    assert provider_creation["Resource"] == tagging["Resource"]
    vault = next(s for s in policy["Statement"] if "bedrock-agentcore:CreateTokenVault" in s["Action"])
    assert vault["Resource"] == tagging["Resource"][0]
    assert "bedrock-agentcore:GetTokenVault" in vault["Action"]
    tag_lookup = next(s for s in policy["Statement"] if "bedrock-agentcore:ListTagsForResource" in s["Action"])
    assert tag_lookup["Resource"] == tagging["Resource"]
    from infra.mcp_onboarding import configure_gateway
    from infra.journey import template as journey_template
    gateway = journey_template("provider", "secret")["Resources"]
    configure_gateway(gateway, value)
    assert "test-studio-mcp-*" in json.dumps(gateway["GatewayRole"])
    assert "secretsmanager:CreateSecret" not in json.dumps(gateway["GatewayRole"])
