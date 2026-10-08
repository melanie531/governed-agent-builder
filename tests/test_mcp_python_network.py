import copy

import boto3
import pytest
from botocore.stub import Stubber
from botocore.validate import validate_parameters

from backend.mcp_python_cloud import PythonCloud
from tests.test_mcp_python import CONFIG
from tests.test_mcp_python_cloud import native

VPC = {"networkMode": "VPC", "networkModeConfig": {"subnets": ["subnet-a", "subnet-b"], "securityGroups": ["sg-runtime"]}}


def cloud_with(network):
    settings = {"account": "123456789012", "region": "us-west-2"}
    if network is not None:
        settings["network"] = network
    return PythonCloud(settings, boto3.Session(aws_access_key_id="test", aws_secret_access_key="test", region_name="us-west-2"))


def test_vpc_platform_network_is_passed_into_the_runtime_request():
    _, state, _, _, _ = native()
    cloud = cloud_with(VPC)
    request = cloud.runtime_request(state, CONFIG)
    assert request["networkConfiguration"] == VPC
    validate_parameters(request, cloud.control.meta.service_model.operation_model("CreateAgentRuntime").input_shape)


def test_legacy_settings_without_network_keep_public_mode():
    _, state, _, _, _ = native()
    assert cloud_with(None).runtime_request(state, CONFIG)["networkConfiguration"] == {"networkMode": "PUBLIC"}


@pytest.mark.parametrize("network", [{"networkMode": "VPC"}, {"networkMode": "PRIVATE"},
                                     {"networkMode": "VPC", "networkModeConfig": {"subnets": [], "securityGroups": ["sg-1"]}}])
def test_present_but_invalid_network_raises_instead_of_deploying_public(network):
    _, state, _, _, _ = native()
    with pytest.raises(ValueError, match="network"):
        cloud_with(network).runtime_request(state, CONFIG)


def stub_reconciliation(control, original, runtime, endpoint, version, tags=None):
    control.add_response("list_agent_runtimes", {"agentRuntimes": [{"description": "Studio Python MCP", **{
        k: original[k] for k in ("agentRuntimeArn", "agentRuntimeId", "agentRuntimeName", "agentRuntimeVersion", "lastUpdatedAt", "status")}}]}, {})
    control.add_response("get_agent_runtime", runtime, {"agentRuntimeId": original["agentRuntimeId"], "agentRuntimeVersion": version})
    if tags is not None:
        control.add_response("list_tags_for_resource", {"tags": tags}, {"resourceArn": original["agentRuntimeArn"]})
        control.add_response("get_agent_runtime_endpoint", endpoint, {
            "agentRuntimeId": original["agentRuntimeId"], "endpointName": "DEFAULT"})


def test_live_network_differing_from_configured_network_is_a_binding_change():
    _, state, _, original, endpoint = native()
    cloud = cloud_with(VPC)
    runtime = copy.deepcopy(original)
    runtime["networkConfiguration"] = {"networkMode": "PUBLIC"}
    with Stubber(cloud.control) as control:
        stub_reconciliation(control, original, runtime, endpoint, "1")
        with pytest.raises(ValueError, match="Python Runtime deployment binding changed"):
            cloud.runtime(state, CONFIG)
        control.assert_no_pending_responses()


def pinned(version):
    cloud, state, _, original, endpoint = native()
    state = {**state, "runtime_version": version}
    original = {**copy.deepcopy(original), "agentRuntimeVersion": version}
    endpoint = {**endpoint, "liveVersion": version}
    return cloud, state, original, endpoint


def test_state_pinned_at_version_two_reconciles_against_its_receipt_version():
    cloud, state, original, endpoint = pinned("2")
    with Stubber(cloud.control) as control:
        stub_reconciliation(control, original, original, endpoint, "2", cloud.tags(state, CONFIG))
        assert cloud.runtime(state, CONFIG) == {"runtime_id": original["agentRuntimeId"],
            "runtime_arn": original["agentRuntimeArn"], "runtime_version": "2"}
        control.assert_no_pending_responses()


def test_state_pinned_at_version_two_refuses_a_live_version_one_runtime():
    cloud, state, original, endpoint = pinned("2")
    original["agentRuntimeVersion"] = "1"
    with Stubber(cloud.control) as control:
        control.add_response("list_agent_runtimes", {"agentRuntimes": [{"description": "Studio Python MCP", **{
            k: original[k] for k in ("agentRuntimeArn", "agentRuntimeId", "agentRuntimeName", "agentRuntimeVersion", "lastUpdatedAt", "status")}}]}, {})
        with pytest.raises(ValueError, match="identity or version changed"):
            cloud.runtime(state, CONFIG)


def test_state_pinned_at_version_two_refuses_an_endpoint_serving_another_version():
    cloud, state, original, endpoint = pinned("2")
    endpoint["targetVersion"] = "3"
    with Stubber(cloud.control) as control:
        stub_reconciliation(control, original, original, endpoint, "2", cloud.tags(state, CONFIG))
        with pytest.raises(ValueError, match="endpoint version changed"):
            cloud.runtime(state, CONFIG)
