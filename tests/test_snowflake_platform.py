"""Snowflake deployment contracts: synthetic inputs, no AWS or credentials."""
import copy
import json
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError
from botocore.session import get_session
from botocore.validate import validate_parameters

from backend.catalog import PERSONAS
from backend.foundation_runs import get, put
from backend.journey_catalog import choices, records, resolve
from backend.store import Store
from foundation_harness.config import digest
from scripts import snowflake_platform as sf


BINDING = {"account": "123456789012", "region": "us-east-1", "profile": "test-only"}
SECRET = "arn:aws:secretsmanager:us-east-1:123456789012:secret:governed-agent-builder-serverless/snowflake-pat-Ab1234"
CONFIG = {
    "endpoint": "https://org-account.snowflakecomputing.com/api/v2/databases/DEMO/schemas/PUBLIC/mcp-servers/SALES",
    "secret_arn": SECRET,
    "model": {"id": "bedrock-claude", "name": "Test model", "model_id": "test.model",
              "provider": "Amazon Bedrock", "supports_temperature": False},
}
GATEWAY = {"id": "gab-journey-tools-test", "arn": "arn:aws:bedrock-agentcore:us-east-1:123456789012:gateway/gab-journey-tools-test",
           "url": "https://gab-journey-tools-test.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"}
GATEWAY_IDENTITY = "arn:aws:bedrock-agentcore:us-east-1:123456789012:workload-identity-directory/default/workload-identity/gab-journey-tools-test"
DESCRIPTORS = [
    {"name": "snowflake___sales", "description": "Query synthetic sales",
     "inputSchema": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}},
    {"name": "tavily___search", "description": "Unrelated", "inputSchema": {"type": "object"}},
]


class Target:
    def __init__(self):
        self.binding = copy.deepcopy(BINDING)
        self.state = {"target": self.binding}
        self.saves = []

    def save(self, key, value):
        self.state[key] = copy.deepcopy(value)
        self.saves.append(copy.deepcopy(self.state))


def test_config_accepts_exact_nonsecret_contract_without_mutating_input():
    config = copy.deepcopy(CONFIG)
    assert sf.validate_config(config, BINDING) == CONFIG
    assert config == CONFIG


def test_demo_endpoint_and_sales_sql_name_are_discovery_driven():
    config = copy.deepcopy(CONFIG)
    config["endpoint"] = (
        "https://org-account.snowflakecomputing.com/api/v2/databases/"
        "GAB_SNOWFLAKE_DEMO_20260925/schemas/DEMO/mcp-servers/SALES_MCP")
    sf.validate_config(config, BINDING)
    descriptor = {**DESCRIPTORS[0], "name": "snowflake___sales_sql"}
    items = sf.build_catalog(config, GATEWAY, {"snowflake": "target-test"}, [descriptor])
    tool = next(x for x in items if x["kind"] == "tool")
    assert tool["id"] == "snowflake-sales_sql"
    assert tool["binding"]["name"] == "snowflake___sales_sql"
    assert tool["binding"]["inputSchema"] == descriptor["inputSchema"]


def test_cortex_templates_select_only_the_tools_needed_for_each_capability():
    operations = ["sales_sql", "sales_analyst", "sales_search", "sales_agent", "discount_quote"]
    descriptors = [{**DESCRIPTORS[0], "name": "snowflake___" + name} for name in operations]
    items = sf.build_catalog(CONFIG, GATEWAY, {"snowflake": "target-test"}, descriptors)
    for item in items:
        if item["kind"] == "tool":
            assert item["binding"].get("response_adapter") == (
                "snowflake-cortex-agent" if item["id"] == "snowflake-sales_agent" else None)
    templates = {t["id"]: t for t in sf.catalog_templates(items)}
    assert templates["snowflake-analyst"]["tools"] == ["snowflake-sales_analyst", "snowflake-sales_sql"]
    assert templates["snowflake-search"]["tools"] == ["snowflake-sales_search"]
    assert templates["snowflake-agent"]["tools"] == ["snowflake-sales_agent"]
    assert templates["snowflake-function"]["tools"] == ["snowflake-discount_quote"]
    assert templates["snowflake-sql"]["tools"] == ["snowflake-sales_sql"]
    assert set(templates["snowflake-lab"]["tools"]) == {"snowflake-" + name for name in operations}
    for name, template in templates.items():
        if name.startswith("snowflake-"):
            assert template["requires_tool"] and template["approved"]
            assert template["sample_input"] and template["sample_dataset"]
            assert template["workspaces"] == sf.WORKSPACES


def test_cortex_templates_are_not_advertised_before_required_tools_are_discovered():
    items = sf.build_catalog(CONFIG, GATEWAY, {"snowflake": "target-test"},
                             [{**DESCRIPTORS[0], "name": "snowflake___sales_analyst"}])
    assert not any(t["id"].startswith("snowflake-") for t in sf.catalog_templates(items))


