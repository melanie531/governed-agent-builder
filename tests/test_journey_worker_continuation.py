import time
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend import journey_lifecycle, serverless
from backend.catalog import PERSONAS
from backend.foundation_runs import get, put
from backend.journey_schema import AgentDefinition, DeleteAgent, DeletePreview, SaveAgent
from backend.store import Store
from infra.serverless import template
from tests.journey_support import definition, drain, make_journey


def test_all_functions_keep_recursion_protection(tmp_path):
    journey, _ = make_journey(Store(str(tmp_path / "state.sqlite")))
    settings = {**journey.settings, "evaluator_id": "Builtin.Correctness",
                "evaluator_arn": "arn:aws:bedrock-agentcore:::evaluator/Builtin.Correctness"}
    resources = template(journey=settings)["Resources"]
    for resource in resources.values():
        if resource["Type"] == "AWS::Lambda::Function":
            assert resource["Properties"].get("RecursiveLoop", "Terminate") == "Terminate"
    assert template()["Resources"]["Worker"]["Properties"].get("RecursiveLoop", "Terminate") == "Terminate"
    assert resources["WorkerMapping"]["Properties"]["ScalingConfig"]["MaximumConcurrency"] == 2
    assert resources["Jobs"]["Properties"]["MessageRetentionPeriod"] == 86400


def test_runtime_wait_stops_at_deadline_without_another_cloud_call(tmp_path):
    journey, cloud = make_journey(Store(str(tmp_path / "state.sqlite")))
    cloud.ready_result = False
    saved = journey.save(PERSONAS["alex"], SaveAgent(
        definition=AgentDefinition(**definition(journey)), idempotency_key=uuid4().hex, deploy=True))
    journey.step(saved["job_id"])
    with journey.store.tx() as db:
        job = get(db, "journey-job:" + saved["job_id"])
        job["deadline"] = time.time() - 1
        put(db, "journey-job:" + saved["job_id"], job)
    for _ in range(20):
        journey.step(saved["job_id"])
    result = journey.result(PERSONAS["alex"], saved["job_id"])
    assert result["phase"] == "FAILED" and "time budget" in result["error"]
    assert len(cloud.created) == 1 and not cloud.invocations and not cloud.evaluations


def cleanup_fixture(tmp_path, monkeypatch):
    journey, cloud = make_journey(Store(str(tmp_path / "state.sqlite")))
    saved = journey.save(PERSONAS["alex"], SaveAgent(
        definition=AgentDefinition(**definition(journey)), idempotency_key=uuid4().hex, deploy=True))
    drain(journey)
    preview = journey_lifecycle.preview(journey, PERSONAS["alex"], saved["agent_id"], DeletePreview(version=1), "session")
    accepted = journey_lifecycle.confirm(journey, PERSONAS["alex"], saved["agent_id"], DeleteAgent(
        version=1, confirmation_token=preview["confirmation_token"], confirm_name=preview["name"],
        idempotency_key=uuid4().hex), "session")
    app = SimpleNamespace(state=SimpleNamespace(store=journey.store, step_job=journey.step))
    monkeypatch.setattr(serverless, "application", lambda **kwargs: app)
    monkeypatch.setenv("JOB_QUEUE_URL", "synthetic-queue")
    messages = []
    monkeypatch.setattr(serverless.boto3, "client",
                        lambda name: SimpleNamespace(send_message=lambda **kwargs: messages.append(kwargs)))
    event = {"Records": [{"messageId": "cleanup", "body": json.dumps(accepted)}]}
    return journey, cloud, accepted["job_id"], event, messages


