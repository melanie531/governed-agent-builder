import json
from io import BytesIO
from copy import deepcopy
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError

from backend.journey_cloud import JourneyCloud


@pytest.mark.parametrize("completed,expected", [(0, True), (1, False), (6, False), (None, False)])
def test_consent_challenge_only_allows_auto_resume_with_zero_completed_tool_calls(completed, expected):
    cloud = object.__new__(JourneyCloud)
    cloud.ready = lambda _: True
    challenge = {"status": "AUTHORIZATION_REQUIRED", "authorization": {},
                 "definition_digest": "definition", "session_id": "gab-" + "r" * 32}
    if completed is not None:
        challenge["completed_tool_calls"] = completed
    cloud.data = SimpleNamespace(invoke_agent_runtime=lambda **_: {
        "response": BytesIO(json.dumps(challenge).encode())})
    result = cloud.invoke({"arn": "runtime"}, {"digest": "definition"}, "Question", "r" * 32)
    assert (result.get("completed_tool_calls") == 0) is expected


@pytest.mark.parametrize("completed", ["0", -1, True, 7])
def test_consent_challenge_rejects_untrusted_resume_proof(completed):
    cloud = object.__new__(JourneyCloud)
    cloud.ready = lambda _: True
    challenge = {"status": "AUTHORIZATION_REQUIRED", "authorization": {},
                 "definition_digest": "definition", "session_id": "gab-" + "r" * 32,
                 "completed_tool_calls": completed}
    cloud.data = SimpleNamespace(invoke_agent_runtime=lambda **_: {
        "response": BytesIO(json.dumps(challenge).encode())})
    with pytest.raises(ValueError, match="Authorization challenge"):
        cloud.invoke({"arn": "runtime"}, {"digest": "definition"}, "Question", "r" * 32)


def test_force_gateway_auth_is_forwarded_only_to_capable_runtime():
    cloud = object.__new__(JourneyCloud)
    cloud.ready = lambda _: True
    payloads = []
    def invoke(**request):
        payloads.append(json.loads(request["payload"]))
        return {"response": BytesIO(json.dumps({"status": "AUTHORIZATION_REQUIRED",
            "authorization": {}, "definition_digest": "definition",
            "session_id": "gab-" + "r" * 32, "completed_tool_calls": 0}).encode())}
    cloud.data = SimpleNamespace(invoke_agent_runtime=invoke)
    definition = {"digest": "definition"}
    cloud.invoke({"arn": "runtime", "gateway_force_auth_v1": True}, definition, "Question",
                 "r" * 32, force_gateway_auth=True)
    assert payloads[0]["force_gateway_auth"] is True
    with pytest.raises(ValueError, match="does not support"):
        cloud.invoke({"arn": "runtime"}, definition, "Question", "r" * 32,
                     force_gateway_auth=True)
    assert len(payloads) == 1


def _runtime_recovery_fixture(changed=None, conflict=True):
    manifest = {
        "agent_id": "a" * 32, "workspace": "research",
        "artifact": {"bucket": "artifact-bucket", "key": "journey/runtime.zip", "version_id": "artifact-version"},
    }
    token = "b" * 64
    name = "gab_journey_" + token[:24]
    runtime_id = name + "-RUNTIME123"
    arn = f"arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/{runtime_id}"
    location = {"bucket": "evidence-bucket", "key": "journey/manifests/manifest.json",
                "version_id": "manifest-version", "digest": "manifest-digest"}
    tags = {"project": "governed-agent-builder", "journey": "create-agent",
            "agent": manifest["agent_id"], "workspace": manifest["workspace"], "auto-delete": "no"}
    native = {
        "agentRuntimeId": runtime_id, "agentRuntimeArn": arn, "agentRuntimeVersion": "1",
        "agentRuntimeName": name, "status": "READY",
        "agentRuntimeArtifact": {"codeConfiguration": {
            "code": {"s3": {"bucket": "artifact-bucket", "prefix": "journey/runtime.zip",
                            "versionId": "artifact-version"}},
            "runtime": "PYTHON_3_13", "entryPoint": ["main.py"]}},
        "roleArn": "arn:aws:iam::123456789012:role/runtime",
        "networkConfiguration": {"networkMode": "PUBLIC"},
        "protocolConfiguration": {"serverProtocol": "HTTP"},
        "lifecycleConfiguration": {"idleRuntimeSessionTimeout": 60, "maxLifetime": 900},
        "environmentVariables": {"JOURNEY_MANIFEST": json.dumps(location, separators=(",", ":"))},
    }
    if changed:
        changed(native, tags)
    calls = {"create": [], "get": [], "tags": []}

    def create(**request):
        calls["create"].append(request)
        if conflict:
            raise ClientError({"Error": {"Code": "ConflictException", "Message": "already exists"}},
                              "CreateAgentRuntime")
        return {"agentRuntimeId": runtime_id, "agentRuntimeArn": arn, "agentRuntimeVersion": "1"}

    def get(**request):
        calls["get"].append(request)
        return deepcopy(native)

    def list_tags(**request):
        calls["tags"].append(request)
        return {"tags": deepcopy(tags)}

    control = SimpleNamespace(
        create_agent_runtime=create,
        list_agent_runtimes=lambda **_: {"agentRuntimes": [{
            "agentRuntimeId": runtime_id, "agentRuntimeArn": arn,
            "agentRuntimeVersion": "1", "agentRuntimeName": name}]},
        get_agent_runtime=get, list_tags_for_resource=list_tags)
    cloud = object.__new__(JourneyCloud)
    cloud.settings = {"account": "123456789012", "region": "us-east-1",
                      "runtime_role": "arn:aws:iam::123456789012:role/runtime",
                      "network": {"networkMode": "PUBLIC"}}
    cloud.control = control
    cloud.write = lambda *_: location
    return cloud, manifest, token, location, calls


