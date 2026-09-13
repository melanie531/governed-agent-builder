import copy
import json
from uuid import uuid4

import pytest

from backend.catalog import PERSONAS
from backend.foundation_runs import get
from backend.journey_schema import AgentDefinition, SaveAgent
from backend.store import Store
from foundation_harness.journey_runtime import execute
from tests.journey_support import make_journey, definition


@pytest.fixture
def manifest(tmp_path):
    journey, _ = make_journey(Store(str(tmp_path / "runtime.sqlite")))
    saved = journey.save(PERSONAS["alex"], SaveAgent(definition=AgentDefinition(**definition(journey)),
                        idempotency_key=uuid4().hex, deploy=False))
    with journey.store.tx() as db:
        version = journey.owned(db, PERSONAS["alex"], saved["agent_id"])[1]
        return get(db, "journey-manifest:" + version["digest"])


class Model:
    def __init__(self, tool):
        self.tool, self.requests = tool, []

    def converse(self, **request):
        self.requests.append(copy.deepcopy(request))
        first = len(self.requests) == 1
        content = [{"toolUse": {"toolUseId": "call-1", "name": self.tool,
                                "input": {"query": "Aurora launch"}}}] if first else [{"text": "Aurora launches in October. [aurora-launch]"}]
        return {"output": {"message": {"role": "assistant", "content": content}},
                "stopReason": "tool_use" if first else "end_turn", "usage": {"inputTokens": 10, "outputTokens": 20}}


class Gateway:
    def __init__(self, manifest):
        self.tools, self.calls = copy.deepcopy(manifest["tools"]), []

    def discover(self):
        return self.tools

    def call(self, name, arguments):
        self.calls.append((name, arguments))
        return "Aurora launches in October. Source: aurora-launch."


def test_selected_mcp_operation_runs_and_records_actual_call_and_trace(manifest):
    gateway = Gateway(manifest)
    model = Model(manifest["tools"][0]["name"])
    receipt = execute(manifest, "What is the launch plan?", "gab-" + uuid4().hex, model=model, gateway=gateway)
    assert receipt["model_id"] == manifest["model_id"]
    assert receipt["usage"] == {"inputTokens": 20, "outputTokens": 40}
    assert len(gateway.calls) == 1
    assert len(receipt["spans"]) == 4  # root, two model calls, real tool execution
    assert all(span["traceId"] == receipt["trace_id"] for span in receipt["spans"])
    assert len({span["spanId"] for span in receipt["spans"]}) == 4
    tool_span = next(span for span in receipt["spans"] if span["attributes"]["gen_ai.operation.name"] == "execute_tool")
    assert tool_span["attributes"]["gen_ai.tool.name"] == gateway.calls[0][0]
    model_result = model.requests[1]["messages"][2]["content"][0]["toolResult"]["content"][0]["text"]
    assert tool_span["attributes"]["gen_ai.tool.call.result"] == model_result
    assert receipt["session_id"] == tool_span["attributes"]["session.id"]


def test_model_cannot_call_an_operation_from_an_unselected_mcp_server(manifest):
    gateway = Gateway(manifest)
    with pytest.raises(ValueError, match="unselected tool"):
        execute(manifest, "Run another operation", "gab-" + uuid4().hex, model=Model("tavily___tavily_search"), gateway=gateway)
    assert not gateway.calls


def test_changed_gateway_schema_is_rejected_before_any_model_call(manifest):
    gateway = Gateway(manifest)
    gateway.tools[0]["inputSchema"]["properties"]["query"]["maxLength"] = 2
    model = Model(manifest["tools"][0]["name"])
    with pytest.raises(ValueError, match="schema changed"):
        execute(manifest, "Question", "gab-" + uuid4().hex, model=model, gateway=gateway)
    assert not model.requests
    assert not gateway.calls


def test_model_options_and_skills_come_from_the_pinned_catalog_manifest(manifest):
    manifest["model_id"] = "newly-published-provider-model"
    manifest["supports_temperature"] = False
    manifest["skill_instructions"] = ["Use the newly published domain style."]
    gateway, model = Gateway(manifest), Model(manifest["tools"][0]["name"])
    execute(manifest, "Question", "gab-" + uuid4().hex, model=model, gateway=gateway)
    assert all(request["modelId"] == manifest["model_id"] for request in model.requests)
    assert all("temperature" not in request["inferenceConfig"] for request in model.requests)
    assert manifest["skill_instructions"][0] in model.requests[0]["system"][0]["text"]
