import json
from urllib.parse import quote

import pytest
from botocore.validate import validate_parameters

from backend.foundation_runs import get, put
from tests.conftest import login
from tests.test_mcp_credentials import enable
from tests.test_mcp_onboarding import BODY as ONBOARD, setup, start
from tests.test_mcp_onboarding_cloud import STATE, adapter

ARN = "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/customer_mcp-AbCdEf1234"
ENDPOINT = "https://bedrock-agentcore.us-west-2.amazonaws.com/runtimes/" + quote(ARN, safe="") + "/invocations?qualifier=DEFAULT"
BODY = {"name": "Customer Runtime IAM", "endpoint": ENDPOINT,
        "idempotency_key": "runtime-iam-request-0001"}


def create(setup):
    _, cloud = enable(setup)
    response = setup[0].post("/api/admin/mcp/iam-credentials", json=BODY)
    assert response.status_code == 200, response.text
    return response.json(), cloud


def test_iam_connection_is_secretless_exact_endpoint_metadata_and_native_sdk_shape(setup):
    value, cloud = create(setup)
    assert value["phase"] == "READY" and cloud.writes == []
    with setup[1].tx() as db:
        connection = get(db, "mcp-auth:" + value["connection_id"])
        config = setup[2].config(db)
        rows = list(db.select("settings"))
    assert connection["allowed_endpoints"] == [ENDPOINT]
    assert connection["configuration"] == {
        "credentialProviderType": "GATEWAY_IAM_ROLE",
        "credentialProvider": {"iamCredentialProvider": {"service": "bedrock-agentcore", "region": "us-west-2"}}}
    assert "secret_arn" not in json.dumps([dict(r) for r in rows])
    native, session, control, _ = adapter()
    calls = []
    control.create_gateway_target = lambda **kwargs: calls.append(kwargs)
    state = {**STATE, "endpoint": ENDPOINT, "connection_id": value["connection_id"]}
    native.write("connect", state, config)
    validate_parameters(calls[0], session._session.get_service_model("bedrock-agentcore-control")
                        .operation_model("CreateGatewayTarget").input_shape)
    assert calls[0]["credentialProviderConfigurations"] == [connection["configuration"]]


def test_iam_request_replay_and_get_reconciliation_do_not_create_more_records(setup):
    first, _ = create(setup)
    assert setup[0].post("/api/admin/mcp/iam-credentials", json=BODY).json() == first
    assert setup[0].get("/api/admin/mcp/iam-credentials/" + BODY["idempotency_key"]).json() == first
    assert setup[0].post("/api/admin/mcp/iam-credentials", json={**BODY, "name": "Changed"}).status_code == 409
    assert len(setup[0].get("/api/admin/mcp/auth-connections").json()["items"]) == 2


def test_iam_replay_and_deletion_fail_closed_after_deployment_configuration_changes(setup):
    value, _ = create(setup)
    with setup[1].tx() as db:
        config = get(db, "mcp-onboarding")
        put(db, "mcp-onboarding", {**config, "credential_prefix": "another-installation"})
    assert setup[0].get("/api/admin/mcp/iam-credentials/" + BODY["idempotency_key"]).status_code == 409
    assert setup[0].post("/api/admin/mcp/iam-credentials", json=BODY).status_code == 409
    assert setup[0].post("/api/admin/mcp/auth-connections/" + value["connection_id"] + "/delete", json={
        "expected_revision": 1, "confirm_name": BODY["name"], "idempotency_key": "delete-stale-iam-request"}).status_code == 409
    with setup[1].tx() as db:
        assert get(db, "mcp-auth:" + value["connection_id"])


@pytest.mark.parametrize("endpoint", [
    "https://example.com/mcp",
    ENDPOINT.replace("123456789012", "999999999999"),
    ENDPOINT.replace("us-west-2", "us-east-1"),
    ENDPOINT.replace("?qualifier=DEFAULT", "?qualifier=OTHER"),
    ENDPOINT + "&token=not-a-real-token",
    ENDPOINT.replace("/invocations", "/mcp"),
    ENDPOINT.replace("https://", "http://"),
    ENDPOINT.replace("bedrock-agentcore.us-west-2.amazonaws.com", "bedrock-agentcore.us-west-2.amazonaws.com.example.com"),
])
def test_iam_signing_cannot_target_another_account_region_service_or_url(setup, endpoint):
    enable(setup)
    response = setup[0].post("/api/admin/mcp/iam-credentials", json={**BODY, "endpoint": endpoint})
    assert response.status_code == 422
    with setup[1].tx() as db:
        assert not any(r["key"].startswith("mcp-auth:") for r in db.select("settings"))