def test_general_sql_defaults_and_skill_leave_workflow_to_the_agent(tmp_path):
    operations = ["query_sql", "sales_sql", "sales_analyst", "sales_search", "sales_agent", "discount_quote"]
    descriptors = [{**DESCRIPTORS[0], "name": "snowflake___" + name} for name in operations]
    items = sf.build_catalog(CONFIG, GATEWAY, {"snowflake": "target-test"}, descriptors)
    server = next(x for x in items if x["kind"] == "mcp_server")
    assert server["default_tool_ids"] == ["snowflake-query_sql"]
    skill = next(x for x in items if x["id"] == "snowflake-data-access")
    assert skill["kind"] == "skill"
    assert skill["binding"]["type"] == "instructions"
    assert "INFORMATION_SCHEMA" in skill["binding"]["instructions"]
    assert "sales" not in skill["binding"]["instructions"].lower()
    templates = {t["id"]: t for t in sf.catalog_templates(items)}
    custom = templates["snowflake-custom"]
    assert custom["tools"] == ["snowflake-query_sql"]
    assert "snowflake-data-access" in custom["skills"]
    assert custom["sample_dataset"] == []
    assert "sales" not in custom["prompt"].lower()
    assert "support" not in custom["prompt"].lower()
    for name in ("research", "knowledge"):
        assert templates[name]["tools"] == ["snowflake-query_sql"]
        assert "snowflake-data-access" in templates[name]["skills"]
        assert "Snowflake" in templates[name]["description"]
    for name in ("analyst", "search", "agent", "sql", "function", "lab"):
        assert "example" in templates["snowflake-" + name]["name"].lower()
    # Exercise actual catalog storage, grants and the selected skill's binding.
    store = Store(str(tmp_path / "shared-snowflake.sqlite"))
    actor = PERSONAS["alex"]
    with store.tx() as db:
        db.execute("CREATE TABLE principals(id TEXT PRIMARY KEY, body TEXT, expires REAL)")
        db.insert("principals", {"id": actor["id"], "body": json.dumps(actor)})
        sf.publish_catalog(db, items, {"enabled": True})
    with store.tx() as db:
        visible = {x["id"]: x for x in choices(db, actor)}
        assert visible["snowflake-query_sql"]["granted"]
        assert visible["snowflake-data-access"]["granted"]
        stored = {r["id"]: json.loads(r["body"]) for r in db.select("foundations")}
        assert stored["snowflake-custom"]["tools"] == ["snowflake-query_sql"]
        assert next(x for x in records(db) if x["id"] == "snowflake-data-access")["binding"] == skill["binding"]


def test_general_starter_and_skill_require_discovered_general_sql():
    items = sf.build_catalog(CONFIG, GATEWAY, {"snowflake": "target-test"}, DESCRIPTORS)
    assert not any(x["id"] == "snowflake-data-access" for x in items)
    assert not any(t["id"] == "snowflake-custom" for t in sf.catalog_templates(items))


def test_cortex_template_is_retired_when_its_tool_disappears(tmp_path):
    store = Store(str(tmp_path / "cortex-retirement.sqlite"))
    descriptors = [{**DESCRIPTORS[0], "name": "snowflake___" + name}
                   for name in ("sales_sql", "sales_analyst", "sales_search", "sales_agent", "discount_quote")]
    with store.tx() as db:
        db.execute("CREATE TABLE principals(id TEXT PRIMARY KEY, body TEXT, expires REAL)")
        sf.publish_catalog(db, sf.build_catalog(CONFIG, GATEWAY, {"snowflake": "target-test"}, descriptors), {})
        sf.publish_catalog(db, sf.build_catalog(CONFIG, GATEWAY, {"snowflake": "target-test"}, descriptors[:1]), {})
        templates = {row["id"]: json.loads(row["body"]) for row in db.select("foundations")}
        assert templates["snowflake-agent"]["approved"] is False
        assert templates["snowflake-agent"]["version"] == "2"
        assert templates["snowflake-sql"]["approved"] is True


@pytest.mark.parametrize("endpoint", [
    "http://org.snowflakecomputing.com/api/v2/databases/DB/schemas/PUBLIC/mcp-servers/S",
    CONFIG["endpoint"] + "/", CONFIG["endpoint"] + "?", CONFIG["endpoint"] + "#",
    CONFIG["endpoint"] + "?token=synthetic", CONFIG["endpoint"] + "#fragment",
    CONFIG["endpoint"].replace("org-account.", "user:password@org-account."),
    CONFIG["endpoint"].replace(".com/", ".com:443/"),
    CONFIG["endpoint"].replace(".com/", ".com.evil.test/"),
    CONFIG["endpoint"].replace("/DEMO/", "/%44EMO/"),
    CONFIG["endpoint"].replace("/DEMO/", "/../"),
    CONFIG["endpoint"].replace("/DEMO/", '/"DEMO"/'),
    CONFIG["endpoint"] + "\n", " https://example.snowflakecomputing.com/mcp",
    CONFIG["endpoint"].replace("org-account.", ""),
])
def test_rejects_unsafe_or_noncanonical_endpoints(endpoint):
    with pytest.raises(ValueError, match="endpoint"):
        sf.validate_config({**CONFIG, "endpoint": endpoint}, BINDING)


