import copy
from unittest.mock import Mock

from botocore.exceptions import ClientError
import pytest

from deploy import Deployment
from tests.test_oauth_infrastructure import CONFIG


def deployment():
    value = Deployment.__new__(Deployment)
    value.config = {"prefix": "customer-mcp", "account": "123456789012", "region": "us-east-1",
                    "profile": "customer", "gateway_role_arn": "arn:aws:iam::123456789012:role/gateway",
                    "snowflake_account": "org-account", "snowflake_role": "READER", "warehouse": "READ_WH"}
    live = {"bucket": "private-bucket", "key": "releases/old/runtime.zip", "version": "version-one", "digest": "old"}
    value.state = {"config": value.config, "operations": {},
        "runtime": {"outputs": {"RuntimeArn": "runtime-arn"}},
        "upload": {"bucket": "private-bucket", "key": "releases/pending/runtime.zip", "digest": "pending"},
        "pending_release": {"from": "old", "to": "pending"},
        "release_history": [{"upload": live, "runtime_operation": {"phase": "CREATE_COMPLETE", "digest": "old-template"}}]}
    value.generated = Mock(return_value=({
        "status": "READY", "agentRuntimeArn": "runtime-arn", "agentRuntimeVersion": "1",
        "agentRuntimeArtifact": {"codeConfiguration": {"code": {"s3": {
            "bucket": live["bucket"], "prefix": live["key"], "versionId": live["version"]}}}}},
        {"status": "READY", "liveVersion": "1"}))
    client = Mock()
    value.client = lambda name: client
    value.save, value.plan = Mock(), Mock()
    return value, client


def test_oauth_migration_preserves_live_receipt_and_only_supersedes_absent_upload():
    value, client = deployment()
    client.head_object.side_effect = ClientError(
        {"Error": {"Code": "404"}, "ResponseMetadata": {"HTTPStatusCode": 404}}, "HeadObject")
    value.migrate_oauth({**value.config, "oauth": CONFIG})
    assert value.state["upload"]["version"] == "version-one"
    assert value.state["operations"]["runtime"]["phase"] == "CREATE_COMPLETE"
    assert "pending_release" not in value.state
    assert value.state["migration_history"][0]["pending_outcome"] == "ABSENT"
    client.put_object.assert_not_called()
    value.save.assert_called_once()


def test_oauth_migration_refuses_to_forget_a_package_that_exists():
    value, client = deployment()
    before = copy.deepcopy(value.state)
    client.head_object.return_value = {"VersionId": "existing"}
    with pytest.raises(ValueError, match="exists"):
        value.migrate_oauth({**value.config, "oauth": CONFIG})
    assert value.state == before
    value.save.assert_not_called()


def test_gateway_migration_retains_exact_runtime_and_removes_runtime_identity_configuration():
    value, client = deployment()
    value.config = {**value.config, "oauth": CONFIG}
    value.state["config"] = value.config
    value.state["upload"] = value.state["release_history"][0]["upload"]
    value.state.pop("pending_release")
    config = {k: v for k, v in value.config.items() if k != "oauth"}
    config["auth_source"] = "gateway"
    value.migrate_gateway(config)
    assert value.state["config"] == config
    assert value.state["upload"]["version"] == "version-one"
    assert value.state["migration_history"][-1]["to_auth_source"] == "gateway"
    assert not client.mock_calls


def test_gateway_migration_cannot_retarget_an_existing_runtime():
    value, _ = deployment()
    config = {**value.config, "auth_source": "gateway", "account": "999999999999"}
    with pytest.raises(ValueError):
        value.migrate_gateway(config)
    value.save.assert_not_called()
