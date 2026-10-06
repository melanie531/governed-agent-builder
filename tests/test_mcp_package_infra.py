import copy
import json

import pytest

from infra.serverless import template
from scripts.mcp_onboarding_deploy import review_evaluated_changes, review_existing_ui_change
from tests.test_mcp_onboarding_infra import settings


def configured():
    value = settings()
    value["mcp_package_upload"] = {"bucket": "test-private-packages", "runtime_prefix": "studio_python",
        "gateway_role_arn": "arn:aws:iam::123456789012:role/test-gateway"}
    return value


def test_package_permissions_separate_upload_read_and_gateway_invocation():
    from infra.mcp_packages import policies
    value = configured()
    resources = policies(value)
    assert set(resources) == {"McpPackageUploadPolicy", "McpPackageReadPolicy", "McpPackageGatewayPolicy", "McpPackageDeletionPolicy"}
    upload, read, gateway = [resources[k]["Properties"] for k in (
        "McpPackageUploadPolicy", "McpPackageReadPolicy", "McpPackageGatewayPolicy")]
    assert upload["Roles"] == [{"Ref": "BusinessRole"}]
    assert read["Roles"] == [{"Ref": "WorkerRole"}]
    assert gateway["Roles"] == ["test-gateway"]
    assert "s3:Delete" not in json.dumps([upload, read, gateway])
    deletion = resources["McpPackageDeletionPolicy"]["Properties"]
    assert deletion["Roles"] == [{"Ref": "WorkerRole"}]
    assert all(s["Resource"] != "*" for s in deletion["PolicyDocument"]["Statement"])
    object_delete = next(s for s in deletion["PolicyDocument"]["Statement"] if "s3:DeleteObjectVersion" in s["Action"])
    assert object_delete["Resource"] == ["arn:aws:s3:::test-private-packages/mcp/python/uploads/*/part-*",
                                         "arn:aws:s3:::test-private-packages/mcp/python/*/runtime.zip"]
    assert "s3:DeleteObject\"" not in json.dumps(deletion)
    assert "DeleteSecret" not in json.dumps(deletion) and "DeleteRole" not in json.dumps(deletion)
    assert "runtime.zip" not in json.dumps(upload) and "runtime.zip" not in json.dumps(read)
    put = next(s for s in upload["PolicyDocument"]["Statement"] if "s3:PutObject" in s["Action"])
    assert put["Resource"] == "arn:aws:s3:::test-private-packages/mcp/python/uploads/*/part-*"
    assert put["Condition"]["Null"]["s3:if-none-match"] == "false"
    assert put["Condition"]["StringEquals"]["s3:RequestObjectTag/auto-delete"] == "no"
    assert gateway["PolicyDocument"]["Statement"][0]["Resource"] == [
        "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/studio_python_*",
        "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/studio_python_*/runtime-endpoint/DEFAULT"]
    assert all(len(json.dumps(r["Properties"]["PolicyDocument"], separators=(",", ":"))) < 6144 for r in resources.values())


def test_runtime_retirement_includes_the_native_default_endpoint_dependency():
    from infra.mcp_packages import policies
    statements = policies(configured())["McpPackageDeletionPolicy"]["Properties"]["PolicyDocument"]["Statement"]
    runtime_delete = next(s for s in statements if "bedrock-agentcore:DeleteAgentRuntime" in s["Action"])
    # AgentCore checks both actions on the Runtime when deleting its DEFAULT
    # endpoint, even though the application sends one DeleteAgentRuntime call.
    assert set(runtime_delete["Action"]) == {
        "bedrock-agentcore:DeleteAgentRuntime", "bedrock-agentcore:DeleteAgentRuntimeEndpoint"}
    assert runtime_delete["Resource"] == [
        "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/studio_python_*",
        "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/studio_python_*/runtime-endpoint/DEFAULT"]
    assert runtime_delete["Condition"]["StringEquals"] == {
        "aws:ResourceTag/auto-delete": "no", "aws:ResourceTag/project": "governed-agent-builder"}


@pytest.mark.parametrize("patch", [
    {"bucket": "*"}, {"runtime_prefix": "prefix*"},
    {"gateway_role_arn": "arn:aws:iam::999999999999:role/test-gateway"},
    {"extra_permissions": "*"},
])
def test_package_policy_bindings_reject_wildcards_foreign_roles_and_extra_keys(patch):
    from infra.mcp_packages import policies
    value = configured()
    value["mcp_package_upload"].update(patch)
    with pytest.raises(ValueError):
        policies(value)


def test_release_admits_only_exact_generated_package_policies():
    from infra.mcp_onboarding import configure_app
    value = configured()
    previous = template()
    proposed = copy.deepcopy(previous)
    configure_app(proposed["Resources"], value)
    # Existing onboarding policies are already deployed; isolate the new delta.
    previous = copy.deepcopy(proposed)
    for key in ("McpPackageUploadPolicy", "McpPackageReadPolicy", "McpPackageGatewayPolicy", "McpPackageDeletionPolicy"):
        del previous["Resources"][key]
    review_existing_ui_change(previous, proposed, package_settings=value)
    tampered = copy.deepcopy(proposed)
    tampered["Resources"]["McpPackageGatewayPolicy"]["Properties"]["PolicyDocument"]["Statement"][0]["Resource"] = "*"
    with pytest.raises(ValueError):
        review_existing_ui_change(previous, tampered, package_settings=value)
    with pytest.raises(ValueError):
        review_existing_ui_change(previous, proposed)
    assert review_evaluated_changes([{"ResourceChange": {
        "LogicalResourceId": "McpPackageReadPolicy", "ResourceType": "AWS::IAM::ManagedPolicy", "Action": "Add"}}], {})