@pytest.mark.parametrize("secret", [
    SECRET.replace("123456789012", "987654321098"),
    SECRET.replace("us-east-1", "us-west-2"),
    SECRET.replace("snowflake-pat", "tavily-pat"),
    SECRET.replace("governed-agent-builder-serverless/", "another-project/"),
    SECRET + "\n", SECRET + ":jsonKey", "synthetic-pat-value",
])
def test_rejects_unbound_secret(secret):
    with pytest.raises(ValueError, match="secret"):
        sf.validate_config({**CONFIG, "secret_arn": secret}, BINDING)


@pytest.mark.parametrize("change", [
    {"api_key": "synthetic-do-not-publish"}, {"model": {**CONFIG["model"], "supports_temperature": "false"}},
    {"model": {**CONFIG["model"], "apiKey": "synthetic-do-not-publish"}},
    {"model": {**CONFIG["model"], "id": "mcp-snowflake"}},
])
def test_rejects_extra_credentials_and_invalid_model(change):
    with pytest.raises(ValueError):
        sf.validate_config({**CONFIG, **change}, BINDING)


def test_catalog_uses_only_discovered_snowflake_schema_and_no_auth_metadata():
    items = sf.build_catalog(CONFIG, GATEWAY, {"snowflake": "target-test"}, DESCRIPTORS)
    tool = next(x for x in items if x["kind"] == "tool")
    server = next(x for x in items if x["kind"] == "mcp_server")
    assert tool["binding"]["name"] == DESCRIPTORS[0]["name"]
    assert tool["binding"]["inputSchema"] == DESCRIPTORS[0]["inputSchema"]
    assert tool["binding"]["schema_digest"] == digest(DESCRIPTORS[0]["inputSchema"])
    assert tool["parent_id"] == server["id"] == "mcp-snowflake"
    assert server["default_tool_ids"] == [tool["id"]]
    assert all(x["fixture"] is False and x["binding_digest"] == digest(x["binding"]) for x in items)
    assert all(x["default_grant_workspaces"] == ["research", "operations"] for x in items)
    raw = json.dumps(items)
    assert SECRET not in raw and CONFIG["endpoint"] not in raw
    assert "Authorization" not in raw and "tavily" not in raw
    assert sf.build_catalog(CONFIG, GATEWAY, {"snowflake": "target-test"}, list(reversed(DESCRIPTORS))) == items


@pytest.mark.parametrize("descriptors", [
    [], DESCRIPTORS[1:], [DESCRIPTORS[0], DESCRIPTORS[0]],
    [{"name": "snowflake___sales", "description": "Missing schema"}],
    [{**DESCRIPTORS[0], "inputSchema": {"type": "string"}}],
    [{**DESCRIPTORS[0], "inputSchema": {"type": "object", "properties": []}}],
    [{**DESCRIPTORS[0], "name": "snowflake___"}],
])
def test_no_fake_or_ambiguous_tool_schemas(descriptors):
    with pytest.raises(ValueError):
        sf.build_catalog(CONFIG, GATEWAY, {"snowflake": "target-test"}, descriptors)


def test_catalog_versions_grants_and_resolution_use_real_repository(tmp_path):
    store = Store(str(tmp_path / "catalog.sqlite"))
    items = sf.build_catalog(CONFIG, GATEWAY, {"snowflake": "target-test"}, DESCRIPTORS)
    actor = PERSONAS["alex"]
    with store.tx() as db:
        db.execute("CREATE TABLE principals(id TEXT PRIMARY KEY, body TEXT, expires REAL)")
        db.insert("principals", {"id": actor["id"], "body": json.dumps(actor)})
        put(db, "journey-grants-migrated", True)  # Old migration must not suppress new Snowflake grants.
        sf.publish_catalog(db, items, {"enabled": True})
    with store.tx() as db:
        visible = choices(db, actor)
        assert all(next(x for x in visible if x["id"] == item["id"])["granted"] for item in items)
        selected = {kind: [x["id"] for x in items if x["kind"] == kind]
                    for kind in ("model", "mcp_server", "tool", "skill")}
        resolved = resolve(db, actor, selected, {x["id"]: "1" for x in items})
        assert len(resolved) == len(items)
        sf.publish_catalog(db, items, {"enabled": True})
        assert all(x["version"] == "1" for x in records(db))
        assert get(db, "journey-platform") == {"enabled": True}
    updated = copy.deepcopy(DESCRIPTORS)
    updated[0]["inputSchema"]["properties"]["limit"] = {"type": "integer"}
    with store.tx() as db:
        sf.publish_catalog(db, sf.build_catalog(CONFIG, GATEWAY, {"snowflake": "target-test"}, updated), {"enabled": True})
        assert next(x for x in records(db) if x["kind"] == "tool")["version"] == "2"


