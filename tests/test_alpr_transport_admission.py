"""No AWS calls: signed exchange payloads, native entry gates and provider schemas."""
from dataclasses import replace
import json
from types import SimpleNamespace
import time

import pytest
from botocore.session import Session
from botocore.validate import validate_parameters

from backend import serverless
from foundation_harness.alpr_exchange import CALLER_HEADER, CHANNEL, SpecialistExchange
from foundation_harness.config import digest
from foundation_harness.context import Binding, Denied
from foundation_harness.journey_mcp import GatewayMCP, GatewayFailure
from foundation_harness.journey_runtime import execute
from scripts.journey_platform import alpr_target_configuration

URL = "https://offline.gateway.bedrock-agentcore.us-west-2.amazonaws.com/mcp"
TOOL = "alpr-investigation-specialists___agent-alpr-remediation"
ARGS = {"question": "Investigate ALPR-C001"}
CONFIG = {"endpoint": "https://offline.execute-api.us-west-2.amazonaws.com/internal/journey/alpr",
          "runtime_arn": "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/alpr-B",
          "runtime_version": "1", "deployment_digest": "d" * 64,
          "channel": CHANNEL, "channel_evidence_digest": "e" * 64}
MANIFEST = {"owner": "alex", "workspace": "research", "foundation": {"digest": "f" * 64},
            "tools": [{"name": TOOL}]}
SESSION = "s" * 33


def gateway(authority=None):
    return GatewayMCP(SimpleNamespace(region_name="us-west-2"), URL, alpr_admission=authority)


def binding(manifest=MANIFEST):
    return Binding("job", "alex", "research", "role-A", "runtime-A", "1", SESSION,
                   digest(manifest), digest(manifest["foundation"]), 0, time.time() + 60)


class Authority:
    def __init__(self):
        self.binding = binding()
        self.calls = []

    def resolve(self):
        return self.binding

    def authorize(self, bound, operation, resource):
        assert bound == self.binding
        self.calls.append((operation, resource))

    def issue(self, bound, name, arguments):
        assert bound == self.binding and name == TOOL and arguments == ARGS
        return "a" * 43


def test_native_live_run_fails_before_model_or_gateway_without_exchange():
    with pytest.raises(GatewayFailure, match="RUN_EXCHANGE_NOT_CONNECTED"):
        execute(MANIFEST, ARGS["question"], SESSION, model=object(), gateway=gateway())
    with pytest.raises(GatewayFailure, match="RUN_EXCHANGE_NOT_CONNECTED"):
        gateway().call(TOOL, ARGS)
    with pytest.raises(GatewayFailure, match="CALLER_REFERENCE_REQUIRED"):
        gateway().rpc("tools/call", {"name": TOOL, "arguments": ARGS})


@pytest.mark.parametrize("change", [{"workspace": "other"}, {"manifest_digest": "e" * 64},
                                  {"runtime_session": "other"}, {"owner": "admin"},
                                  {"expires_at": float("nan")}])
def test_gateway_admission_rejects_misbound_server_response(change):
    authority = Authority()
    authority.binding = replace(authority.binding, **change)
    with pytest.raises(GatewayFailure, match="IMMUTABLE_RUN_BINDING"):
        gateway(authority).admit_alpr(MANIFEST, SESSION)
    assert not authority.calls


def test_failed_readmission_cannot_reuse_previous_binding():
    authority = Authority()
    client = gateway(authority)
    client.admit_alpr(MANIFEST, SESSION)
    authority.binding = replace(authority.binding, workspace="other")
    with pytest.raises(GatewayFailure):
        client.admit_alpr(MANIFEST, SESSION)
    with pytest.raises(GatewayFailure, match="RUN_EXCHANGE_NOT_CONNECTED"):
        client.call(TOOL, ARGS)


def test_only_server_issued_capability_is_passed_outside_arguments(monkeypatch):
    authority = Authority()
    client = gateway(authority)
    client.admit_alpr(MANIFEST, SESSION)
    wire = []
    monkeypatch.setattr(client, "rpc", lambda method, params, **extra:
        wire.append((method, params, extra)) or {"content": [{"type": "text", "text": "live evidence"}]})
    assert client.call(TOOL, ARGS) == "live evidence"
    assert wire == [("tools/call", {"name": TOOL, "arguments": ARGS}, {"caller_reference": "a" * 43})]
    assert authority.calls == [("start", ""), ("tool", TOOL)]
    client.finish_alpr()
    assert authority.calls[-1] == ("finish", "")


