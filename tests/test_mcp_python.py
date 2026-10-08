import copy
import hashlib
import io
import json
import zipfile
from types import SimpleNamespace

import pytest

from backend.foundation_runs import get, put
from tests.test_mcp_onboarding import setup


CONFIG = {
    "bundle_name": "Python 3.13, FastMCP and Snowflake",
    "artifact": {"bucket": "test-private-bucket", "key": "mcp/python/base.zip", "version_id": "base-v1", "digest": "a" * 64},
    "runtime_role": "arn:aws:iam::123456789012:role/test-python-runtime",
    "runtime_prefix": "test_python_mcp", "deployment_prefix": "test-studio",
    "facade_url": "https://python.example.com",
}
BODY = {"name": "Uploaded Snowflake", "filename": "snowflake.py",
    "source": 'from snowflake_mcp.server import create_server\n\nif __name__ == "__main__":\n    create_server().run(transport="streamable-http")\n',
    "snowflake_account": "org-account", "snowflake_role": "READER", "warehouse": "READ_WH",
    "idempotency_key": "upload-python-mcp-0001"}
TOOLS = [{"name": "test_connection", "description": "Check data access.", "inputSchema": {"type": "object", "properties": {}}}]


class Cloud:
    def __init__(self):
        self.native, self.writes, self.sources = {}, [], []
        self.lose = None

    def read(self, stage, state, config):
        return copy.deepcopy(self.native.get((state["id"], stage)))

    def write(self, stage, state, config, source=None):
        self.writes.append(stage)
        if stage == "package":
            self.sources.append(source)
        self.native[(state["id"], stage)] = {
            "package": {"artifact": {**CONFIG["artifact"], "key": "mcp/python/" + state["id"] + "/runtime.zip", "version_id": "package-v1"}},
            "runtime": {"runtime_id": state["runtime_name"] + "-abc123",
                "runtime_arn": "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/" + state["runtime_name"] + "-abc123",
                "runtime_version": "1"},
            "logs": {"log_group": "/aws/bedrock-agentcore/runtimes/test-DEFAULT"},
        }[stage]
        if self.lose == stage:
            raise TimeoutError("Synthetic unknown outcome")

    def discover(self, state, config):
        return copy.deepcopy(TOOLS)


def enable(setup):
    from backend.mcp_python import PythonMcp
    cloud = Cloud()
    setup[2].python = PythonMcp(setup[2], cloud)
    with setup[1].tx() as db:
        put(db, "mcp-python-config", copy.deepcopy(CONFIG))
    return setup[2].python, cloud


def create(setup, body=None):
    response = setup[0].post("/api/admin/mcp/python", json=body or BODY)
    assert response.status_code == 202, response.text
    return response.json()


def drain(setup, job):
    for _ in range(8):
        setup[0].app.state.step_job(job)


def test_python_upload_deploys_once_and_returns_real_schema_for_existing_gateway_review(setup):
    service, cloud = enable(setup)
    value = create(setup)
    assert create(setup)["id"] == value["id"]
    assert not cloud.writes  # The HTTP request never runs uploaded code or cloud mutations.
    assert setup[0].get("/api/admin/mcp/python-requests/" + BODY["idempotency_key"]).json()["id"] == value["id"]
    drain(setup, value["job_id"])
    result = setup[0].get("/api/admin/mcp/python/" + value["id"]).json()
    assert result["phase"] == "READY" and result["tools"] == TOOLS
    assert result["endpoint"] == CONFIG["facade_url"] + "/mcp/" + value["id"]
    assert result["source_digest"] == hashlib.sha256(BODY["source"].encode()).hexdigest()
    assert cloud.writes == ["package", "runtime", "logs"] and cloud.sources == [BODY["source"]]
    assert BODY["source"] not in json.dumps(result)
    assert setup[0].get("/api/admin/mcp/onboarding-options").json()["python_bundle"] == CONFIG["bundle_name"]
    assert setup[0].get("/api/admin/mcp/python").json()["items"] == [result]
    changed = setup[0].post("/api/admin/mcp/python", json={**BODY, "source": BODY["source"] + "# changed\n"})
    assert changed.status_code == 409


@pytest.mark.parametrize("stage", ["package", "runtime", "logs"])
def test_unknown_python_cloud_outcome_requires_reconciliation_without_duplicate_writes(setup, stage):
    _, cloud = enable(setup)
    cloud.lose = stage
    value = create(setup)
    drain(setup, value["job_id"])
    path = "/api/admin/mcp/python/" + value["id"]
    assert setup[0].get(path).json()["phase"] == "NEEDS_RECONCILIATION"
    cloud.lose = None
    response = setup[0].post(path + "/reconcile", json={})
    assert response.status_code == 202
    drain(setup, response.json()["job_id"])
    assert setup[0].get(path).json()["phase"] == "READY"
    assert cloud.writes == ["package", "runtime", "logs"]


