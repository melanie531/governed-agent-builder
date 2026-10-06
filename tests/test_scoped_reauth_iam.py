import copy

import pytest

from scripts.scoped_reauth_iam import reviewed_evaluated_changes, reviewed_role_change


def test_business_role_removes_only_unneeded_gateway_workload_grant():
    gateway = "arn:aws:bedrock-agentcore:us-east-1:123456789012:workload-identity-directory/default/workload-identity/gateway"
    before = {"Resources": {"BusinessRole": {"Properties": {"Policies": [{
        "PolicyName": "McpUserAuthorization", "PolicyDocument": {"Statement": [
            {"Action": ["bedrock-agentcore:GetWorkloadIdentity"], "Resource": ["identity"]},
            {"Action": ["bedrock-agentcore:GetWorkloadAccessTokenForJWT"], "Resource": ["identity"]},
            {"Action": ["bedrock-agentcore:GetResourceOauth2Token"], "Resource": ["vault"]},
            {"Action": ["bedrock-agentcore:CompleteResourceTokenAuth"], "Resource": "*"},
        ]},
    }]}}}}
    after = copy.deepcopy(before)
    for statement in before["Resources"]["BusinessRole"]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"][:3]:
        statement["Resource"].append(gateway)
    assert reviewed_role_change(before, after, gateway)
    after["Resources"]["ExtraBucket"] = {"Type": "AWS::S3::Bucket"}
    with pytest.raises(ValueError, match="more than"):
        reviewed_role_change(before, after, gateway)


def test_change_set_accepts_only_role_and_its_dynamic_references():
    def entry(name, source, cause, field):
        return {"ResourceChange": {
            "LogicalResourceId": name, "Action": "Modify", "Replacement": "False",
            "Details": [{"ChangeSource": source, "CausingEntity": cause,
                         "Target": {"Name": field, "RequiresRecreation": "Never"}}]}}
    change = {"Status": "CREATE_COMPLETE", "Changes": [
        entry("BusinessRole", "DirectModification", None, "Policies"),
        entry("Business", "ResourceAttribute", "BusinessRole.Arn", "Role"),
        entry("BusinessIntegration", "ResourceAttribute", "Business.Arn", "IntegrationUri"),
    ]}
    assert reviewed_evaluated_changes(change) == ["Business", "BusinessIntegration", "BusinessRole"]
    change["Changes"][1]["ResourceChange"]["Details"][0]["CausingEntity"] = "OtherRole.Arn"
    with pytest.raises(ValueError, match="unexpected dependency"):
        reviewed_evaluated_changes(change)
