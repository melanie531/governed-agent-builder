import copy
import json

from backend.foundation_runs import get, put
from tests.test_mcp_onboarding import CONFIG, BODY as ONBOARD_BODY, setup, start, drain, detail

BODY = {"name": "Support API", "endpoint": "https://support.example.com/mcp",
        "header": "Authorization", "prefix": "Bearer", "secret": "test-only-credential",
        "idempotency_key": "credential-request-0001"}


class CredentialCloud:
    def __init__(self):
        self.secrets, self.providers, self.writes = {}, {}, []
        self.lose = None

    def read_secret(self, state):
        return self.secrets.get(state["id"])

    def create_secret(self, state, value):
        assert value == BODY["secret"]
        self.writes.append("secret")
        self.secrets[state["id"]] = "arn:aws:secretsmanager:us-west-2:123456789012:secret:test"
        if self.lose == "secret":
            raise TimeoutError(value)

    def read_provider(self, state):
        return self.providers.get(state["id"])

    def create_provider(self, state):
        self.writes.append("provider")
        self.providers[state["id"]] = (
            "arn:aws:bedrock-agentcore:us-west-2:123456789012:token-vault/default/apikeycredentialprovider/" + state["provider_name"])
        if self.lose == "provider":
            raise TimeoutError(BODY["secret"])


def enable(setup):
    from backend.mcp_credentials import Credentials
    cloud = CredentialCloud()
    service = Credentials(setup[2], cloud)
    setup[2].credentials = service
    with setup[1].tx() as db:
        put(db, "mcp-onboarding", {**copy.deepcopy(CONFIG), "credential_prefix": "test-studio"})
    return service, cloud


def test_admin_creates_origin_bound_authentication_without_storing_or_returning_secret(setup):
    _, cloud = enable(setup)
    response = setup[0].post("/api/admin/mcp/credentials", json=BODY)
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["phase"] == "READY"
    assert cloud.writes == ["secret", "provider"]
    options = setup[0].get("/api/admin/mcp/onboarding-options").json()
    assert options["credential_setup"] is True
    connection = next(c for c in options["connections"] if c["id"] == value["connection_id"])
    assert connection["allowed_origins"] == ["https://support.example.com"]
    with setup[1].tx() as db:
        persisted = json.dumps([dict(r) for r in db.select("settings")] + [dict(r) for r in db.select("audit")])
    assert BODY["secret"] not in persisted + response.text + json.dumps(options)
    assert setup[0].post("/api/admin/mcp/credentials", json=BODY).json() == value
    assert cloud.writes == ["secret", "provider"]
    request = {**ONBOARD_BODY,
               "endpoint": BODY["endpoint"], "connection_id": value["connection_id"]}
    created = start(setup, request)
    drain(setup[2], created["job_id"])
    assert detail(setup, created)["phase"] == "REVIEW"


def test_lost_secret_response_requires_read_and_explicit_continue_without_rewriting_secret(setup):
    _, cloud = enable(setup)
    cloud.lose = "secret"
    value = setup[0].post("/api/admin/mcp/credentials", json=BODY).json()
    assert value["phase"] == "NEEDS_RECONCILIATION"
    assert BODY["secret"] not in json.dumps(value)
    path = "/api/admin/mcp/credentials/" + BODY["idempotency_key"]
    current = setup[0].get(path).json()
    assert current["phase"] == "CONTINUE"
    assert cloud.writes == ["secret"]
    cloud.lose = None
    assert setup[0].post(path + "/continue", json={}).json()["phase"] == "READY"
    assert cloud.writes == ["secret", "provider"]


def test_lost_provider_response_is_reconciled_without_another_post(setup):
    _, cloud = enable(setup)
    cloud.lose = "provider"
    assert setup[0].post("/api/admin/mcp/credentials", json=BODY).json()["phase"] == "NEEDS_RECONCILIATION"
    assert setup[0].get("/api/admin/mcp/credentials/" + BODY["idempotency_key"]).json()["phase"] == "READY"
    assert cloud.writes == ["secret", "provider"]


def test_explicit_provider_retry_is_fenced_and_keeps_the_original_secret(setup):
    _, cloud = enable(setup)
    cloud.lose = "provider"
    value = setup[0].post("/api/admin/mcp/credentials", json=BODY).json()
    cloud.providers.clear()  # confirmed rejection before resource creation
    path = "/api/admin/mcp/credentials/" + BODY["idempotency_key"]
    state = setup[0].get(path).json()
    assert state["retry_available"] is True
    cloud.lose = None
    result = setup[0].post(path + "/retry", json={"expected_attempt": 0})
    assert result.json()["phase"] == "READY"
    assert setup[0].post(path + "/retry", json={"expected_attempt": 0}).json()["phase"] == "READY"
    assert cloud.writes == ["secret", "provider", "provider"]
    assert BODY["secret"] not in json.dumps(value)


def test_provider_retry_cannot_write_when_secret_identity_cannot_be_verified(setup):
    _, cloud = enable(setup)
    cloud.lose = "provider"
    setup[0].post("/api/admin/mcp/credentials", json=BODY)
    cloud.providers.clear()
    cloud.read_secret = lambda state: (_ for _ in ()).throw(ValueError("binding changed"))
    path = "/api/admin/mcp/credentials/" + BODY["idempotency_key"]
    assert setup[0].get(path).json()["retry_available"] is False
    assert setup[0].post(path + "/retry", json={"expected_attempt": 0}).status_code == 409
    assert cloud.writes == ["secret", "provider"]


