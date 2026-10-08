"""Fresh installations require no provider account, endpoint, or credential."""
import copy
import json
from types import SimpleNamespace

import pytest

from backend.foundation_runs import get, put
from backend.store import Store
from infra.journey import template
from infra.resource_tags import validate_resource_tags
from scripts import mcp_onboarding_deploy as onboarding


REGISTRY = {"registryId": "test-registry",
            "registryArn": "arn:aws:agent-registry:us-west-2:123456789012:registry/test-registry"}
PLATFORM = {"enabled": True, "account": "123456789012", "region": "us-west-2",
            "gateway_id": "gab-journey-tools-test"}


def release(monkeypatch, tmp_path, config=None):
    store = Store(str(tmp_path / "platform.sqlite"))
    settings = copy.deepcopy(PLATFORM)
    if config is not None:
        settings["mcp_onboarding"] = config
    with store.tx() as db:
        put(db, "journey-platform", settings)
        if config is not None:
            put(db, "mcp-onboarding", config)
    monkeypatch.setattr(onboarding, "DynamoStore", lambda *args: store)
    value = onboarding.Release.__new__(onboarding.Release)
    value.state = {"journeyPlatform": settings, "mcpOnboardingRegistry": REGISTRY,
                   "app": {"outputs": {"StateTable": "test-state"}}}
    value.target = SimpleNamespace(session=SimpleNamespace(resource=lambda _: None),
                                   save=lambda k, v: value.state.update({k: v}))
    return value, store


def test_first_install_configures_empty_generic_onboarding(monkeypatch, tmp_path):
    value, store = release(monkeypatch, tmp_path)
    value.configure()
    config = value.state["journeyPlatform"]["mcp_onboarding"]
    assert config["connections"] == []
    assert config["secret_arns"] == []
    assert config["credential_prefix"] == "governed-agent-builder-serverless"
    assert config["registry_arn"] == REGISTRY["registryArn"]
    with store.tx() as db:
        assert get(db, "mcp-onboarding") == config
    from backend.mcp_onboarding import configuration
    assert configuration(config, PLATFORM) == config


def test_configure_preserves_operator_connections_on_resume(monkeypatch, tmp_path):
    from tests.test_mcp_onboarding import CONFIG
    config = {**copy.deepcopy(CONFIG), "registry_id": REGISTRY["registryId"],
              "registry_arn": REGISTRY["registryArn"], "credential_prefix": "operator-studio"}
    value, store = release(monkeypatch, tmp_path, config)
    value.configure()
    with store.tx() as db:
        assert get(db, "mcp-onboarding") == config
    assert value.state["journeyPlatform"]["mcp_onboarding"] == config


def test_configure_rejects_unreconciled_live_changes(monkeypatch, tmp_path):
    value, store = release(monkeypatch, tmp_path)
    with store.tx() as db:
        put(db, "mcp-onboarding", {"operator_change": True})
    with pytest.raises(ValueError):
        value.configure()
    with store.tx() as db:
        assert get(db, "mcp-onboarding") == {"operator_change": True}


def test_default_integration_has_no_sample_tool_or_provider_secret():
    body = template()
    resources = body["Resources"]
    validate_resource_tags(resources)
    assert not any(r["Type"] in ("AWS::Lambda::Function", "AWS::SecretsManager::Secret")
                   for r in resources.values())
    assert "KnowledgeFunction" not in body["Outputs"]
    text = json.dumps(body)
    assert "secretsmanager:GetSecretValue" not in text
    assert "GetResourceApiKey" not in text
    assert "snowflake" not in text.lower() and "tavily" not in text.lower()


def test_fresh_account_runtime_prerequisites_are_retained_service_managed_roles():
    from infra.journey import runtime_prerequisites_template

    resources = runtime_prerequisites_template()["Resources"]
    assert {r["Properties"]["AWSServiceName"] for r in resources.values()} == {
        "runtime-identity.bedrock-agentcore.amazonaws.com",
        "runtime-instances.bedrock-agentcore.amazonaws.com",
    }
    for resource in resources.values():
        assert resource["Type"] == "AWS::IAM::ServiceLinkedRole"
        assert resource["DeletionPolicy"] == "Retain"
        assert resource["UpdateReplacePolicy"] == "Retain"
        assert "Policies" not in resource["Properties"]


