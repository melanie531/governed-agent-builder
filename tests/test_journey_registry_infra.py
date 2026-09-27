import pytest

from infra.serverless import template


SETTINGS = {"enabled": True, "account": "123456789012", "region": "us-west-2",
            "gateway_id": "gab-journey-tools-synthetic", "gateway_url": "https://synthetic/mcp",
            "runtime_role": "arn:aws:iam::123456789012:role/synthetic-runtime",
            "bucket": "synthetic-evidence", "log_group": "/synthetic/traces",
            "network": {"networkMode": "PUBLIC"},
            "evaluator_id": "Builtin.Correctness",
            "evaluator_arn": "arn:aws:bedrock-agentcore:us-west-2:123456789012:evaluator/Builtin.Correctness",
            "foundation": {}, "artifact": {"bucket": "b", "key": "k", "version_id": "v", "sha256": "0" * 64}}


def registry_statements(body):
    for policy in body["Resources"]["BusinessRole"]["Properties"]["Policies"]:
        if policy["PolicyName"] == "PlatformAdministration":
            return policy["PolicyDocument"]["Statement"]
    return []


def test_registry_grants_use_agent_registry_service_and_exact_resource():
    arn = "arn:aws:agent-registry:us-west-2:123456789012:registry/abcdefghijkl"
    body = template(journey={**SETTINGS, "admin_enabled": True, "registry_arn": arn})
    grants = [s for s in registry_statements(body) if "agent-registry:GetRegistry" in s["Action"]]
    assert len(grants) == 1
    assert sorted(grants[0]["Action"]) == ["agent-registry:CreateRegistryRecord", "agent-registry:GetRegistry",
                                           "agent-registry:GetRegistryRecord",
                                           "agent-registry:SubmitRegistryRecordForApproval",
                                           "agent-registry:UpdateRegistryRecordStatus"]
    assert grants[0]["Resource"] == [arn, arn + "/record/*"]
    assert not any("bedrock-agentcore:GetRegistry" in s.get("Action", []) for s in registry_statements(body))


@pytest.mark.parametrize("arn", [
    "arn:aws:bedrock-agentcore:us-west-2:123456789012:registry/abcdefghijkl",
    "arn:aws:agent-registry:us-east-1:123456789012:registry/abcdefghijkl",
    "arn:aws:agent-registry:us-west-2:999999999999:registry/abcdefghijkl",
    "arn:aws:agent-registry:us-west-2:123456789012:registry/abc/record/def"])
def test_registry_binding_outside_platform_target_is_rejected(arn):
    with pytest.raises(ValueError):
        template(journey={**SETTINGS, "admin_enabled": True, "registry_arn": arn})


def test_admin_enabled_without_registry_grants_no_registry_actions():
    body = template(journey={**SETTINGS, "admin_enabled": True})
    assert registry_statements(body)
    assert not any("agent-registry:GetRegistry" in s.get("Action", []) for s in registry_statements(body))
