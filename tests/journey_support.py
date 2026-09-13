"""Offline dependency injection for journey contracts; never packaged in production."""
import copy
import json
from pathlib import Path

from backend.catalog import PERSONAS
from backend.foundation_runs import put
from backend.journey import Journey, TERMINAL
from backend.journey_catalog import SkillPublication, publish_skill
from backend.live_catalog import grant_scope
from foundation_harness.config import digest

ROOT = Path(__file__).resolve().parents[1]


class OfflineCloud:
    mode = "offline-test"

    def __init__(self):
        self.created, self.invocations, self.evaluations, self.objects = {}, [], [], {}
        self.score = 1.0
        self.ready_result = True
        self.fail_evaluation = False

    def create(self, manifest, token):
        self.created.setdefault(token, copy.deepcopy(manifest))
        return {"id": token, "arn": "offline:runtime:" + token, "version": "1"}

    def ready(self, binding):
        return self.ready_result

    def invoke(self, binding, definition, text, request_id):
        self.invocations.append({"request_id": request_id, "text": text})
        receipt = {"status": "SUCCEEDED", "output": "Aurora launches in October. Support responds in four hours. [aurora-launch] [aurora-support]",
                   "trace_id": request_id, "session_id": "gab-" + request_id, "definition_digest": definition["digest"],
                   "model_id": definition["resolved_model_id"], "tool_calls": [{"name": "knowledge___search", "arguments": {"query": text}}],
                   "usage": {"inputTokens": 10, "outputTokens": 20}, "latency_ms": 1, "spans": []}
        key = "receipt:" + request_id
        self.objects[key] = copy.deepcopy(receipt)
        return {**receipt, "evidence": {"key": key, "version_id": "1", "digest": digest(receipt)}}

    def read(self, location):
        return copy.deepcopy(self.objects[location["key"]])

    def recover_invocation(self, definition, request_id):
        return None

    def evaluate(self, definition, case, receipt, evaluation_id):
        self.evaluations.append({"case": case["id"], "evaluation_id": evaluation_id})
        if self.fail_evaluation:
            raise ValueError("Evaluation service unavailable")
        return {"score": self.score, "explanation": "Offline contract-test evaluator result.",
                "label": "Correct", "ignored_reference_fields": [], "evaluator_id": "offline-test",
                "request_id": "test-request", "evidence": {"key": "offline", "version_id": "1", "digest": "0" * 64}}


def seed(store):
    settings = {"enabled": True, "account": "123456789012", "region": "us-west-2",
                "runtime_role": "arn:aws:iam::123456789012:role/journey-test",
                "gateway_id": "test-gateway", "gateway_url": "https://test-gateway.gateway.bedrock-agentcore.us-west-2.amazonaws.com/mcp",
                "bucket": "test-private-bucket", "log_group": "/test/journey",
                "network": {"networkMode": "PUBLIC"},
                "artifact": {"bucket": "test-private-bucket", "key": "foundation.zip", "version_id": "1"},
                "foundation": json.loads((ROOT / "examples/journey/foundation.json").read_text())}
    model_binding = {"type": "bedrock-converse", "model_id": "test.claude", "supports_temperature": True}
    schema = {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}
    bindings = [("bedrock-claude", "model", "Catalog test model", model_binding),
                ("mcp-tavily", "mcp_server", "Tavily", {"type": "mcp-server", "gateway_id": "test-gateway", "target_id": "tavily"}),
                ("mcp-knowledge", "mcp_server", "Aurora knowledge", {"type": "mcp-server", "gateway_id": "test-gateway", "target_id": "knowledge"}),
                ("web-search", "tool", "Web search", {"type": "mcp", "gateway_id": "test-gateway", "name": "tavily___tavily_search",
                                                     "target_id": "tavily", "inputSchema": schema, "schema_digest": digest(schema)}),
                ("knowledge-search", "tool", "Knowledge search", {"type": "mcp", "gateway_id": "test-gateway", "name": "knowledge___search",
                                                                 "target_id": "knowledge", "inputSchema": schema, "schema_digest": digest(schema)})]
    with store.tx() as db:
        for cid, kind, name, binding in bindings:
            item = {"id": cid, "kind": kind, "name": name, "description": "Offline catalog contract fixture",
                    "catalog": "journey", "version": "1", "provider": "Offline test", "origin": "AI Catalog",
                    "approved": True, "fixture": False, "external": False, "protocol": binding["type"],
                    "discoverable_workspaces": ["research"], "default_grant_workspaces": ["research"],
                    "execution_ready": True, "integration_ready": True, "supported": True,
                    "binding": binding, "binding_digest": digest(binding)}
            if kind in ("tool", "mcp_server"):
                item["protocol"] = "MCP"
            if kind == "tool":
                item["parent_id"] = "mcp-" + binding["target_id"]
            if kind == "mcp_server":
                item["default_tool_ids"] = [entry[0] for entry in bindings
                                            if entry[1] == "tool" and entry[3]["target_id"] == binding["target_id"]]
            db.insert("components", {"id": cid, "body": json.dumps(item)}, upsert=True)
        for data in json.loads((ROOT / "examples/journey/skills.json").read_text()):
            publish_skill(db, PERSONAS["admin"], SkillPublication(id=data["id"], name=data["name"],
                          description=data["description"], instructions=data["instruction"], workspaces=["research"]))
        for item in json.loads((ROOT / "examples/journey/templates.json").read_text()):
            item.update(catalog="journey", version="1", approved=True, workspaces=["research"],
                        requires_tool=item["id"] == "knowledge")
            db.insert("foundations", {"id": item["id"], "body": json.dumps(item)}, upsert=True)
        for cid in [item[0] for item in bindings] + ["concise", "citations"]:
            db.insert("grants", {"persona": "alex", "component": cid}, ignore=True)
            put(db, grant_scope(PERSONAS["alex"], cid), True)
        put(db, "journey-platform", settings)
    return settings


def make_journey(store):
    cloud = OfflineCloud()
    return Journey(store, seed(store), cloud), cloud


def definition(journey, template_id="knowledge", dataset=None):
    options = journey.options(PERSONAS["alex"])
    template = next(item for item in options["templates"] if item["id"] == template_id)
    with journey.store.tx() as db:
        parents = {json.loads(row["body"]).get("parent_id") for row in db.select("components")
                   if row["id"] in template["tools"]}
    servers = sorted(parents - {None})
    selected = ["bedrock-claude", *servers, *template["tools"], *template["skills"]]
    return {"template_id": template_id, "name": "Contract test agent", "model_id": "bedrock-claude",
            "prompt": template["prompt"], "mcp_servers": servers, "tools": template["tools"], "skills": template["skills"],
            "output_format": "text", "dataset": [] if dataset is None else dataset, "minimum_score": 0.7,
            "component_versions": {cid: "1" for cid in selected}}


def drain(journey):
    for _ in range(100):
        with journey.store.tx() as db:
            jobs = [row["id"] for row in db.select("jobs") if row["stage"] not in TERMINAL]
        if not jobs:
            return
        for job_id in jobs:
            journey.step(job_id)
    raise AssertionError("Journey did not reach a terminal state")
