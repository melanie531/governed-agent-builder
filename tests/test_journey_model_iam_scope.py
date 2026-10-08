from infra.journey import template


def invoke_resources():
    policy = template()["Resources"]["RuntimeRole"]["Properties"]["Policies"][0]["PolicyDocument"]
    statements = [s for s in policy["Statement"] if s["Action"] == ["bedrock:InvokeModel"]]
    assert len(statements) == 1
    return [r["Fn::Sub"] for r in statements[0]["Resource"]]


def test_runtime_model_access_stays_region_wildcarded_for_au_profiles_and_destinations():
    resources = invoke_resources()
    # au.* system-defined profiles route to ap-southeast-2/ap-southeast-4 foundation models.
    assert "arn:${AWS::Partition}:bedrock:*::foundation-model/*" in resources
    assert "arn:${AWS::Partition}:bedrock:*:${AWS::AccountId}:inference-profile/*" in resources


def test_runtime_model_access_is_not_broadened_beyond_bedrock_model_resources():
    assert all(r.startswith("arn:${AWS::Partition}:bedrock:*:") and r != "*" for r in invoke_resources())