def test_create_reconciles_exact_owned_runtime_after_name_conflict():
    cloud, manifest, token, location, calls = _runtime_recovery_fixture()
    assert cloud.create(manifest, token) == {
        "id": "gab_journey_" + token[:24] + "-RUNTIME123",
        "arn": f"arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/gab_journey_{token[:24]}-RUNTIME123",
        "version": "1", "manifest": location}
    assert len(calls["create"]) == 1
    assert calls["get"] == [{"agentRuntimeId": "gab_journey_" + token[:24] + "-RUNTIME123",
                             "agentRuntimeVersion": "1"}]
    assert len(calls["tags"]) == 1


@pytest.mark.parametrize("changed", [
    lambda runtime, tags: runtime["environmentVariables"].update({"JOURNEY_MANIFEST": "{}"}),
    lambda runtime, tags: runtime["agentRuntimeArtifact"]["codeConfiguration"]["code"]["s3"].update(
        {"versionId": "other-version"}),
    lambda runtime, tags: tags.update({"workspace": "other-workspace"}),
    lambda runtime, tags: tags.update({"auto-delete": "yes"}),
    lambda runtime, tags: runtime.update({"roleArn": "arn:aws:iam::123456789012:role/other"}),
])
def test_create_refuses_conflicting_runtime_without_exact_configuration_and_ownership(changed):
    cloud, manifest, token, _, calls = _runtime_recovery_fixture(changed)
    with pytest.raises(ValueError, match="operator review"):
        cloud.create(manifest, token)
    assert len(calls["create"]) == 1


def test_create_preserves_conflict_when_no_matching_runtime_is_visible():
    cloud, manifest, token, _, calls = _runtime_recovery_fixture()
    cloud.control.list_agent_runtimes = lambda **_: {"agentRuntimes": []}
    with pytest.raises(ClientError) as caught:
        cloud.create(manifest, token)
    assert caught.value.response["Error"]["Code"] == "ConflictException"
    assert calls["get"] == []
    assert calls["tags"] == []


def test_create_without_conflict_does_not_need_recovery_lookup():
    cloud, manifest, token, location, calls = _runtime_recovery_fixture(conflict=False)
    cloud.control.list_agent_runtimes = lambda **_: pytest.fail("normal create should not list runtimes")
    assert cloud.create(manifest, token)["manifest"] == location
    assert len(calls["create"]) == 1


@pytest.mark.parametrize("conflict", [False, True])
def test_new_runtime_binding_retains_native_gateway_force_capability_after_recovery(conflict):
    cloud, manifest, token, _, _ = _runtime_recovery_fixture(conflict=conflict)
    manifest["gateway_force_auth_v1"] = True
    assert cloud.create(manifest, token)["gateway_force_auth_v1"] is True


@pytest.mark.parametrize("endpoint,expected", [
    ({"liveVersion": "1"}, True),  # Real READY responses omit targetVersion.
    ({"liveVersion": "1", "targetVersion": "1"}, True),
    ({"liveVersion": "2"}, False),
    ({"liveVersion": "1", "targetVersion": "2"}, False),
])
def test_ready_checks_live_version_and_optional_update_target(endpoint, expected):
    binding = {"id": "runtime-id", "arn": "runtime-arn", "version": "1", "manifest": {"digest": "manifest-digest"}}
    cloud = object.__new__(JourneyCloud)
    cloud.control = SimpleNamespace(
        get_agent_runtime=lambda **_: {"agentRuntimeArn": binding["arn"], "agentRuntimeVersion": "1", "status": "READY",
                                      "environmentVariables": {"JOURNEY_MANIFEST": json.dumps(binding["manifest"])}},
        get_agent_runtime_endpoint=lambda **_: {"status": "READY", "agentRuntimeArn": binding["arn"], **endpoint})
    assert cloud.ready(binding) is expected
