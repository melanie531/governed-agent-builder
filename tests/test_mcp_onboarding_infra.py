import json

import pytest

from infra.mcp_onboarding import configure_app
from infra.serverless import template
from tests.test_mcp_onboarding import CONFIG


def settings():
    return {"account": "123456789012", "region": "us-west-2", "gateway_id": "preview-gateway",
            "mcp_onboarding": {**CONFIG, "secret_arns": [
                "arn:aws:secretsmanager:us-west-2:123456789012:secret:governed-agent-builder-serverless/data-Ab1234"]}}


def test_onboarding_uses_a_bounded_managed_policy_without_consuming_worker_inline_budget():
    resources, value = template()["Resources"], settings()
    value["mcp_onboarding"]["credential_prefix"] = "customer-agent-studio-serverless"
    before = json.dumps(resources["WorkerRole"]["Properties"]["Policies"], sort_keys=True)
    configure_app(resources, value)
    assert json.dumps(resources["WorkerRole"]["Properties"]["Policies"], sort_keys=True) == before
    policy = resources["McpOnboardingPolicy"]
    assert policy["Type"] == "AWS::IAM::ManagedPolicy"
    assert policy["Properties"]["Roles"] == [{"Ref": "WorkerRole"}]
    assert len(json.dumps(policy["Properties"]["PolicyDocument"], separators=(",", ":"))) < 6144


def test_only_worker_receives_scoped_native_onboarding_permissions():
    resources = template()["Resources"]
    configure_app(resources, settings())
    policy = resources["McpOnboardingPolicy"]["Properties"]["PolicyDocument"]
    assert all(s["Resource"] != "*" for s in policy["Statement"])
    assert "agent-registry:CreateRegistryRecord" in json.dumps(policy)
    assert "bedrock-agentcore:CreateGatewayTarget" in json.dumps(policy)
    assert "McpOnboarding" not in json.dumps(resources["BusinessRole"])
    assert resources["Jobs"]["Properties"]["VisibilityTimeout"] >= 6 * resources["Worker"]["Properties"]["Timeout"]


def test_registry_create_can_apply_required_tags_only_within_bound_registry():
    resources = template()["Resources"]
    configure_app(resources, settings())
    policy = resources["McpOnboardingPolicy"]["Properties"]["PolicyDocument"]
    statement = next(s for s in policy["Statement"] if "agent-registry:TagResource" in s["Action"])
    assert statement["Resource"] == [CONFIG["registry_arn"] + "/record/*"]
    assert statement["Condition"]["StringEquals"] == {
        "aws:RequestTag/auto-delete": "no", "aws:RequestTag/project": "governed-agent-builder"}


def test_platform_model_administration_uses_only_the_bound_native_registry():
    from tests.test_deployment_tags import SETTINGS
    value = {**SETTINGS, **settings(), "admin_enabled": True}
    resources = template(journey=value)["Resources"]
    policies = resources["BusinessRole"]["Properties"]["Policies"]
    statements = next(p["PolicyDocument"]["Statement"] for p in policies
                      if p["PolicyName"] == "PlatformAdministration")
    registry = [s for s in statements if any(a.startswith("agent-registry:") for a in s["Action"])]
    assert registry
    assert {a for s in registry for a in s["Action"]} == {
        "agent-registry:GetRegistry", "agent-registry:GetRegistryRecord",
        "agent-registry:CreateRegistryRecord", "agent-registry:SubmitRegistryRecordForApproval",
        "agent-registry:UpdateRegistryRecordStatus", "agent-registry:TagResource"}
    allowed = {CONFIG["registry_arn"], CONFIG["registry_arn"] + "/record/*"}
    for statement in registry:
        assert set(statement["Resource"]) <= allowed
        if any(a in statement["Action"] for a in (
                "agent-registry:CreateRegistryRecord", "agent-registry:TagResource")):
            assert statement["Condition"]["StringEquals"] == {
                "aws:RequestTag/auto-delete": "no", "aws:RequestTag/project": "governed-agent-builder"}
    assert "bedrock-agentcore:CreateGatewayTarget" not in json.dumps(policies)
    assert len(json.dumps([p["PolicyDocument"] for p in policies], separators=(",", ":"))) < 10240


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
    policy = resources["McpOnboardingPolicy"]["Properties"]
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
        "arn:aws:bedrock-agentcore:us-west-2:123456789012:token-vault/default/apikeycredentialprovider/*",
        "arn:aws:bedrock-agentcore:us-west-2:123456789012:token-vault/default/oauth2credentialprovider/*"]
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
    assert "bedrock-agentcore:CreateOauth2CredentialProvider" in provider_creation["Action"]
    assert "bedrock-agentcore:DeleteOauth2CredentialProvider" in lookup["Action"]
    assert "test-studio-mcp-oauth-*" in json.dumps(lookup["Resource"])
    statements = next(p["PolicyDocument"]["Statement"] for p in gateway["GatewayRole"]["Properties"]["Policies"]
                      if p["PolicyName"] == "McpCredentialUse")
    oauth = next(s for s in statements if "bedrock-agentcore:GetResourceOauth2Token" in s["Action"])
    assert all(r != "*" for r in oauth["Resource"])
    assert any(r.endswith("/oauth2credentialprovider/test-studio-mcp-oauth-*") for r in oauth["Resource"])