def test_journal_records_intent_before_write_and_never_replays_unknown():
    target = Target()
    journal = sf.Journal(target)
    writes = []
    def write(token):
        assert target.state["snowflakeOperations"]["create"]["request_token"] == token
        writes.append(token)
        raise TimeoutError("synthetic sensitive error must not persist")
    for _ in range(2):
        with pytest.raises(sf.PendingOperation):
            journal.run("create", {"name": "owned"}, write, lambda record: None)
    assert len(writes) == 1
    assert "sensitive" not in json.dumps(target.state)
    result = journal.run("create", {"name": "owned"}, write, lambda record: {"id": "recovered"})
    assert result == {"id": "recovered"} and len(writes) == 1
    with pytest.raises(RuntimeError, match="changed"):
        journal.run("create", {"name": "different"}, write, lambda record: None)


def test_paid_probe_is_once_and_has_no_output_in_receipt():
    target = Target()
    calls = []
    def converse(**request):
        assert target.state["snowflakeOperations"]["model-probe"]["status"] == "INTENT"
        calls.append(request)
        return {"output": {"message": {"content": [{"text": "READY"}]}},
                "ResponseMetadata": {"RequestId": "synthetic-request"}}
    client = SimpleNamespace(converse=converse)
    first = sf.probe_model(target, client, CONFIG["model"])
    assert sf.probe_model(target, client, CONFIG["model"]) == first
    assert len(calls) == 1
    assert calls[0]["inferenceConfig"] == {"maxTokens": 64}
    assert first["request_id"] == "synthetic-request"
    assert "READY" not in json.dumps(first)


def test_paid_probe_uncertain_acceptance_cannot_be_repeated():
    target = Target()
    calls = []
    def converse(**request):
        calls.append(request)
        raise TimeoutError("synthetic")
    for _ in range(2):
        with pytest.raises(sf.PendingOperation):
            sf.probe_model(target, SimpleNamespace(converse=converse), CONFIG["model"])
    assert len(calls) == 1


class Control:
    """Stateful service boundary, including lost acknowledgements after acceptance."""
    def __init__(self, target):
        self.target = target
        self.provider = self.gw = self.target_resource = None
        self.calls = []
        self.lose = set()
        self.tags = {}
        self.model = get_session().get_service_model("bedrock-agentcore-control")

    def dispatch(self, operation, request, journal_name):
        validate_parameters(request, self.model.operation_model(operation).input_shape)
        record = self.target.state["snowflakeOperations"][journal_name]
        assert record["status"] == "INTENT"
        if "clientToken" in request:
            assert request["clientToken"] == record["request_token"]
        self.calls.append((operation, copy.deepcopy(request)))

    def ack(self, operation, result):
        if operation in self.lose:
            raise TimeoutError("lost acknowledgement")
        return copy.deepcopy(result)

    def get_api_key_credential_provider(self, **request):
        assert request == {"name": sf.PROVIDER}
        if self.provider is None:
            raise ClientError({"Error": {"Code": "ResourceNotFoundException"}}, "GetApiKeyCredentialProvider")
        return copy.deepcopy(self.provider)

    def create_api_key_credential_provider(self, **request):
        self.dispatch("CreateApiKeyCredentialProvider", request, "credential")
        arn = f"arn:aws:bedrock-agentcore:{BINDING['region']}:{BINDING['account']}:token-vault/default/apikeycredentialprovider/{sf.PROVIDER}"
        self.provider = {"name": sf.PROVIDER, "credentialProviderArn": arn,
                         "apiKeySecretSource": "EXTERNAL", "apiKeySecretArn": {"secretArn": SECRET},
                         "apiKeySecretJsonKey": "pat"}
        self.tags[arn] = copy.deepcopy(request["tags"])
        return self.ack("CreateApiKeyCredentialProvider", self.provider)

    def list_tags_for_resource(self, resourceArn):
        return {"tags": copy.deepcopy(self.tags[resourceArn])}

    def list_gateways(self, **request):
        return {"items": [self.gw] if self.gw else []}

    def create_gateway(self, **request):
        self.dispatch("CreateGateway", request, "gateway")
        self.gw = {**request, "gatewayArn": GATEWAY["arn"], "gatewayId": GATEWAY["id"],
                   "gatewayUrl": GATEWAY["url"], "status": "READY",
                   "workloadIdentityDetails": {"workloadIdentityArn": GATEWAY_IDENTITY}}
        self.tags[GATEWAY["arn"]] = copy.deepcopy(request["tags"])
        self.tags[GATEWAY_IDENTITY] = {}
        return self.ack("CreateGateway", self.gw)

    def tag_resource(self, **request):
        self.dispatch("TagResource", request, "gateway-identity-tags")
        self.tags[request["resourceArn"]].update(request["tags"])
        return self.ack("TagResource", {})

    def get_gateway(self, gatewayIdentifier):
        assert gatewayIdentifier == GATEWAY["id"]
        return copy.deepcopy(self.gw)

    def list_gateway_targets(self, **request):
        assert request["gatewayIdentifier"] == GATEWAY["id"]
        return {"items": [self.target_resource] if self.target_resource else []}

    def create_gateway_target(self, **request):
        self.dispatch("CreateGatewayTarget", request, "target")
        self.target_resource = {**request, "targetId": "target-test", "gatewayArn": GATEWAY["arn"], "status": "READY"}
        return self.ack("CreateGatewayTarget", self.target_resource)

    def get_gateway_target(self, gatewayIdentifier, targetId):
        assert gatewayIdentifier == GATEWAY["id"] and targetId == "target-test"
        return copy.deepcopy(self.target_resource)


