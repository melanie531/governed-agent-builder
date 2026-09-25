"""journey_platform registers only a same-account/region specialist Runtime as a Gateway target."""
from types import SimpleNamespace

import pytest

from backend.foundation_approval import runtime_mcp_endpoint
from scripts.journey_platform import specialist

ACCOUNT = "111122223333"


def target():
    saved = {}
    return SimpleNamespace(binding={"account": ACCOUNT, "region": "us-west-2"}, state=saved,
                           save=lambda key, value: saved.__setitem__(key, value))


def test_specialist_runtime_becomes_this_accounts_runtime_mcp_endpoint():
    platform = target()
    curated = specialist(platform, f"arn:aws:bedrock-agentcore:us-west-2:{ACCOUNT}:runtime/governed_mcp_specialist-SYNTH00001")
    assert runtime_mcp_endpoint(curated["endpoint"], ACCOUNT)
    assert curated["endpoint"].endswith("/invocations?qualifier=DEFAULT")
    assert platform.state["journeySpecialist"] == curated


@pytest.mark.parametrize("arn", [
    "arn:aws:bedrock-agentcore:us-west-2:444455556666:runtime/governed_mcp_specialist-SYNTH00001",
    f"arn:aws:bedrock-agentcore:us-east-1:{ACCOUNT}:runtime/governed_mcp_specialist-SYNTH00001",
    f"arn:aws:bedrock-agentcore:us-west-2:{ACCOUNT}:gateway/gab-journey-tools-synth",
])
def test_foreign_or_non_runtime_specialist_is_rejected(arn):
    platform = target()
    with pytest.raises(RuntimeError):
        specialist(platform, arn)
    assert "journeySpecialist" not in platform.state


def test_no_specialist_argument_keeps_previous_registration():
    platform = target()
    assert specialist(platform, None) is None