def test_user_oauth_tokens_are_limited_to_the_business_backend_and_prefixed_identity():
    resources, value = template()["Resources"], settings()
    value["mcp_onboarding"]["credential_prefix"] = "test-studio"
    configure_app(resources, value)
    policy = next(p["PolicyDocument"] for p in resources["BusinessRole"]["Properties"]["Policies"]
                  if p["PolicyName"] == "McpUserAuthorization")
    token = next(s for s in policy["Statement"] if "bedrock-agentcore:GetResourceOauth2Token" in s["Action"])
    assert all(r != "*" for r in token["Resource"])
    assert any(r.endswith("/oauth2credentialprovider/test-studio-mcp-oauth-*") for r in token["Resource"])
    assert any(r.endswith("/workload-identity/test-studio-mcp-users-*") for r in token["Resource"])
    deny = next(s for s in policy["Statement"] if s["Effect"] == "Deny")
    assert deny["Action"] == ["bedrock-agentcore:GetWorkloadAccessTokenForUserId"]
    worker = json.dumps(resources["McpOnboardingPolicy"])
    assert "bedrock-agentcore:GetWorkloadIdentity" in worker
    assert "CompleteResourceTokenAuth" not in worker


def test_gateway_native_3lo_does_not_grant_studio_access_to_service_linked_identity():
    from tests.test_mcp_gateway_oauth import oauth_config, GATEWAY

    resources, value = template()["Resources"], settings()
    value["mcp_onboarding"] = oauth_config()
    configure_app(resources, value)
    policy = next(p["PolicyDocument"] for p in resources["BusinessRole"]["Properties"]["Policies"]
                  if p["PolicyName"] == "McpUserAuthorization")
    workload = ("arn:aws:bedrock-agentcore:us-west-2:123456789012:"
                "workload-identity-directory/default/workload-identity/" + GATEWAY["gateway_id"])
    for action in ("bedrock-agentcore:GetWorkloadIdentity",
                   "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
                   "bedrock-agentcore:GetResourceOauth2Token"):
        assert not any(action in statement["Action"] and workload in statement["Resource"]
                       for statement in policy["Statement"])
    assert all("workload-identity/*" not in json.dumps(statement["Resource"])
               for statement in policy["Statement"])


def test_user_oauth_metadata_is_bounded_inside_the_managed_policy():
    resources, value = template()["Resources"], settings()
    value["mcp_onboarding"]["credential_prefix"] = "customer-agent-studio-serverless"
    configure_app(resources, value)
    statements = resources["McpOnboardingPolicy"]["Properties"]["PolicyDocument"]["Statement"]
    policy = {"Statement": [s for s in statements if any(a in (
        "bedrock-agentcore:GetAgentRuntime", "bedrock-agentcore:GetWorkloadIdentity") for a in s["Action"])]}
    assert len(json.dumps(policy, separators=(",", ":"))) <= 900