OUTPUTS = {"GatewayRole": "arn:aws:iam::123456789012:role/gateway-test"}


@pytest.mark.parametrize("lost", [False, True])
def test_external_provider_and_gateway_sdk_contracts_reconcile_without_replay(lost):
    target = Target()
    control = Control(target)
    if lost:
        control.lose = {"CreateApiKeyCredentialProvider", "CreateGateway", "CreateGatewayTarget", "TagResource"}
    provider = sf.credential(target, control, CONFIG)
    gw, targets = sf.gateway(target, control, OUTPUTS, provider, CONFIG)
    assert gw == GATEWAY and targets == {"snowflake": "target-test"}
    assert sf.credential(target, control, CONFIG) == provider
    assert sf.gateway(target, control, OUTPUTS, provider, CONFIG) == (gw, targets)
    assert len(control.calls) == 4
    assert control.tags[GATEWAY_IDENTITY] == sf.TAGS
    request = control.calls[0][1]
    assert request["apiKeySecretSource"] == "EXTERNAL"
    assert request["apiKeySecretConfig"] == {"secretId": SECRET, "jsonKey": "pat"}
    assert "apiKey" not in request
    assert all(request["tags"][key] == value for key, value in sf.TAGS.items())
    assert control.calls[1][1]["tags"] == sf.TAGS
    auth = control.calls[2][1]["credentialProviderConfigurations"][0]
    assert auth == {"credentialProviderType": "API_KEY", "credentialProvider": {
        "apiKeyCredentialProvider": {"providerArn": provider["provider_arn"], "credentialParameterName": "Authorization",
                                     "credentialPrefix": "Bearer", "credentialLocation": "HEADER"}}}


def test_existing_snowflake_operator_preserves_additional_studio_targets():
    target = Target()
    control = Control(target)
    provider = sf.credential(target, control, CONFIG)
    expected = sf.gateway(target, control, OUTPUTS, provider, CONFIG)
    other = {"targetId": "studio-target", "name": "studio-mcp-0123456789ab"}
    control.list_gateway_targets = lambda **request: {"items": [other, control.target_resource]}
    assert sf.gateway(target, control, OUTPUTS, provider, CONFIG) == expected
    assert len(control.calls) == 4
    assert other == {"targetId": "studio-target", "name": "studio-mcp-0123456789ab"}


@pytest.mark.parametrize("drift", ["provider", "secret", "source", "tags", "role", "auth", "endpoint", "target-auth",
                                 "identity", "identity-owner"])
def test_saved_resource_drift_rejected_without_write(drift):
    target = Target()
    control = Control(target)
    provider = sf.credential(target, control, CONFIG)
    sf.gateway(target, control, OUTPUTS, provider, CONFIG)
    if drift == "provider": control.provider["credentialProviderArn"] += "-changed"
    if drift == "secret": control.provider["apiKeySecretArn"]["secretArn"] = SECRET.replace("pat", "another")
    if drift == "source": control.provider["apiKeySecretSource"] = "INTERNAL"
    if drift == "tags": control.tags[provider["provider_arn"]].pop("auto-delete")
    if drift == "role": control.gw["roleArn"] += "-different"
    if drift == "auth": control.gw["authorizerType"] = "NONE"
    if drift == "endpoint": control.target_resource["targetConfiguration"]["mcp"]["mcpServer"]["endpoint"] += "/"
    if drift == "target-auth": control.target_resource["credentialProviderConfigurations"] = []
    if drift == "identity": control.gw["workloadIdentityDetails"]["workloadIdentityArn"] += "-another"
    if drift == "identity-owner": control.tags[GATEWAY_IDENTITY]["project"] = "another-project"
    with pytest.raises(RuntimeError):
        sf.credential(target, control, CONFIG)
        sf.gateway(target, control, OUTPUTS, provider, CONFIG)
    assert len(control.calls) == 4


def test_unrecorded_provider_cannot_be_adopted():
    target = Target()
    control = Control(target)
    sf.credential(target, control, CONFIG)
    target.state = {"target": BINDING}
    with pytest.raises(RuntimeError, match="unrecorded"):
        sf.credential(target, control, CONFIG)
    assert len(control.calls) == 1