def test_failed_native_mcp_initialization_exposes_stage_and_safe_code_without_redeploy(setup, monkeypatch):
    import boto3
    from botocore.stub import Stubber, ANY
    from backend.mcp_python_cloud import PythonCloud
    _, cloud = enable(setup)
    native = PythonCloud(setup[2].settings, boto3.Session(
        aws_access_key_id="test", aws_secret_access_key="test", region_name="us-west-2"))
    monkeypatch.setattr(native, "runtime", lambda state, config: {
        k: state[k] for k in ("runtime_id", "runtime_arn", "runtime_version")})
    monkeypatch.setattr(cloud, "discover", native.discover)
    value = create(setup)
    error_body = json.dumps({"jsonrpc": "2.0", "id": None,
        "error": {"code": -32603, "message": "private-startup-details-and-credentials"}}).encode()
    with Stubber(native.data) as data:
        data.add_response("invoke_agent_runtime", {
            "response": io.BytesIO(error_body), "statusCode": 200, "contentType": "application/json",
        }, {"agentRuntimeArn": ANY, "qualifier": "DEFAULT", "runtimeSessionId": "python-mcp-" + value["id"],
            "contentType": "application/json", "accept": "application/json, text/event-stream", "payload": ANY})
        drain(setup, value["job_id"])
        data.assert_no_pending_responses()
    result = setup[0].get("/api/admin/mcp/python/" + value["id"]).json()
    assert result["phase"] == "NEEDS_RECONCILIATION"
    assert result.get("stage") == "schema"
    assert result["failure_code"] == "MCP_DISCOVERY_FAILED"
    assert "private-startup-details" not in json.dumps(result)
    assert not result["retry_available"]
    assert cloud.writes == ["package", "runtime", "logs"]


def test_unknown_missing_runtime_is_not_recreated_until_explicit_fenced_retry(setup):
    _, cloud = enable(setup)
    cloud.lose = "runtime"
    value = create(setup)
    drain(setup, value["job_id"])
    del cloud.native[(value["id"], "runtime")]
    cloud.lose = None
    path = "/api/admin/mcp/python/" + value["id"]
    resume = setup[0].post(path + "/reconcile", json={}).json()
    drain(setup, resume["job_id"])
    assert cloud.writes == ["package", "runtime"]
    retry = setup[0].post(path + "/retry", json={"job_id": resume["job_id"]})
    assert retry.status_code == 202
    drain(setup, retry.json()["job_id"])
    assert setup[0].get(path).json()["phase"] == "READY"
    assert cloud.writes == ["package", "runtime", "runtime", "logs"]
    assert setup[0].post(path + "/retry", json={"job_id": resume["job_id"]}).status_code == 409


@pytest.mark.parametrize("patch", [
    {"filename": "../main.py"}, {"filename": "server.zip"}, {"source": "def broken("},
    {"source": "x" * 65537}, {"snowflake_account": "https://evil.example.com"},
    {"snowflake_role": "ACCOUNTADMIN"}, {"warehouse": "READ_WH; drop table X"},
])
def test_python_upload_rejects_invalid_source_or_unbounded_data_configuration_without_echo(setup, patch):
    _, cloud = enable(setup)
    response = setup[0].post("/api/admin/mcp/python", json={**BODY, **patch})
    assert response.status_code == 422
    assert BODY["source"] not in response.text
    assert not cloud.writes


def test_python_worker_stops_when_configuration_or_real_admin_authority_changes(setup):
    service, cloud = enable(setup)
    value = create(setup)
    with setup[1].tx() as db:
        put(db, "mcp-python-config", {**CONFIG, "runtime_role": CONFIG["runtime_role"] + "-other"})
    drain(setup, value["job_id"])
    assert not cloud.writes
    with setup[1].tx() as db:
        put(db, "mcp-python-config", CONFIG)
    service.service.hosted = True
    # A payload owner or profile is not a substitute for the actual job session.
    value = create(setup, {**BODY, "idempotency_key": "no-real-admin-session-0001"})
    drain(setup, value["job_id"])
    assert not cloud.writes


def test_business_user_cannot_upload_python(setup):
    from tests.conftest import login
    _, cloud = enable(setup)
    login(setup[0])
    assert setup[0].post("/api/admin/mcp/python", json=BODY).status_code == 403
    assert setup[0].get("/api/admin/mcp/python").status_code == 403
    assert not cloud.writes


def test_python_file_limit_counts_source_bytes_separately_from_json_envelope(setup):
    enable(setup)
    source = "#" + "x" * 65534 + "\n"
    assert len(source.encode()) == 65536
    assert create(setup, {**BODY, "source": source})["phase"] == "PACKAGING"