def test_iam_connection_cannot_be_reused_for_another_runtime_on_the_same_host(setup):
    value, _ = create(setup)
    response = setup[0].post("/api/admin/mcp/onboarding", json={
        **ONBOARD, "endpoint": ENDPOINT.replace("customer_mcp", "other_mcp"),
        "connection_id": value["connection_id"]})
    assert response.status_code == 422
    start(setup, {**ONBOARD, "endpoint": ENDPOINT, "connection_id": value["connection_id"]})
    info = setup[0].get("/api/admin/mcp/auth-connections/" + value["connection_id"]).json()
    assert not info["can_edit"] and not info["can_delete"] and info["references"]
    assert setup[0].post("/api/admin/mcp/auth-connections/" + value["connection_id"] + "/delete",
        json={"expected_revision": 1, "confirm_name": BODY["name"], "idempotency_key": "delete-iam-in-use-0001"}).status_code == 409


def test_unused_iam_delete_is_local_idempotent_and_cannot_be_resurrected(setup):
    value, cloud = create(setup)
    path = "/api/admin/mcp/auth-connections/" + value["connection_id"]
    info = setup[0].get(path).json()
    assert info["can_delete"] and not info["can_edit"]
    deletion = {"expected_revision": 1, "confirm_name": BODY["name"], "idempotency_key": "delete-iam-request-0001"}
    first = setup[0].post(path + "/delete", json=deletion)
    assert first.status_code == 200 and first.json()["phase"] == "DELETED"
    assert setup[0].post(path + "/delete", json=deletion).json() == first.json()
    assert setup[0].get(path + "/operations/" + deletion["idempotency_key"]).json() == first.json()
    assert setup[0].post("/api/admin/mcp/iam-credentials", json=BODY).json()["phase"] == "DELETED"
    assert cloud.writes == []
    with setup[1].tx() as db:
        assert get(db, "mcp-auth:" + value["connection_id"]) is None


def test_iam_creation_requires_admin_and_rejects_embedded_secrets(setup):
    enable(setup)
    response = setup[0].post("/api/admin/mcp/iam-credentials", json={**BODY, "secret": "must-not-be-stored"})
    assert response.status_code == 422 and "must-not-be-stored" not in response.text
    login(setup[0])
    assert setup[0].post("/api/admin/mcp/iam-credentials", json=BODY).status_code == 403
    assert setup[0].get("/api/admin/mcp/iam-credentials/" + BODY["idempotency_key"]).status_code == 403


def test_per_user_iam_connection_pins_native_identity_and_forwards_only_its_header(setup):
    from tests.test_mcp_user_configuration import native, AUTH
    from backend.mcp_user_oauth import USER_TOKEN_HEADER
    enable(setup)
    cloud, *_ = native()
    setup[2].oauth_cloud = cloud
    setup[2].auth = AUTH
    response = setup[0].post("/api/admin/mcp/iam-credentials", json={**BODY, "user_authorization": True})
    assert response.status_code == 200, response.text
    assert response.json()["user_authorization"] is True
    with setup[1].tx() as db:
        config = setup[2].config(db)
    connection = next(c for c in config["connections"] if c["id"] == response.json()["connection_id"])
    assert connection["user_authorization"]["client_id"] == AUTH.client_id
    target, session, control, _ = adapter()
    calls = []
    control.create_gateway_target = lambda **kwargs: calls.append(kwargs)
    target.write("connect", {**STATE, "endpoint": ENDPOINT, "connection_id": connection["id"]}, config)
    assert calls[0]["metadataConfiguration"] == {"allowedRequestHeaders": [USER_TOKEN_HEADER]}
    validate_parameters(calls[0], session._session.get_service_model("bedrock-agentcore-control")
                        .operation_model("CreateGatewayTarget").input_shape)
    assert setup[0].post("/api/admin/mcp/iam-credentials",
                        json={**BODY, "user_authorization": True}).json() == response.json()
    assert setup[0].post("/api/admin/mcp/iam-credentials", json=BODY).status_code == 409