@pytest.mark.parametrize("json_key", [None, "", "other"])
def test_saved_provider_requires_exact_pat_json_key(json_key):
    target = Target()
    control = Control(target)
    arn = f"arn:aws:bedrock-agentcore:{BINDING['region']}:{BINDING['account']}:token-vault/default/apikeycredentialprovider/{sf.PROVIDER}"
    target.state["journeyCredential"] = {"name": sf.PROVIDER, "provider_arn": arn, "secret_arn": SECRET}
    control.provider = {"name": sf.PROVIDER, "credentialProviderArn": arn,
                        "apiKeySecretSource": "EXTERNAL", "apiKeySecretArn": {"secretArn": SECRET}}
    if json_key is not None:
        control.provider["apiKeySecretJsonKey"] = json_key
    control.tags[arn] = copy.deepcopy(sf.TAGS)
    with pytest.raises(RuntimeError, match="binding changed"):
        sf.credential(target, control, CONFIG)
    assert control.calls == []


def test_sdk_retry_policy_overrides_upload_helpers_retry_settings():
    calls = []
    raw = SimpleNamespace(client=lambda name, **kwargs: calls.append(kwargs) or SimpleNamespace(),
                          resource=lambda name, **kwargs: calls.append(kwargs) or SimpleNamespace())
    session = sf.NoRetrySession(raw, Target())
    from botocore.config import Config
    session.client("s3", config=Config(retries={"max_attempts": 9}, s3={"payload_signing_enabled": True}))
    session.resource("dynamodb")
    session.client("bedrock-agentcore-control")
    assert all(call["config"].retries["total_max_attempts"] == 1 for call in calls)
    assert calls[0]["config"].s3["payload_signing_enabled"] is True
    assert all(call["config"].parameter_validation is True for call in calls)


def test_local_validate_never_constructs_an_aws_session(tmp_path, monkeypatch, capsys):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"target": BINDING}))
    config = tmp_path / "config.json"
    config.write_text(json.dumps(CONFIG))
    def forbidden(*args, **kwargs):
        raise AssertionError("Local validation must not construct an AWS target")
    monkeypatch.setattr(sf, "SnowflakeTarget", forbidden)
    sf.main(["validate", "--expected-account", BINDING["account"], "--profile", BINDING["profile"],
             "--region", BINDING["region"], "--state", str(state), "--config", str(config)])
    assert "no AWS calls" in capsys.readouterr().out


def test_durable_state_preserves_other_agent_updates(tmp_path):
    target = sf.SnowflakeTarget.__new__(sf.SnowflakeTarget)
    target.path = tmp_path / "state.json"
    target.binding, target.state = BINDING, {"target": BINDING, "app": {"initial": True}}
    target.path.write_text(json.dumps({**target.state, "releaseSha256": "other-agent"}))
    target.save("snowflakeConfig", CONFIG)
    assert json.loads(target.path.read_text())["releaseSha256"] == "other-agent"
    assert target.path.stat().st_mode & 0o777 == 0o600
    changed = {**target.state, "snowflakeConfig": {"concurrent": True}}
    target.path.write_text(json.dumps(changed))
    with pytest.raises(RuntimeError, match="Concurrent"):
        target.save("snowflakeConfig", CONFIG)
    assert json.loads(target.path.read_text()) == changed


def test_disappeared_tools_are_retired_and_templates_select_real_tools(tmp_path):
    store = Store(str(tmp_path / "catalog.sqlite"))
    descriptors = DESCRIPTORS + [{**DESCRIPTORS[0], "name": "snowflake___other"}]
    with store.tx() as db:
        db.execute("CREATE TABLE principals(id TEXT PRIMARY KEY, body TEXT, expires REAL)")
        sf.publish_catalog(db, sf.build_catalog(CONFIG, GATEWAY, {"snowflake": "target-test"}, descriptors), {})
        sf.publish_catalog(db, sf.build_catalog(CONFIG, GATEWAY, {"snowflake": "target-test"}, DESCRIPTORS), {})
        retired = next(x for x in records(db) if x["id"] == "snowflake-other")
        assert retired["approved"] is False and retired["execution_ready"] is False
        for row in db.select("foundations"):
            entry = json.loads(row["body"])
            if entry.get("catalog") == "journey":
                assert entry["tools"] == ["snowflake-sales"]
                assert not entry["sample_dataset"]  # No sample Aurora/web evaluation claims.


class S3:
    def __init__(self, data=None):
        self.data, self.head, self.writes = data, None, 0
        self.lose = False

    def head_object(self, **request):
        if self.head is None:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return copy.deepcopy(self.head)

    def get_object(self, **request):
        import io
        return {"Body": io.BytesIO(self.data)}

    def upload_file(self, filename, bucket, key, *, ExtraArgs, Config):
        self.writes += 1
        self.data = sf.Path(filename).read_bytes()
        self.head = {"ContentLength": len(self.data), "VersionId": "test-version",
                     "ServerSideEncryption": "AES256", "Metadata": ExtraArgs["Metadata"]}
        if self.lose:
            raise TimeoutError("lost upload acknowledgement")