def test_worker_schedules_python_continuation_without_entering_legacy_provisioning(setup, monkeypatch):
    from backend import serverless
    enable(setup)
    value = create(setup)
    queue = []
    monkeypatch.setattr(serverless, "application", lambda **_: setup[0].app)
    monkeypatch.setattr(serverless.boto3, "client", lambda _: SimpleNamespace(send_message=lambda **kw: queue.append(kw)))
    monkeypatch.setenv("JOB_QUEUE_URL", "https://queue.example.test/jobs")
    result = serverless.worker_handler({"Records": [{"messageId": "delivery-python",
        "body": json.dumps({"job_id": value["job_id"]})}]},
        SimpleNamespace(get_remaining_time_in_millis=lambda: 300000))
    assert result == {"batchItemFailures": []}
    assert len(queue) == 1


def test_python_package_overlays_only_main_and_never_executes_upload(tmp_path):
    from backend.mcp_python_cloud import package
    base = tmp_path / "base.zip"
    with zipfile.ZipFile(base, "w") as archive:
        archive.writestr("main.py", "old")
        archive.writestr("mcp/__init__.py", "approved dependency")
    before = base.read_bytes()
    marker = tmp_path / "must-not-exist"
    source = f"open({str(marker)!r}, 'w').write('executed')\n"
    output = tmp_path / "runtime.zip"
    package(base, hashlib.sha256(before).hexdigest(), source, output)
    assert not marker.exists() and base.read_bytes() == before
    with zipfile.ZipFile(output) as archive:
        assert set(archive.namelist()) == {"main.py", "mcp/__init__.py"}
        assert archive.read("main.py") == source.encode()
        assert archive.read("mcp/__init__.py") == b"approved dependency"
    with pytest.raises(ValueError, match="digest"):
        package(base, "0" * 64, source, output)


@pytest.mark.parametrize("network", [None, {"networkMode": "VPC", "networkModeConfig": {
    "subnets": ["subnet-a", "subnet-b"], "securityGroups": ["sg-runtime"]}}])
def test_runtime_creation_pins_the_platform_network_receipt(setup, network):
    _, cloud = enable(setup)
    if network:
        setup[2].settings["network"] = network
    seen = []
    write = cloud.write
    cloud.write = lambda stage, state, config, source=None: (seen.append((stage, state.get("network"))), write(stage, state, config, source))[1]
    value = create(setup)
    drain(setup, value["job_id"])
    with setup[1].tx() as db:
        state = get(db, "mcp-python:" + value["id"])
    pinned = network or {"networkMode": "PUBLIC"}
    assert state["phase"] == "READY" and state["network"] == pinned
    assert ("runtime", pinned) in seen


def test_python_runtime_wait_keeps_one_hop_per_poll_as_documented_residual(setup, monkeypatch):
    # MCP Python DEPLOYING steps chain multiple 60s-capable reads, so they are
    # NOT drained in-process (no whole-step bound fits the drain budget): each
    # pending poll still costs one SQS self-requeue hop. This remains exposed
    # to Lambda's ~16-invocation recursion cap for slow Runtimes (documented
    # residual). Guarded operation records must still prevent duplicate writes.
    from backend import serverless

    service, cloud = enable(setup)
    value = create(setup)
    polls = {"pending": 0}
    original_read = cloud.read
    def read(stage, state, config):
        receipt = original_read(stage, state, config)
        if stage == "runtime" and receipt is not None and polls["pending"] < 3:
            polls["pending"] += 1
            return {"pending": True}
        return receipt
    cloud.read = read
    monkeypatch.setattr(serverless, "application", lambda **_: setup[0].app)
    messages = []
    monkeypatch.setattr(serverless.boto3, "client",
                        lambda _: SimpleNamespace(send_message=lambda **kw: messages.append(kw)))
    monkeypatch.setenv("JOB_QUEUE_URL", "synthetic-python-queue")
    monkeypatch.setattr(serverless.time, "sleep",
                        lambda seconds: pytest.fail("MCP DEPLOYING must yield to the queue, not drain"))
    event = {"Records": [{"messageId": "python-deploy", "body": json.dumps({"job_id": value["job_id"]})}]}
    context = SimpleNamespace(get_remaining_time_in_millis=lambda: 300000)
    path = "/api/admin/mcp/python/" + value["id"]
    deliveries = 0
    while setup[0].get(path).json()["phase"] != "READY":
        before = len(messages)
        assert serverless.worker_handler(event, context) == {"batchItemFailures": []}
        deliveries += 1
        assert deliveries < 30
        if setup[0].get(path).json()["phase"] != "READY":
            assert len(messages) == before + 1  # exactly one hop per delivery
    assert polls["pending"] == 3
    # Draining never replayed a guarded cloud write, and a duplicate delivery
    # of the finished job is a no-op.
    assert cloud.writes == ["package", "runtime", "logs"]
    before = len(messages)
    assert serverless.worker_handler(event, context) == {"batchItemFailures": []}
    assert len(messages) == before and cloud.writes == ["package", "runtime", "logs"]