def test_many_cleanup_pages_finish_in_few_deliveries(tmp_path, monkeypatch):
    journey, cloud, job_id, event, messages = cleanup_fixture(tmp_path, monkeypatch)
    # Real S3 cleanup first deletes object versions, then verifies their absence.
    # Include enough pages to require continuation when the batch limit is hit.
    with journey.store.tx() as db:
        state = get(db, "journey-job:" + job_id)
        state["prefixes"] *= 20
        put(db, "journey-job:" + job_id, state)
    pages = 0
    def purge(entry):
        nonlocal pages
        pages += 1
        return pages % 2 == 0
    monkeypatch.setattr(cloud, "purge_agent_objects", purge)
    context = SimpleNamespace(get_remaining_time_in_millis=lambda: 300000)
    deliveries = 0
    while journey.result(PERSONAS["alex"], job_id)["phase"] != "DELETED":
        before = len(messages)
        assert serverless.worker_handler(event, context) == {"batchItemFailures": []}
        deliveries += 1
        assert deliveries < 16
        if journey.result(PERSONAS["alex"], job_id)["phase"] != "DELETED":
            assert len(messages) == before + 1
            assert json.loads(messages[-1]["MessageBody"]) == {"job_id": job_id}
    assert pages > 100 and deliveries < 10
    assert not cloud.created
    # A duplicate delivery cannot restart cleanup or create a continuation.
    before = len(messages)
    assert serverless.worker_handler(event, context) == {"batchItemFailures": []}
    assert len(messages) == before


@pytest.mark.parametrize("reason", ["time", "claim", "runtime"])
def test_cleanup_yields_when_another_step_cannot_start(tmp_path, monkeypatch, reason):
    journey, cloud, job_id, event, messages = cleanup_fixture(tmp_path, monkeypatch)
    journey.step(job_id)  # Discover resources.
    if reason != "runtime":
        journey.step(job_id)
        journey.step(job_id)
    if reason == "claim":
        with journey.store.tx() as db:
            state = get(db, "journey-job:" + job_id)
            state["claim"] = {"token": "another-worker", "expires": time.time() + 330}
            put(db, "journey-job:" + job_id, state)
    monkeypatch.setattr(cloud, "delete_runtime", lambda binding: False)
    calls = []
    monkeypatch.setattr(cloud, "purge_agent_objects", lambda entry: calls.append(entry) or True)
    context = SimpleNamespace(get_remaining_time_in_millis=lambda: 240000 if reason == "time" else 300000)
    assert serverless.worker_handler(event, context) == {"batchItemFailures": []}
    assert len(messages) == 1 and messages[0]["DelaySeconds"] == 10
    assert len(calls) == (1 if reason == "time" else 0)


def test_cleanup_queue_failure_preserves_delivery_for_retry(tmp_path, monkeypatch):
    journey, cloud, job_id, event, messages = cleanup_fixture(tmp_path, monkeypatch)
    def unavailable(**kwargs):
        raise TimeoutError("Queue unavailable")
    monkeypatch.setattr(serverless.boto3, "client", lambda name: SimpleNamespace(send_message=unavailable))
    context = SimpleNamespace(get_remaining_time_in_millis=lambda: 300000)
    assert serverless.worker_handler(event, context) == {"batchItemFailures": [{"itemIdentifier": "cleanup"}]}
    assert journey.result(PERSONAS["alex"], job_id)["phase"] == "DELETE_RUNTIMES"


def runtime_wait_fixture(tmp_path, monkeypatch):
    journey, cloud = make_journey(Store(str(tmp_path / "state.sqlite")))
    cloud.ready_result = False
    saved = journey.save(PERSONAS["alex"], SaveAgent(
        definition=AgentDefinition(**definition(journey)), idempotency_key=uuid4().hex, deploy=True))
    app = SimpleNamespace(state=SimpleNamespace(store=journey.store, step_job=journey.step))
    monkeypatch.setattr(serverless, "application", lambda **kwargs: app)
    monkeypatch.setenv("JOB_QUEUE_URL", "synthetic-queue")
    messages = []
    monkeypatch.setattr(serverless.boto3, "client",
                        lambda name: SimpleNamespace(send_message=lambda **kwargs: messages.append(kwargs)))
    event = {"Records": [{"messageId": "deploy", "body": json.dumps({"job_id": saved["job_id"]})}]}
    return journey, cloud, saved, event, messages


