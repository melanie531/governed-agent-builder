"""Model-policy identifier rules shared by the platform and the packaged Runtime."""

PREFIXES = {"global": "global.", "au": "au."}


def policy_prefix(policy):
    if policy not in PREFIXES:
        raise ValueError("The platform model policy is not supported")
    return PREFIXES[policy]


def model_id_matches_policy(model_id, policy):
    """Cheap identifier check only; full eligibility needs the profile detail."""
    return (isinstance(model_id, str) and model_id.startswith(policy_prefix(policy))
            and not (policy == "au" and ".nova" in model_id))
