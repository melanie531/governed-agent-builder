"""CUSTOM Catalog records use the installed native Registry SDK contract."""
from datetime import datetime, timezone
import json

import boto3
from botocore.stub import Stubber
from fastapi import HTTPException
import pytest

from backend.platform_cloud import PlatformCloud, registry_descriptor
from foundation_harness.config import digest
from tests.test_mcp_onboarding import CONFIG


MODEL_ID = "global.amazon.nova-micro-v1:0"
ITEM = {
    "id": "model-native-test", "kind": "model", "version": "1",
    "description": "Model reviewed for research.",
    "binding": {"model_id": MODEL_ID},
    "binding_digest": digest({"model_id": MODEL_ID}),
}
RECORD_ID = "record-test01"
RECORD_ARN = CONFIG["registry_arn"] + "/record/" + RECORD_ID
RECORD = {
    "registryArn": CONFIG["registry_arn"], "recordArn": RECORD_ARN,
    "recordId": RECORD_ID, "name": ITEM["id"], "recordType": "CUSTOM",
    "recordVersion": "1", "status": "DRAFT",
    "createdAt": datetime(2026, 10, 6, tzinfo=timezone.utc),
    "updatedAt": datetime(2026, 10, 6, tzinfo=timezone.utc),
    "descriptors": {"custom": {"data": json.dumps(registry_descriptor(ITEM))}},
}
PROFILE = {
    "inferenceProfileName": "Global Nova Micro",
    "inferenceProfileArn": f"arn:aws:bedrock:us-west-2:123456789012:inference-profile/{MODEL_ID}",
    "models": [{"modelArn": "arn:aws:bedrock:us-west-2::foundation-model/amazon.nova-micro-v1:0"}],
    "inferenceProfileId": MODEL_ID, "status": "ACTIVE", "type": "SYSTEM_DEFINED",
}


@pytest.fixture
def native():
    session = boto3.Session(region_name="us-west-2",
                            aws_access_key_id="testing", aws_secret_access_key="testing")
    client = session.client("agent-registry-control")
    bedrock = session.client("bedrock")
    adapter = PlatformCloud({"account": "123456789012", "region": "us-west-2",
                             "mcp_onboarding": dict(CONFIG)}, session=session)

    def sdk(service):
        if service == "bedrock":
            return bedrock
        assert service == "agent-registry-control"
        return client

    adapter.client = sdk
    with Stubber(client) as stub, Stubber(bedrock) as bedrock_stub:
        yield adapter, stub, bedrock_stub
        stub.assert_no_pending_responses()
        bedrock_stub.assert_no_pending_responses()


def create_parameters():
    return {
        "registryId": CONFIG["registry_id"], "name": ITEM["id"],
        "description": ITEM["description"], "recordType": "CUSTOM",
        "recordVersion": "1", "descriptors": RECORD["descriptors"],
        "clientToken": digest([CONFIG["registry_arn"], ITEM["id"], "1", ITEM["binding_digest"]]),
        "tags": {"auto-delete": "no", "project": "governed-agent-builder"},
    }


def test_model_uses_native_custom_record_through_approval(native):
    adapter, stub, bedrock_stub = native
    identity = {"registryId": CONFIG["registry_id"], "recordId": RECORD_ID}
    bedrock_stub.add_response("get_inference_profile", PROFILE,
                              {"inferenceProfileIdentifier": MODEL_ID})
    stub.add_response("create_registry_record", {"recordArn": RECORD_ARN, "status": "CREATING"},
                      create_parameters())
    for status in ("DRAFT", "PENDING_APPROVAL"):
        stub.add_response("get_registry_record", {**RECORD, "status": status}, identity)
        operation = ("submit_registry_record_for_approval" if status == "DRAFT"
                     else "update_registry_record_status")
        outcome = "PENDING_APPROVAL" if status == "DRAFT" else "APPROVED"
        params = identity if status == "DRAFT" else {
            **identity, "status": "APPROVED", "statusReason": "Reviewed model"}
        stub.add_response(operation, {
            "registryArn": CONFIG["registry_arn"], "recordArn": RECORD_ARN,
            "recordId": RECORD_ID, "status": outcome, "updatedAt": RECORD["updatedAt"],
            **({"statusReason": "Reviewed model"} if status != "DRAFT" else {}),
        }, params)
    binding = adapter.register(ITEM)
    assert binding == {
        "arn": RECORD_ARN, "status": "CREATING", "version": "1",
        "binding_digest": ITEM["binding_digest"], "descriptor_type": "custom",
    }
    assert adapter.submit(binding) == "PENDING_APPROVAL"
    assert adapter.decide(binding, True, "Reviewed model") == "APPROVED"


@pytest.mark.parametrize("kind", ["custom", "mcpServer"])
def test_native_record_cannot_cross_registry_boundary(native, kind):
    adapter, _, _ = native
    with pytest.raises(HTTPException) as error:
        adapter.record({"descriptor_type": kind, "arn": RECORD_ARN.replace("123456789012", "999999999999")})
    assert error.value.status_code == 409


def test_native_registration_rejects_foreign_configuration_before_sdk_call(native):
    adapter, _, _ = native
    adapter.settings["mcp_onboarding"]["registry_arn"] = CONFIG["registry_arn"].replace(
        "123456789012", "999999999999")
    with pytest.raises(HTTPException):
        adapter.register(ITEM)


def test_native_registration_does_not_accept_foreign_record_receipt(native):
    adapter, stub, bedrock_stub = native
    bedrock_stub.add_response("get_inference_profile", PROFILE,
                              {"inferenceProfileIdentifier": MODEL_ID})
    stub.add_response("create_registry_record", {
        "recordArn": RECORD_ARN.replace("123456789012", "999999999999"), "status": "CREATING",
    }, create_parameters())
    with pytest.raises(HTTPException) as error:
        adapter.register(ITEM)
    assert error.value.status_code == 409