def test_legacy_integration_keeps_explicit_bindings():
    body = template("operator-provider", "operator-secret")
    assert "KnowledgeFunction" in body["Outputs"]
    assert "operator-secret" in json.dumps(body["Resources"]["GatewayRole"])


@pytest.mark.parametrize("provider,secret", [("provider", None), (None, "secret")])
def test_incomplete_legacy_credential_fails_closed(provider, secret):
    with pytest.raises(ValueError):
        template(provider, secret)


def test_initial_catalog_has_no_model_or_remote_tool_binding(tmp_path):
    from scripts.platform_install import starter_catalog, publish_catalog
    from backend.journey_catalog import records, templates as stored_templates
    items, templates = starter_catalog()
    assert {item["kind"] for item in items} == {"skill"}
    assert all(t["tools"] == [] and not t["requires_tool"] and not t["sample_dataset"] for t in templates)
    store = Store(str(tmp_path / "initial.sqlite"))
    with store.tx() as db:
        db.execute("CREATE TABLE principals(id TEXT PRIMARY KEY, body TEXT, expires REAL)")
        publish_catalog(db, PLATFORM, items, templates)
        publish_catalog(db, PLATFORM, items, templates)
        assert {r["kind"] for r in records(db)} == {"skill"}
        assert all(t["tools"] == [] for t in stored_templates(db))
        # A later administrator change cannot be overwritten by bootstrap.
        edited = {**items[0], "version": "2", "name": "Operator instructions"}
        db.insert("components", {"id": edited["id"], "body": json.dumps(edited)}, upsert=True)
        with pytest.raises(RuntimeError, match="refusing bootstrap overwrite"):
            publish_catalog(db, PLATFORM, items, templates)


def test_prepare_of_existing_install_does_not_publish_or_call_provider(monkeypatch, tmp_path):
    from scripts import platform_install as install
    value, store = release(monkeypatch, tmp_path)
    monkeypatch.setattr(install, "DynamoStore", lambda *args: store)
    value.target.state = value.state
    # No client(), upload capability or provider config: a resume must only read.
    install.prepare(value.target)


@pytest.mark.parametrize("memory", [True, 256, 10241, "512"])
def test_worker_memory_is_validated_before_any_installation_write(memory):
    from scripts.platform_install import prepare
    with pytest.raises(ValueError, match="memory"):
        prepare(SimpleNamespace(), worker_memory_size=memory)


def test_explicit_worker_memory_cannot_silently_change_an_installed_platform(monkeypatch, tmp_path):
    from scripts import platform_install as install
    value, store = release(monkeypatch, tmp_path)
    monkeypatch.setattr(install, "DynamoStore", lambda *args: store)
    value.target.state = value.state
    with pytest.raises(RuntimeError, match="memory"):
        install.prepare(value.target, worker_memory_size=512)
    with store.tx() as db:
        assert get(db, "journey-platform") == PLATFORM


@pytest.mark.parametrize("lost_ack", [False, True])
def test_empty_gateway_creation_and_reconnect_never_create_credentials_or_targets(lost_ack):
    from scripts.bootstrap_support import gateway, TAGS
    from tests.bootstrap_support import Target, Control, OUTPUTS, GATEWAY_IDENTITY
    target = Target()
    target.operations_key = "platformOperations"
    control = Control(target)
    if lost_ack:
        control.lose = {"CreateGateway", "TagResource"}
    first = gateway(target, control, OUTPUTS)
    assert gateway(target, control, OUTPUTS) == first
    assert [operation for operation, _ in control.calls] == ["CreateGateway", "TagResource"]
    assert control.provider is None and control.target_resource is None
    assert control.tags[GATEWAY_IDENTITY] == TAGS
    assert "snowflakeOperations" not in target.state