def test_base_lambda_upload_is_reused_only_after_versioned_content_verification(tmp_path):
    source = tmp_path / "lambda.zip"
    source.write_bytes(b"synthetic-lambda")
    sha = sf.hashlib.sha256(source.read_bytes()).hexdigest()
    target, s3 = Target(), S3(source.read_bytes())
    target.state.update(releaseSha256=sha, artifacts={"outputs": {"Bucket": "test-artifacts"}})
    s3.head = {"ContentLength": source.stat().st_size, "VersionId": "base-version",
               "ServerSideEncryption": "AES256", "Metadata": {}}
    client = sf.UploadClient(s3, target)
    client.upload_file(str(source), "test-artifacts", f"releases/{sha}/lambda.zip",
                       ExtraArgs={"ServerSideEncryption": "AES256"}, Config=None)
    assert s3.writes == 0
    s3.data = b"tampered-release"
    with pytest.raises(RuntimeError):
        client.upload_file(str(source), "test-artifacts", f"releases/{sha}/lambda.zip",
                           ExtraArgs={"ServerSideEncryption": "AES256"}, Config=None)


@pytest.mark.parametrize("lost", [False, True])
def test_upload_reconciles_once_without_post_replay(tmp_path, lost):
    source = tmp_path / "runtime.zip"
    source.write_bytes(b"synthetic-runtime")
    target, s3 = Target(), S3()
    s3.lose = lost
    client = sf.UploadClient(s3, target)
    for _ in range(2):
        client.upload_file(str(source), "test-artifacts", "journey/foundation/hash.zip",
                           ExtraArgs={"ServerSideEncryption": "AES256"}, Config=None)
    assert s3.writes == 1
    assert next(iter(target.state["snowflakeOperations"].values()))["status"] == "COMPLETE"


class CloudFormation:
    def __init__(self, target):
        self.target, self.live, self.body, self.writes = target, None, None, []
        self.lose = False

    def describe_stacks(self, **request):
        if self.live is None:
            raise ClientError({"Error": {"Code": "ValidationError", "Message": "Stack does not exist"}}, "DescribeStacks")
        return {"Stacks": [copy.deepcopy(self.live)]}

    def get_template(self, **request):
        return {"TemplateBody": copy.deepcopy(self.body)}

    def validate_template(self, **request):
        return {}

    def create_stack(self, **request):
        assert request["ClientRequestToken"] == self.target.state["snowflakeOperations"]["stack"]["request_token"]
        self.writes.append(copy.deepcopy(request))
        self.body = json.loads(request["TemplateBody"])
        self.live = {"StackId": f"arn:aws:cloudformation:us-east-1:123456789012:stack/{sf.journey.STACK}/test",
                     "StackStatus": "CREATE_COMPLETE", "Tags": request["Tags"], "Parameters": request["Parameters"],
                     "Outputs": [{"OutputKey": k, "OutputValue": v} for k, v in {
                         **OUTPUTS, "RuntimeRole": "arn:aws:iam::123456789012:role/runtime-test",
                         "KnowledgeFunction": "arn:aws:lambda:us-east-1:123456789012:function:knowledge-test",
                         "EvidenceBucket": "test-evidence", "TraceLogGroup": "/test/traces"}.items()]}
        if self.lose:
            raise TimeoutError("lost stack acknowledgement")
        return {"StackId": self.live["StackId"]}


@pytest.mark.parametrize("lost", [False, True])
def test_shared_stack_template_and_request_token_recovery(lost):
    target = Target()
    control = Control(target)
    provider = sf.credential(target, control, CONFIG)
    target.cf = CloudFormation(target)
    target.cf.lose = lost
    result = sf.stack(target, provider, "test-artifacts", "test-key")
    assert sf.stack(target, provider, "test-artifacts", "test-key") == result
    assert len(target.cf.writes) == 1
    assert target.cf.body == sf.journey.platform_template(provider["provider_arn"], provider["secret_arn"])
    target.cf.body["Description"] = "different deployment"
    with pytest.raises(RuntimeError, match="contract changed"):
        sf.stack(target, provider, "test-artifacts", "test-key")
    assert len(target.cf.writes) == 1


