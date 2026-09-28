import copy
import json
from pathlib import Path
from types import SimpleNamespace

import boto3
from botocore.validate import validate_parameters
import pytest

from backend.mcp_onboarding_cloud import OnboardingCloud
from tests.test_mcp_onboarding import CONFIG

SETTINGS = {"account": "123456789012", "region": "us-west-2", "gateway_id": "test-gateway",
            "gateway_url": "https://test-gateway.gateway.bedrock-agentcore.us-west-2.amazonaws.com/mcp"}
STATE = {"id": "a" * 32, "name": "Data server", "description": "Discover data.",
         "target_name": "studio-remote-aaaaaaaaaaaa", "catalog_id": "mcp-remote-" + "a" * 32,
         "endpoint": "https://data.example.com/mcp", "connection_id": "data-service",
         "tools": [{"name": "list_datasets", "description": "List datasets.",
                    "inputSchema": {"type": "object", "properties": {}}}]}


def adapter():
    session = boto3.Session(region_name="us-west-2", aws_access_key_id="testing", aws_secret_access_key="testing")
    gateway_arn = "arn:aws:bedrock-agentcore:us-west-2:123456789012:gateway/test-gateway"
    gateway = {"gatewayArn": gateway_arn, "gatewayUrl": SETTINGS["gateway_url"], "status": "READY", "authorizerType": "AWS_IAM"}
    registry = {"registryArn": CONFIG["registry_arn"], "status": "READY"}
    control = SimpleNamespace(get_gateway=lambda **_: gateway)
    native = SimpleNamespace(get_registry=lambda **_: registry)
    cloud = OnboardingCloud(SETTINGS, session=session, control=control, registry=native,
                            transport=SimpleNamespace(discover=lambda: []))
    return cloud, session, control, native


def test_native_mcp_record_uses_current_sdk_descriptor_and_no_credentials():
    cloud, session, _, native = adapter()
    calls = []
    def create(**kw):
        validate_parameters(kw, session._session.get_service_model("agent-registry-control").operation_model("CreateRegistryRecord").input_shape)
        calls.append(kw)
    native.create_registry_record = create
    # Use real-shaped Registry identifiers for SDK validation.
    config = copy.deepcopy(CONFIG)
    config["registry_id"] = "a" * 16
    config["registry_arn"] = CONFIG["registry_arn"].rsplit("/", 1)[0] + "/" + config["registry_id"]
    native.get_registry = lambda **_: {"registryArn": config["registry_arn"], "status": "READY"}
    cloud.write("register", STATE, config)
    assert calls[0]["recordType"] == "MCP"
    assert calls[0]["tags"]["auto-delete"] == "no"
    assert "mcpServer" in calls[0]["descriptors"]
    assert "credential" not in str(calls[0]["descriptors"]).lower()


@pytest.mark.parametrize("description", ["", "Discover data.", "Long description. " * 20, "数据" * 150])
def test_server_descriptor_conforms_to_published_schema_and_keeps_full_registry_description(description):
    from jsonschema import Draft7Validator
    # Published schema pinned from its canonical $id, without network at test time.
    schema = json.loads((Path(__file__).parent / "fixtures/mcp/server-2025-12-11.schema.json").read_text())
    cloud, _, _, native = adapter()
    calls = []
    native.create_registry_record = lambda **kwargs: calls.append(kwargs)
    state = {**STATE, "description": description}
    cloud.write("register", state, CONFIG)
    server = json.loads(calls[0]["descriptors"]["mcpServer"]["data"])
    Draft7Validator(schema).validate(server)
    assert calls[0]["description"] == (description or STATE["name"])
    assert calls[0]["descriptors"]["mcpServer"]["additionalData"]["tools"]["data"] == json.dumps({"tools": STATE["tools"]}, sort_keys=True)


def test_gateway_target_uses_configured_authentication_and_sdk_shape():
    cloud, session, control, _ = adapter()
    calls = []
    def create(**kw):
        validate_parameters(kw, session._session.get_service_model("bedrock-agentcore-control").operation_model("CreateGatewayTarget").input_shape)
        calls.append(kw)
    control.create_gateway_target = create
    cloud.write("connect", STATE, CONFIG)
    assert calls[0]["credentialProviderConfigurations"] == [CONFIG["connections"][0]["configuration"]]
    assert calls[0]["targetConfiguration"]["mcp"]["mcpServer"]["endpoint"] == STATE["endpoint"]


def test_native_target_drift_stops_discovery():
    cloud, _, control, _ = adapter()
    control.list_gateway_targets = lambda **_: {"items": [{"name": STATE["target_name"], "targetId": "target"}]}
    control.get_gateway_target = lambda **_: {
        "name": STATE["target_name"], "targetId": "target", "gatewayArn": cloud.gateway_arn, "status": "READY",
        "targetConfiguration": {"mcp": {"mcpServer": {"endpoint": "https://other.example.com/mcp"}}},
        "credentialProviderConfigurations": [CONFIG["connections"][0]["configuration"]]}
    with pytest.raises(ValueError, match="binding changed"):
        cloud.discover(STATE, CONFIG)


def test_retirement_verifies_owned_target_then_uses_native_delete_and_reconciles_absence():
    cloud, session, control, native = adapter()
    calls = []
    state = {**STATE, "gateway_target_id": "target", "change": {"native_registry": False}}
    target = {"name": STATE["target_name"], "targetId": "target", "gatewayArn": cloud.gateway_arn, "status": "READY",
              "targetConfiguration": {"mcp": {"mcpServer": {"endpoint": STATE["endpoint"]}}},
              "credentialProviderConfigurations": [CONFIG["connections"][0]["configuration"]]}
    control.list_gateway_targets = lambda **_: {"items": [{"name": STATE["target_name"], "targetId": "target"}]}
    control.get_gateway_target = lambda **_: target
    control.delete_gateway_target = lambda **kwargs: calls.append(kwargs)
    assert cloud.read("retire_registry", state, CONFIG)["absent"]
    assert not cloud.read("retire_target", state, CONFIG)["absent"]
    cloud.write("retire_target", state, CONFIG)
    assert calls == [{"gatewayIdentifier": SETTINGS["gateway_id"], "targetId": "target"}]
    target["status"] = "DELETING"
    assert cloud.read("retire_target", state, CONFIG)["pending"]
    control.list_gateway_targets = lambda **_: {"items": []}
    assert cloud.read("retire_target", state, CONFIG)["absent"]


def test_edited_revision_uses_new_native_request_tokens():
    cloud, _, control, native = adapter()
    calls = []
    control.create_gateway_target = lambda **kwargs: calls.append(kwargs)
    cloud.write("connect", STATE, CONFIG)
    cloud.write("connect", {**STATE, "revision": 2, "target_name": STATE["target_name"] + "-r2"}, CONFIG)
    assert calls[0]["clientToken"] != calls[1]["clientToken"]