def test_audit_does_not_require_temporary_qa_accounts():
    from scripts.mcp_onboarding_audit import deployment_state
    state = {"journeyStack": {"id": "stack"}, "journeyGateway": {"id": "gw", "arn": "gw-arn"},
             "journeyGatewayIdentity": {"arn": "identity"}, "mcpOnboardingRegistry": REGISTRY,
             "releaseSha256": "digest"}
    normalized = deployment_state(state, existing=True)
    assert "qa-admin" not in normalized and "qa-business" not in normalized
    assert normalized["registry"] == REGISTRY
    state.update(journeyAdminQA={"parameterPrefix": "/test/admin"},
                 journeyQA={"parameterPrefix": "/test/business"})
    normalized = deployment_state(state, existing=True)
    assert normalized["qa-admin"] == state["journeyAdminQA"]
    assert normalized["qa-business"] == state["journeyQA"]


@pytest.mark.parametrize("worker_memory_size", [None, 512])
def test_fresh_prepare_publishes_generic_catalog_to_real_dynamo_boundary(monkeypatch, worker_memory_size):
    import boto3
    from moto import mock_aws
    from backend.dynamo_store import DynamoStore
    from backend.journey_catalog import records, templates
    from backend.mcp_onboarding import McpOnboarding
    from scripts import platform_install as install
    from tests.bootstrap_support import Target, Control, CloudFormation
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1",
                                  aws_access_key_id="testing", aws_secret_access_key="testing")
        resource.create_table(TableName="test-state", BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": key, "AttributeType": "S"} for key in ("pk", "sk")])
        store = DynamoStore("test-state", resource)
        store.initialize()
        target = Target()
        target.operations_key = "platformOperations"
        target.state["app"] = {"outputs": {"StateTable": "test-state"}}
        target.cf = CloudFormation(target)
        control = Control(target)
        control.get_evaluator = lambda **kwargs: {
            "evaluatorId": "Builtin.Correctness", "evaluatorArn": "arn:aws:bedrock-agentcore:::evaluator/Builtin.Correctness"}
        target.session = SimpleNamespace(resource=lambda _: resource, client=lambda _: control)
        artifact = {"bucket": "test-artifacts", "key": "test.zip", "version_id": "test-version", "sha256": "a" * 64}
        monkeypatch.setattr(install.journey, "upload", lambda _: ("test-artifacts", "test.zip", artifact))
        install.prepare(target, worker_memory_size=worker_memory_size)
        install.prepare(target, worker_memory_size=worker_memory_size)
        assert target.cf.body == template()
        assert len(target.cf.writes) == 1
        assert [op for op, _ in control.calls] == ["CreateGateway", "TagResource"]
        assert not any(key in target.state for key in ("journeyCredential", "snowflakeConfig", "journeyTargets"))
        assert target.state["journeyPlatform"]["admin_enabled"]
        assert target.state["journeyPlatform"].get("worker_memory_size") == worker_memory_size
        with store.tx() as db:
            assert {r["kind"] for r in records(db)} == {"skill"}
            assert all(t["tools"] == [] for t in templates(db))
        # Continue through the same configure action documented for an installer.
        target.state["mcpOnboardingRegistry"] = {
            **REGISTRY, "registryArn": REGISTRY["registryArn"].replace("us-west-2", "us-east-1")}
        release = onboarding.Release.__new__(onboarding.Release)
        release.target, release.state = target, target.state
        release.configure()
        service = McpOnboarding(store, target.state["journeyPlatform"], control)
        options = service.options({"role": "admin"})
        assert options["enabled"] and options["credential_setup"]
        assert options["connections"] == []
        # Both synthesized stacks accept this empty configuration with scoped IAM.
        from infra.serverless import template as app_template
        from infra.mcp_onboarding import configure_gateway
        body = app_template(journey=target.state["journeyPlatform"])
        validate_resource_tags(body["Resources"])
        configure_gateway(target.cf.body["Resources"], target.state["journeyPlatform"])
        assert "McpCredentialSetup" in json.dumps(body)
        assert "snowflake" not in json.dumps(body).lower()
