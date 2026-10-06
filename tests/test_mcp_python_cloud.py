import copy
import hashlib
from types import SimpleNamespace

import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber
from botocore.validate import validate_parameters

from backend.mcp_python_cloud import PythonCloud, TOKEN_HEADER
from tests.test_mcp_python import BODY, CONFIG


def native():
    cloud = PythonCloud({"account": "123456789012", "region": "us-west-2"},
        boto3.Session(aws_access_key_id="test", aws_secret_access_key="test", region_name="us-west-2"))
    sid = "a" * 32
    state = {**{k: v for k, v in BODY.items() if k not in ("source", "idempotency_key")},
        "id": sid, "source_digest": hashlib.sha256(BODY["source"].encode()).hexdigest(),
        "runtime_name": CONFIG["runtime_prefix"] + "_" + sid[:24],
        "artifact": {**CONFIG["artifact"], "key": "mcp/python/" + sid + "/runtime.zip", "version_id": "package-v1"}}
    request = cloud.runtime_request(state, CONFIG)
    rid = state["runtime_name"] + "-abc123"
    arn = "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/" + rid
    runtime = {k: v for k, v in request.items() if k not in ("clientToken", "tags")}
    runtime.update(agentRuntimeId=rid, agentRuntimeArn=arn, agentRuntimeVersion="1",
                   createdAt=0, lastUpdatedAt=0, status="READY", workloadIdentityDetails={
                       "workloadIdentityArn": arn.split(":runtime/")[0] + ":workload-identity-directory/default/workload-identity/" + rid})
    endpoint = {"agentRuntimeEndpointArn": arn + "/runtime-endpoint/DEFAULT",
        "agentRuntimeArn": arn, "status": "READY", "createdAt": 0, "lastUpdatedAt": 0,
        "name": "DEFAULT", "id": "DEFAULT", "liveVersion": "1"}
    return cloud, state, request, runtime, endpoint


def test_python_native_request_is_sdk_valid_and_has_no_credential_or_general_agent_permissions():
    cloud, state, request, runtime, _ = native()
    validate_parameters(request, cloud.control.meta.service_model.operation_model("CreateAgentRuntime").input_shape)
    assert "authorizerConfiguration" not in request
    assert request["roleArn"] == CONFIG["runtime_role"]
    assert request["protocolConfiguration"] == {"serverProtocol": "MCP"}
    assert request["requestHeaderConfiguration"] == {"requestHeaderAllowlist": [TOKEN_HEADER]}
    assert set(request["environmentVariables"]) == {
        "SNOWFLAKE_ACCOUNT", "SNOWFLAKE_ROLE", "SNOWFLAKE_WAREHOUSE", "SNOWFLAKE_AUTH_SOURCE"}
    assert request["tags"]["auto-delete"] == "no"
    with Stubber(cloud.control) as control:
        control.add_response("create_agent_runtime", {k: runtime[k] for k in (
            "agentRuntimeArn", "agentRuntimeId", "agentRuntimeVersion", "createdAt", "status")}, request)
        cloud.write("runtime", state, CONFIG)
        control.assert_no_pending_responses()


@pytest.mark.parametrize("listing", ["absent", "present", "denied"])
def test_package_lookup_distinguishes_missing_objects_from_denied_reads(listing):
    cloud, state, _, _, _ = native()
    key = "mcp/python/" + "a" * 32 + "/runtime.zip"
    request = {"Bucket": CONFIG["artifact"]["bucket"], "Key": key,
               "ExpectedBucketOwner": "123456789012", "ChecksumMode": "ENABLED"}
    lookup = {"Bucket": CONFIG["artifact"]["bucket"], "Prefix": key,
              "MaxKeys": 1, "ExpectedBucketOwner": "123456789012"}
    with Stubber(cloud.s3) as s3:
        # S3 hides a missing key behind 403 when the role cannot list the whole bucket.
        s3.add_client_error("head_object", service_error_code="403", http_status_code=403,
                            expected_params=request)
        if listing == "denied":
            s3.add_client_error("list_objects_v2", service_error_code="AccessDenied",
                                http_status_code=403, expected_params=lookup)
        else:
            s3.add_response("list_objects_v2", {
                "IsTruncated": False, "KeyCount": int(listing == "present"),
                "Contents": [{"Key": key, "Size": 100}] if listing == "present" else [],
            }, lookup)
        if listing == "absent":
            assert cloud.read("package", state, CONFIG) is None
        else:
            with pytest.raises(ClientError):
                cloud.read("package", state, CONFIG)
        s3.assert_no_pending_responses()