def test_runtime_wait_drains_in_process_without_sqs_hops(tmp_path, monkeypatch):
    journey, cloud, saved, event, messages = runtime_wait_fixture(tmp_path, monkeypatch)
    sleeps = []
    def wait(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 3:
            cloud.ready_result = True
    monkeypatch.setattr(serverless.time, "sleep", wait)
    context = SimpleNamespace(get_remaining_time_in_millis=lambda: 300000)
    assert serverless.worker_handler(event, context) == {"batchItemFailures": []}
    assert journey.result(PERSONAS["alex"], saved["job_id"])["phase"] == "DEPLOYED"
    assert messages == [] and sleeps == [10, 10, 10]


def test_runtime_wait_yields_to_queue_when_time_budget_is_low(tmp_path, monkeypatch):
    journey, cloud, saved, event, messages = runtime_wait_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(serverless.time, "sleep",
                        lambda seconds: pytest.fail("A low time budget must yield to the queue, not sleep"))
    # Exactly sleep + one full step budget must already yield: the guard is strict.
    context = SimpleNamespace(get_remaining_time_in_millis=lambda: 260000)
    assert serverless.worker_handler(event, context) == {"batchItemFailures": []}
    assert journey.result(PERSONAS["alex"], saved["job_id"])["phase"] == "WAIT_RUNTIME"
    assert len(messages) == 1 and messages[0]["DelaySeconds"] == 10
    assert json.loads(messages[0]["MessageBody"]) == {"job_id": saved["job_id"]}


def test_drained_steps_always_start_with_a_full_step_budget(tmp_path, monkeypatch):
    # A sleep is allowed only when the remaining time also covers one complete
    # step (250s >= control SDK read_timeout 210s + persistence), so a slow
    # poll after a sleep can never be killed mid-call by the Lambda timeout.
    journey, cloud, saved, event, messages = runtime_wait_fixture(tmp_path, monkeypatch)
    clock = {"now": time.time(), "start": None}
    def remaining():
        return 300000 - int((clock["now"] - clock["start"]) * 1000)
    poll_budgets = []
    def slow_ready(binding, poll=False):
        assert poll is True  # WAIT_RUNTIME must use the bounded poll client path
        poll_budgets.append(remaining())
        clock["now"] += 65  # pathologically slow control-plane poll
        return False
    cloud.ready = slow_ready
    monkeypatch.setattr(serverless.time, "sleep",
                        lambda seconds: clock.__setitem__("now", clock["now"] + seconds))
    monkeypatch.setattr(time, "time", lambda: clock["now"])
    clock["start"] = clock["now"]
    context = SimpleNamespace(get_remaining_time_in_millis=remaining)
    assert serverless.worker_handler(event, context) == {"batchItemFailures": []}
    # The slow poll finished and the delivery yielded one continuation instead
    # of being killed mid-step; every poll started with a complete step budget.
    assert journey.result(PERSONAS["alex"], saved["job_id"])["phase"] == "WAIT_RUNTIME"
    assert len(messages) == 1 and messages[0]["DelaySeconds"] == 10
    assert poll_budgets and min(poll_budgets) > 250000
    assert remaining() > 0


def test_runtime_wait_budget_bounds_chain_under_recursion_cap(tmp_path, monkeypatch):
    # A runtime that never becomes READY must fail in-process within the
    # RUNTIME_WAIT_BUDGET and in fewer than 16 chain deliveries, because Lambda
    # drops the 17th recursive delivery. The failing step sends no continuation.
    from backend.journey import RUNTIME_WAIT_BUDGET
    journey, cloud, saved, event, messages = runtime_wait_fixture(tmp_path, monkeypatch)
    clock = {"now": time.time()}
    monkeypatch.setattr(serverless.time, "sleep",
                        lambda seconds: clock.__setitem__("now", clock["now"] + seconds))
    monkeypatch.setattr(time, "time", lambda: clock["now"])
    began = clock["now"]
    deliveries = 0
    while journey.result(PERSONAS["alex"], saved["job_id"])["phase"] != "FAILED":
        start = clock["now"]
        context = SimpleNamespace(
            get_remaining_time_in_millis=lambda: 300000 - int((clock["now"] - start) * 1000))
        before = len(messages)
        assert serverless.worker_handler(event, context) == {"batchItemFailures": []}
        deliveries += 1
        assert deliveries < 16, "Lambda drops recursively queued invocations at the native limit"
        if journey.result(PERSONAS["alex"], saved["job_id"])["phase"] != "FAILED":
            assert len(messages) == before + 1
            clock["now"] += 10  # DelaySeconds between chain hops
    result = journey.result(PERSONAS["alex"], saved["job_id"])
    assert "wait budget" in result["error"]
    assert deliveries <= 15
    assert len(messages) == deliveries - 1  # the terminal delivery sends nothing
    assert clock["now"] - began < RUNTIME_WAIT_BUDGET + 120  # fails promptly after the budget


def test_legacy_wait_state_without_wait_started_still_hits_the_budget(tmp_path, monkeypatch):
    # In-flight deploys persisted before this fix have no wait_started; the
    # budget must fall back to the job's created timestamp instead of letting
    # the chain crawl toward the one-hour deadline.
    from backend.journey import RUNTIME_WAIT_BUDGET
    journey, cloud, saved, event, messages = runtime_wait_fixture(tmp_path, monkeypatch)
    journey.step(saved["job_id"])  # QUEUED -> WAIT_RUNTIME
    with journey.store.tx() as db:
        state = get(db, "journey-job:" + saved["job_id"])
        assert state["phase"] == "WAIT_RUNTIME" and "wait_started" in state
        del state["wait_started"]
        put(db, "journey-job:" + saved["job_id"], state)
    clock = {"now": time.time()}
    monkeypatch.setattr(serverless.time, "sleep",
                        lambda seconds: clock.__setitem__("now", clock["now"] + seconds))
    monkeypatch.setattr(time, "time", lambda: clock["now"])
    deliveries = 0
    while journey.result(PERSONAS["alex"], saved["job_id"])["phase"] != "FAILED":
        start = clock["now"]
        context = SimpleNamespace(
            get_remaining_time_in_millis=lambda: 300000 - int((clock["now"] - start) * 1000))
        assert serverless.worker_handler(event, context) == {"batchItemFailures": []}
        deliveries += 1
        assert deliveries < 16, "Lambda drops recursively queued invocations at the native limit"
        clock["now"] += 10
    assert "wait budget" in journey.result(PERSONAS["alex"], saved["job_id"])["error"]


def test_poll_ready_bounds_control_calls_and_maps_timeouts_to_not_ready():
    # Executes the REAL readiness method: the poll client must be bounded
    # (connect 5s / read 30s / single attempt) and a timed-out poll must mean
    # "not READY yet", while non-poll callers keep the 210s raising behavior.
    from botocore.exceptions import ReadTimeoutError
    from backend.journey_cloud import JourneyCloud
    def client(name, config=None, **kwargs):
        return SimpleNamespace(config=config)
    cloud = JourneyCloud({"region": "ap-southeast-2"}, session=SimpleNamespace(client=client))
    assert cloud.poll_control.config.read_timeout == 30
    assert cloud.poll_control.config.connect_timeout == 5
    assert cloud.poll_control.config.retries == {"total_max_attempts": 1}
    assert cloud.control.config.read_timeout == 210
    binding = {"id": "rt", "arn": "arn:runtime", "version": "1", "manifest": {}}
    def timeout(**kwargs):
        raise ReadTimeoutError(endpoint_url="https://control.invalid")
    cloud.poll_control.get_agent_runtime = timeout
    assert cloud.ready(binding, poll=True) is False
    cloud.control.get_agent_runtime = timeout
    with pytest.raises(ReadTimeoutError):
        cloud.ready(binding)
    # Runtime READY but the endpoint read times out: still "not READY yet".
    cloud.poll_control.get_agent_runtime = lambda **kwargs: {
        "agentRuntimeArn": "arn:runtime", "agentRuntimeVersion": "1", "status": "READY"}
    cloud.poll_control.get_agent_runtime_endpoint = timeout
    assert cloud.ready(binding, poll=True) is False