def test_provider_failure_returns_only_iam_identifiers_not_error_prose(setup):
    from botocore.exceptions import ClientError
    _, cloud = enable(setup)
    def reject(state):
        raise ClientError({"Error": {"Code": "AccessDeniedException",
            "Message": "not authorized to perform bedrock-agentcore:CreateApiKeyCredentialProvider on "
                       "arn:aws:bedrock-agentcore:us-west-2:123456789012:token-vault/default " + BODY["secret"]},
            "ResponseMetadata": {"RequestId": "test-request"}}, "CreateApiKeyCredentialProvider")
    cloud.create_provider = reject
    value = setup[0].post("/api/admin/mcp/credentials", json=BODY).json()
    assert value["failure_context"]["actions"] == ["bedrock-agentcore:CreateApiKeyCredentialProvider"]
    assert BODY["secret"] not in json.dumps(value)


def test_provider_retries_remain_bounded_and_never_erase_previous_failures(setup):
    _, cloud = enable(setup)
    def reject(state):
        cloud.writes.append("provider")
        raise TimeoutError("not accepted")
    cloud.create_provider = reject
    setup[0].post("/api/admin/mcp/credentials", json=BODY)
    path = "/api/admin/mcp/credentials/" + BODY["idempotency_key"]
    for attempt in range(8):
        result = setup[0].post(path + "/retry", json={"expected_attempt": attempt})
        assert result.status_code == 200
        assert result.json()["retry_count"] == attempt + 1
    assert result.json()["retry_available"] is False
    assert setup[0].post(path + "/retry", json={"expected_attempt": 7}).json()["retry_count"] == 8
    assert cloud.writes == ["secret"] + ["provider"] * 9


def test_auth_input_errors_and_business_access_never_expose_secret_or_dispatch(setup):
    from tests.conftest import login
    _, cloud = enable(setup)
    for patch in ({"endpoint": "http://localhost/mcp"}, {"header": "Host"}, {"secret": {"invalid": BODY["secret"]}},
                  {"prefix": "Bearer\r\nInjected: value"}, {"header": BODY["secret"] + "\n"}):
        response = setup[0].post("/api/admin/mcp/credentials", json={**BODY, **patch})
        assert response.status_code == 422, response.text
        assert BODY["secret"] not in response.text
    login(setup[0])
    assert setup[0].post("/api/admin/mcp/credentials", json=BODY).status_code == 403
    assert cloud.writes == []


def test_adding_an_unrelated_credential_does_not_invalidate_active_onboarding(setup):
    enable(setup)
    created = start(setup)
    assert setup[0].post("/api/admin/mcp/credentials", json=BODY).status_code == 200
    drain(setup[2], created["job_id"])
    assert detail(setup, created)["phase"] == "REVIEW"


def test_cloud_uses_external_deployment_secret_and_checks_native_binding():
    import boto3
    from botocore.stub import Stubber
    from backend.mcp_credentials import CredentialCloud
    cloud = CredentialCloud({"region": "us-west-2", "account": "123456789012"},
        session=boto3.Session(aws_access_key_id="test", aws_secret_access_key="test", region_name="us-west-2"))
    sid = "a" * 32
    state = {"id": sid, "deployment_prefix": "test-studio", "secret_name": "test-studio/mcp/" + sid,
             "provider_name": "test-studio-mcp-" + sid}
    arn = "arn:aws:secretsmanager:us-west-2:123456789012:secret:" + state["secret_name"] + "-Ab1234"
    provider = "arn:aws:bedrock-agentcore:us-west-2:123456789012:token-vault/default/apikeycredentialprovider/" + state["provider_name"]
    tags = cloud.tags(state)
    with Stubber(cloud.secrets) as secrets, Stubber(cloud.control) as control:
        secrets.add_response("create_secret", {"ARN": arn, "Name": state["secret_name"], "VersionId": sid}, {
            "Name": state["secret_name"], "ClientRequestToken": sid, "SecretString": json.dumps({"credential": BODY["secret"]}),
            "Tags": [{"Key": k, "Value": v} for k, v in tags.items()]})
        secrets.add_response("describe_secret", {"ARN": arn, "Name": state["secret_name"],
            "Tags": [{"Key": k, "Value": v} for k, v in tags.items()], "VersionIdsToStages": {sid: ["AWSCURRENT"]}},
            {"SecretId": state["secret_name"]})
        cloud.create_secret(state, BODY["secret"])
        state["secret_arn"] = cloud.read_secret(state)
        native = {"name": state["provider_name"], "credentialProviderArn": provider,
                  "apiKeySecretArn": {"secretArn": arn}, "apiKeySecretJsonKey": "credential", "apiKeySecretSource": "EXTERNAL"}
        control.add_response("create_api_key_credential_provider", native, {"name": state["provider_name"],
            "apiKeySecretSource": "EXTERNAL", "apiKeySecretConfig": {"secretId": arn, "jsonKey": "credential"}, "tags": tags})
        control.add_response("get_api_key_credential_provider", {**native, "createdTime": 0, "lastUpdatedTime": 0},
                             {"name": state["provider_name"]})
        control.add_response("list_tags_for_resource", {"tags": tags}, {"resourceArn": provider})
        cloud.create_provider(state)
        assert cloud.read_provider(state) == provider
        secrets.assert_no_pending_responses()
        control.assert_no_pending_responses()
