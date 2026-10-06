import json
import secrets
from unittest.mock import Mock

import pytest

from oauth_setup import OAuthDeployment


def test_lost_secret_acknowledgement_reconciles_version_without_repeating_write():
    deployment = OAuthDeployment.__new__(OAuthDeployment)
    deployment.state = {"operations": {"oauth": {"phase": "CREATE_COMPLETE", "digest": "previous"}},
                        "oauth": {"outputs": {"ClientSecretArn": "arn:aws:secretsmanager:us-east-1:123456789012:secret:test/mcp/oauth-AbCd12"}}}
    deployment.save = Mock()
    deployment.template = Mock(return_value={"Resources": {}})
    deployment.stack = Mock()
    deployment.audit = Mock()
    native = Mock()
    native.put_secret_value.side_effect = TimeoutError()
    deployment.client = lambda name: native
    value = secrets.token_urlsafe(48)
    with pytest.raises(TimeoutError):
        deployment.configure("snowflake-client", value)
    version = deployment.state["configuration"]["version_id"]
    assert value not in json.dumps(deployment.state)
    native.describe_secret.return_value = {"VersionIdsToStages": {version: ["AWSCURRENT"]}}
    deployment.configure("snowflake-client", value)
    native.put_secret_value.assert_called_once()
    deployment.stack.assert_called_once()
    assert deployment.state["configuration"]["phase"] == "CONFIGURED"
