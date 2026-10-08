"""Platform model policy: global.* (default, legacy) or Australia-only au.* cross-region inference."""
from foundation_harness.model_policy import PREFIXES, model_id_matches_policy, policy_prefix

AU_MODEL_REGIONS = ("ap-southeast-2", "ap-southeast-4")
DESCRIPTIONS = {"global": "global cross-region inference profile (global.*)",
                "au": "Australia cross-region inference profile (au.*) with Australian destination models"}


def model_policy(settings):
    policy = settings.get("model_policy", "global")
    if policy not in PREFIXES:
        raise ValueError("The platform model policy is not supported")
    return policy


def policy_description(policy):
    policy_prefix(policy)
    return DESCRIPTIONS[policy]


def profile_eligible_for_policy(profile_detail, model_id, policy):
    if (not model_id_matches_policy(model_id, policy) or profile_detail.get("status") != "ACTIVE"
            or profile_detail.get("type") != "SYSTEM_DEFINED" or profile_detail.get("inferenceProfileId") != model_id):
        return False
    if policy == "global":
        return True
    models = profile_detail.get("models") or []
    for model in models:
        parts = model.get("modelArn", "").split(":", 5)
        if (len(parts) != 6 or parts[3] not in AU_MODEL_REGIONS
                or "nova" in parts[5].removeprefix("foundation-model/").lower()):
            return False
    return bool(models)