def test_signed_exchange_uses_fixed_endpoint_runtime_and_bounded_existing_transport(monkeypatch):
    client = SpecialistExchange(SimpleNamespace(region_name="us-west-2"), CONFIG)
    sent = []
    def send(url, raw, headers, timeout, service):
        sent.append((url, json.loads(raw), headers, timeout, service))
        return {"reference": "a" * 43}, {}
    monkeypatch.setattr(client.transport, "send", send)
    client.request("a" * 43, "redeem", tool=TOOL, arguments_digest=digest(ARGS))
    url, body, headers, timeout, service = sent[0]
    assert url == CONFIG["endpoint"] and service == "execute-api" and timeout == 3
    assert body["runtime_arn"] == CONFIG["runtime_arn"] and body["runtime_version"] == "1"
    assert headers == {"Accept": "application/json"} and "scope" not in body


@pytest.mark.parametrize("change", [{"channel": "assume-default-forwarding"},
    {"channel_evidence_digest": ""}, {"runtime_version": "DEFAULT"},
    {"endpoint": "https://example.com/internal/journey/alpr"}])
def test_unverified_or_unpinned_exchange_is_rejected(change):
    with pytest.raises(Denied, match="VERIFIED_EXCHANGE_CONFIGURATION"):
        SpecialistExchange(SimpleNamespace(region_name="us-west-2"), {**CONFIG, **change})


def test_missing_listing_context_denies_without_network(monkeypatch):
    client = SpecialistExchange(SimpleNamespace(region_name="us-west-2"), CONFIG)
    monkeypatch.setattr(client.transport, "send", lambda *a: pytest.fail("network"))
    with pytest.raises(Denied, match="CALLER_REFERENCE_REQUIRED"):
        client.list_tools(None)


def test_gateway_header_channel_is_in_installed_provider_schema():
    config = alpr_target_configuration({"account": "123456789012", "region": "us-west-2"}, CONFIG["runtime_arn"])
    model = Session().get_service_model("bedrock-agentcore-control")
    shape = model.operation_model("CreateGatewayTarget").input_shape
    validate_parameters({"gatewayIdentifier": "offline-gateway", **config}, shape)
    runtime_shape = model.operation_model("CreateAgentRuntime").input_shape.members["requestHeaderConfiguration"]
    validate_parameters({"requestHeaderAllowlist": [CALLER_HEADER]}, runtime_shape)
    assert config["name"] == "alpr-investigation-specialists"
    assert config["metadataConfiguration"] == {"allowedRequestHeaders": [CALLER_HEADER]}


def test_admission_handler_is_disabled_and_not_on_existing_product_route(monkeypatch):
    monkeypatch.delenv("JOURNEY_ALPR_EXCHANGE_ENABLED", raising=False)
    assert serverless.journey_alpr_exchange_handler({}, None)["statusCode"] == 403
    monkeypatch.setenv("JOURNEY_ALPR_EXCHANGE_ENABLED", "1")
    assert serverless.journey_alpr_exchange_handler(
        {"routeKey": "POST /internal/foundation/exchange"}, None)["statusCode"] == 403


@pytest.mark.parametrize("event_context", [{}, {"authorizer": {"lambda": {"userArn": "admin"}}},
                                   {"authorizer": {"iam": {"userArn": "admin"}}, "apiId": "wrong"}])
def test_handler_denies_spoofed_api_context_before_store_access(monkeypatch, event_context):
    monkeypatch.setenv("JOURNEY_ALPR_EXCHANGE_ENABLED", "1")
    monkeypatch.setenv("JOURNEY_ALPR_API_ID", "offline")
    monkeypatch.setattr(serverless, "store", lambda: pytest.fail("unauthenticated store access"))
    response = serverless.journey_alpr_exchange_handler({
        "routeKey": "POST /internal/journey/alpr", "requestContext": event_context}, None)
    assert response["statusCode"] == 403