@pytest.fixture
def publication_environment(monkeypatch):
    import boto3
    from moto import mock_aws
    from backend.dynamo_store import DynamoStore
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name=BINDING["region"],
                                  aws_access_key_id="testing", aws_secret_access_key="testing")
        resource.create_table(
            TableName="test-state", BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": key, "AttributeType": "S"} for key in ("pk", "sk")])
        store = DynamoStore("test-state", resource)
        store.initialize()
        with store.tx() as db:
            for persona in ("alex", "sam"):
                actor = PERSONAS[persona]
                db.insert("principals", {"id": actor["id"], "body": json.dumps(actor)})
        target, control = Target(), Control(Target())
        target.state.update(snowflakeConfig=CONFIG, app={"outputs": {"StateTable": "test-state"}})
        calls = []
        def converse(**request):
            calls.append(request)
            return {"output": {"message": {"content": [{"text": "READY"}]}},
                    "ResponseMetadata": {"RequestId": "test-probe"}}
        target.session = SimpleNamespace(resource=lambda name: resource,
                                         client=lambda name: SimpleNamespace(converse=converse))
        descriptors = copy.deepcopy(DESCRIPTORS)
        class MCP:
            def __init__(self, session, url):
                assert url == GATEWAY["url"]
            def discover(self):
                return copy.deepcopy(descriptors)
            def call(self, *args):
                raise AssertionError("Deployment must never invoke tools")
        monkeypatch.setattr(sf, "GatewayMCP", MCP)
        control.get_evaluator = lambda **kwargs: {
            "evaluatorId": "Builtin.Correctness", "evaluatorArn": "arn:aws:bedrock-agentcore:::evaluator/Builtin.Correctness"}
        outputs = {**OUTPUTS, "RuntimeRole": "arn:aws:iam::123456789012:role/runtime-test",
                   "EvidenceBucket": "test-evidence", "TraceLogGroup": "/test/traces"}
        artifact = {"bucket": "test-artifacts", "key": "test.zip", "version_id": "test-version", "sha256": "a" * 64}
        yield target, control, outputs, artifact, resource, store, calls, descriptors


def test_publish_transaction_recovery_and_real_journey_authorization(publication_environment, monkeypatch):
    from backend.journey import Journey
    from tests.journey_support import OfflineCloud
    target, control, outputs, artifact, resource, store, probes, _ = publication_environment
    original = resource.meta.client.transact_write_items
    writes = []
    def lose_after_acceptance(**request):
        writes.append(request)
        original(**request)
        raise TimeoutError("lost catalog transaction acknowledgement")
    monkeypatch.setattr(resource.meta.client, "transact_write_items", lose_after_acceptance)
    for _ in range(2):
        sf.publish(target, control, outputs, artifact, GATEWAY, {"snowflake": "target-test"})
    assert len(writes) == len(probes) == 1
    monkeypatch.setattr(resource.meta.client, "transact_write_items", original)
    journey = Journey(store, target.state["journeyPlatform"], OfflineCloud())
    for persona in ("alex", "sam"):
        actor = PERSONAS[persona]
        options = journey.options(actor)
        assert all(x["granted"] for group in options["choices"].values() for x in group)
        definition = {
            "template_id": "knowledge", "model_id": CONFIG["model"]["id"], "mcp_servers": ["mcp-snowflake"],
            "tools": ["snowflake-sales"], "skills": ["concise", "citations"],
            "component_versions": {x["id"]: x["version"] for group in options["choices"].values() for x in group},
        }
        with store.tx() as db:
            journey.validate(db, actor, definition)
    assert target.state["journeyPlatform"]["foundation"] == sf.json_file(sf.ROOT / "examples/journey/foundation.json")


def test_uncertain_publication_blocks_changed_discovery(publication_environment, monkeypatch):
    target, control, outputs, artifact, resource, store, probes, descriptors = publication_environment
    writes = []
    def never_accepted(**request):
        writes.append(request)
        raise TimeoutError("unknown transaction acceptance")
    monkeypatch.setattr(resource.meta.client, "transact_write_items", never_accepted)
    with pytest.raises(sf.PendingOperation):
        sf.publish(target, control, outputs, artifact, GATEWAY, {"snowflake": "target-test"})
    descriptors[0]["description"] = "Changed discovery while prior publication unresolved"
    with pytest.raises(sf.PendingOperation):
        sf.publish(target, control, outputs, artifact, GATEWAY, {"snowflake": "target-test"})
    assert len(writes) == len(probes) == 1


def test_admin_catalog_drift_is_not_hidden_by_old_publication_receipt(publication_environment):
    target, control, outputs, artifact, resource, store, probes, _ = publication_environment
    sf.publish(target, control, outputs, artifact, GATEWAY, {"snowflake": "target-test"})
    with store.tx() as db:
        entry = next(x for x in records(db) if x["id"] == "snowflake-sales")
        entry.update(approved=False, version="2", managed_by="platform-admin")
        db.insert("components", {"id": entry["id"], "body": json.dumps(entry)}, upsert=True)
    with pytest.raises(RuntimeError, match="Catalog"):
        sf.publish(target, control, outputs, artifact, GATEWAY, {"snowflake": "target-test"})
    assert len(probes) == 1


def test_legacy_publication_preserves_studio_creation_configuration(publication_environment):
    target, control, outputs, artifact, _, _, _, _ = publication_environment
    creation = {"provisioning_secret_arns": ["configured-creator-secret"]}
    target.state["journeyPlatform"] = {"mcp_creation": creation}
    sf.publish(target, control, outputs, artifact, GATEWAY, {"snowflake": "target-test"})
    assert target.state["journeyPlatform"]["mcp_creation"] == creation
