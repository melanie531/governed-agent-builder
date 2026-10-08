import pytest

from backend.model_policy import (AU_MODEL_REGIONS, model_id_matches_policy, model_policy, policy_description,
                                  policy_prefix, profile_eligible_for_policy)

AU_ID = "au.anthropic.claude-sonnet-4-5-20250929-v1:0"
GLOBAL_ID = "global.anthropic.claude-sonnet-4-5-20250929-v1:0"


def arn(region, model="anthropic.claude-sonnet-4-5-20250929-v1:0"):
    return f"arn:aws:bedrock:{region}::foundation-model/{model}"


def profile(profile_id=AU_ID, regions=AU_MODEL_REGIONS, **overrides):
    value = {"inferenceProfileId": profile_id, "status": "ACTIVE", "type": "SYSTEM_DEFINED",
             "models": [{"modelArn": arn(region)} for region in regions]}
    return {**value, **overrides}


def test_au_regions_are_the_australian_destinations():
    assert AU_MODEL_REGIONS == ("ap-southeast-2", "ap-southeast-4")


def test_missing_policy_defaults_to_global_and_unknown_values_raise():
    assert model_policy({}) == "global"
    assert model_policy({"model_policy": "au"}) == "au"
    for value in ("AU", "us", None, ""):
        with pytest.raises(ValueError, match="model policy"):
            model_policy({"model_policy": value})


def test_prefixes_and_descriptions_follow_the_policy():
    assert policy_prefix("global") == "global." and policy_prefix("au") == "au."
    assert "(au.*)" in policy_description("au") and "Australian destination models" in policy_description("au")
    assert policy_description("global") == "global cross-region inference profile (global.*)"
    with pytest.raises(ValueError):
        policy_prefix("us")


def test_au_policy_accepts_an_active_system_defined_profile_with_australian_destinations():
    assert profile_eligible_for_policy(profile(), AU_ID, "au")


@pytest.mark.parametrize("detail,model_id", [
    (profile(GLOBAL_ID, ("us-east-1", "eu-west-1")), GLOBAL_ID),
    (profile(regions=("ap-southeast-2", "us-east-1")), AU_ID),
    (profile(regions=()), AU_ID),
    (profile(status="LEGACY"), AU_ID),
    (profile(type="APPLICATION"), AU_ID),
    (profile("au.anthropic.other"), AU_ID),
    (profile("au.amazon.nova-pro-v1:0"), "au.amazon.nova-pro-v1:0"),
    ({**profile(), "models": [{"modelArn": arn("ap-southeast-2", "amazon.nova-lite-v1:0")}]}, AU_ID),
    ({**profile(), "models": [{"modelArn": "not-an-arn"}]}, AU_ID),
])
def test_au_policy_rejects_ineligible_profiles(detail, model_id):
    assert not profile_eligible_for_policy(detail, model_id, "au")


def test_global_policy_keeps_its_current_semantics():
    detail = profile(GLOBAL_ID, ("us-east-1", "eu-west-1"))
    assert profile_eligible_for_policy(detail, GLOBAL_ID, "global")
    assert profile_eligible_for_policy({**detail, "models": []}, GLOBAL_ID, "global")
    assert profile_eligible_for_policy(profile("global.amazon.nova-pro-v1:0"), "global.amazon.nova-pro-v1:0", "global")
    assert not profile_eligible_for_policy(profile(), AU_ID, "global")
    assert not profile_eligible_for_policy({**detail, "status": "LEGACY"}, GLOBAL_ID, "global")
    assert not profile_eligible_for_policy({**detail, "type": "APPLICATION"}, GLOBAL_ID, "global")
    assert not profile_eligible_for_policy(detail, GLOBAL_ID + "-other", "global")


def test_cheap_model_id_check_never_grants_eligibility_across_policies():
    assert model_id_matches_policy(GLOBAL_ID, "global") and not model_id_matches_policy(GLOBAL_ID, "au")
    assert model_id_matches_policy(AU_ID, "au") and not model_id_matches_policy(AU_ID, "global")
    assert not model_id_matches_policy("au.amazon.nova-pro-v1:0", "au")
    assert not model_id_matches_policy(None, "au")