@pytest.mark.parametrize("fault", [None, "role", "artifact_version", "inbound_auth", "tags", "endpoint_version", "identity"])
@pytest.mark.parametrize("retiring", [False, True])
def test_native_python_reconciliation_refuses_changed_bindings(fault, retiring):
    cloud, state, _, original, endpoint = native()
    runtime = copy.deepcopy(original)
    if fault == "role":
        runtime["roleArn"] += "-other"
    elif fault == "artifact_version":
        runtime["agentRuntimeArtifact"]["codeConfiguration"]["code"]["s3"]["versionId"] = "unapproved"
    elif fault == "inbound_auth":
        runtime["authorizerConfiguration"] = {"customJWTAuthorizer": {"discoveryUrl": "https://elsewhere.example.com/.well-known/openid-configuration"}}
    elif fault == "endpoint_version":
        endpoint["liveVersion"] = "2"
    elif fault == "identity":
        runtime["workloadIdentityDetails"]["workloadIdentityArn"] += "-foreign"
    with Stubber(cloud.control) as control:
        control.add_response("list_agent_runtimes", {"agentRuntimes": [{"description": "Studio Python MCP", **{
            k: original[k] for k in ("agentRuntimeArn", "agentRuntimeId", "agentRuntimeName", "agentRuntimeVersion", "lastUpdatedAt", "status")}}]}, {})
        control.add_response("get_agent_runtime", runtime, {
            "agentRuntimeId": original["agentRuntimeId"], "agentRuntimeVersion": "1"})
        if fault not in ("role", "artifact_version", "inbound_auth"):
            tags = cloud.tags(state, CONFIG)
            if fault == "tags":
                tags["source-digest"] = "0" * 64
            control.add_response("list_tags_for_resource", {"tags": tags}, {"resourceArn": original["agentRuntimeArn"]})
            if fault != "tags":
                control.add_response("get_agent_runtime_endpoint", endpoint, {
                    "agentRuntimeId": original["agentRuntimeId"], "endpointName": "DEFAULT"})
        if fault:
            with pytest.raises(ValueError, match="changed"):
                cloud.runtime(state, CONFIG, retiring=retiring)
        else:
            assert cloud.runtime(state, CONFIG, retiring=retiring) == {
                "runtime_id": original["agentRuntimeId"], "runtime_arn": original["agentRuntimeArn"], "runtime_version": "1"}
        control.assert_no_pending_responses()


def test_generated_runtime_endpoint_and_identity_are_tagged_before_log_stage_completes(monkeypatch):
    cloud, state, _, native_runtime, endpoint = native()
    runtime = {"runtime_id": native_runtime["agentRuntimeId"], "runtime_arn": native_runtime["agentRuntimeArn"], "runtime_version": "1"}
    monkeypatch.setattr(cloud, "runtime", lambda *args: runtime)
    monkeypatch.setattr("backend.journey_runtime_logs.provision", lambda *args, **kwargs: {"logGroupName": "owned"})
    arns = [endpoint["agentRuntimeEndpointArn"], native_runtime["workloadIdentityDetails"]["workloadIdentityArn"]]
    with Stubber(cloud.control) as control:
        for arn in arns:
            control.add_response("list_tags_for_resource", {"tags": {}}, {"resourceArn": arn})
        for arn in arns:
            control.add_response("tag_resource", {}, {"resourceArn": arn, "tags": cloud.tags(state, CONFIG)})
        cloud.write("logs", state, CONFIG)
        control.assert_no_pending_responses()


def test_ready_logs_are_insufficient_when_generated_resources_have_no_retention_tags(monkeypatch):
    cloud, state, _, native_runtime, endpoint = native()
    runtime = {"runtime_id": native_runtime["agentRuntimeId"], "runtime_arn": native_runtime["agentRuntimeArn"], "runtime_version": "1"}
    monkeypatch.setattr(cloud, "runtime", lambda *args: runtime)
    name = "/aws/bedrock-agentcore/runtimes/" + runtime["runtime_id"] + "-DEFAULT"
    cloud.logs = SimpleNamespace(
        describe_log_groups=lambda **_: {"logGroups": [{"logGroupName": name, "arn": "arn:owned-log:*", "retentionInDays": 14}]},
        list_tags_for_resource=lambda **_: {"tags": cloud.tags(state, CONFIG)})
    with Stubber(cloud.control) as control:
        for arn in (endpoint["agentRuntimeEndpointArn"], native_runtime["workloadIdentityDetails"]["workloadIdentityArn"]):
            control.add_response("list_tags_for_resource", {"tags": {}}, {"resourceArn": arn})
        assert cloud.read("logs", state, CONFIG) is None
        control.assert_no_pending_responses()
