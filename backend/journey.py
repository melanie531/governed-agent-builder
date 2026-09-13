"""Durable Create Agent journey using the shared backend AI Catalog.

All external operations run outside transactions. Paid calls have persisted claims;
an uncertain invocation can be recovered from its receipt, never blindly replayed.
"""
import copy
import json
import re
import time
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request

from foundation_harness.config import canonical, digest
from . import journey_catalog as catalog
from . import journey_lifecycle as lifecycle
from .foundation_runs import get, put
from .journey_schema import AgentDefinition, InvokeAgent, SaveAgent, VersionAction, DeletePreview, DeleteAgent

TERMINAL = {"DEPLOYED", "SUCCEEDED", "PASSED", "FAILED_QUALITY", "FAILED", "ERROR", "UNKNOWN", "STALE", "DELETED", "DELETE_FAILED"}
PREFIX = "journey-job:"


def job_state(db, job_id):
    return get(db, PREFIX + job_id)


class Journey:
    def __init__(self, store, settings, cloud, *, hosted=False, auth=None):
        self.store, self.settings, self.cloud = store, settings, cloud
        self.hosted, self.auth = hosted, auth
        if not settings.get("enabled"):
            raise ValueError("Journey platform is not enabled")
        if not re.fullmatch(r"\d{12}", settings["account"]):
            raise ValueError("An explicit deployment account is required")
        if not settings["runtime_role"].startswith("arn:aws:iam::" + settings["account"] + ":role/"):
            raise ValueError("Runtime role belongs to another account")

    def transaction(self, fn):
        for attempt in range(6):
            try:
                with self.store.tx() as db:
                    return fn(db)
            except HTTPException as exc:
                if exc.status_code != 409 or "Concurrent governance" not in str(exc.detail) or attempt == 5:
                    raise
                time.sleep(0.025 * (attempt + 1))

    @staticmethod
    def actor(actor):
        if actor["role"] != "business":
            raise HTTPException(403, "Business workspace membership required")

    @staticmethod
    def owned(db, actor, agent_id, version=None):
        row = db.select("agents", where=[("id", "=", agent_id)]).fetchone()
        if not row or row["owner"] != actor["id"] or row["workspace"] != actor["workspace"]:
            raise HTTPException(404, "Agent not found")
        version = version or row["current_version"]
        found = db.select("versions", where=[("agent", "=", agent_id), ("version", "=", version)]).fetchone()
        if not found:
            raise HTTPException(404, "Agent version not found")
        definition = json.loads(found["body"])
        if definition.get("schema_version") != "journey-v1":
            raise HTTPException(409, "This agent uses the earlier builder; create a new agent from the current catalog")
        return dict(row), definition

    def validate(self, db, actor, definition):
        self.actor(actor)
        template = catalog.template(db, definition["template_id"])
        if actor["workspace"] not in template.get("workspaces", []):
            raise HTTPException(403, "Template is unavailable in this workspace")
        selected = {"model": [definition["model_id"]], "mcp_server": definition["mcp_servers"],
                    "tool": definition["tools"], "skill": definition["skills"]}
        resolved = catalog.resolve(db, actor, selected, definition["component_versions"])
        if template.get("requires_tool") and not definition["tools"]:
            raise HTTPException(422, "Select a knowledge tool for this template")
        if definition.get("template_version", template["version"]) != template["version"]:
            raise HTTPException(409, "Template version changed; revise the agent")
        for item in resolved.values():
            binding = item["binding"]
            if binding["type"] not in ("bedrock-converse", "mcp-server", "mcp", "instructions"):
                raise HTTPException(409, "This capability needs a compatible Foundation Harness")
            if binding["type"] in ("mcp", "mcp-server") and binding["gateway_id"] != self.settings["gateway_id"]:
                raise HTTPException(409, "Tool is not connected to the approved Gateway")
            if binding["type"] == "instructions" and digest(binding["instructions"]) != binding["content_digest"]:
                raise HTTPException(503, "Skill content does not match the approved catalog version")
        for tool_id in definition["tools"]:
            tool = resolved[tool_id]
            parent = tool.get("parent_id")
            if parent not in definition["mcp_servers"]:
                raise HTTPException(422, "Tools must belong to a selected MCP server")
            if tool["binding"].get("target_id") != resolved[parent]["binding"].get("target_id"):
                raise HTTPException(409, "Tool does not match the approved MCP target")
        for server in definition["mcp_servers"]:
            if not any(resolved[cid].get("parent_id") == server for cid in definition["tools"]):
                raise HTTPException(422, "Each selected MCP server needs at least one allowed tool")
        if definition.get("capability_digests") and definition["capability_digests"] != {
            key: item["binding_digest"] for key, item in resolved.items()
        }:
            raise HTTPException(409, "Capability content changed; revise the agent")
        return template, resolved

    def options(self, actor):
        def load(db):
            self.actor(actor)
            choices = catalog.choices(db, actor)
            return {"foundation": self.settings["foundation"],
                    "templates": [item for item in catalog.templates(db) if actor["workspace"] in item.get("workspaces", [])],
                    "choices": {kind + "s": [item for item in choices if item["kind"] == kind] for kind in ("model", "mcp_server", "tool", "skill")},
                    "catalog_revision": digest(choices), "mode": self.cloud.mode,
                    "region": self.settings["region"]}
        return self.transaction(load)

    def manifest(self, definition, resolved):
        model = resolved[definition["model_id"]]["binding"]
        return {"schema_version": "journey-foundation-v1", "foundation": definition["platform_foundation"],
                "artifact": definition["foundation_artifact"],
                "agent_id": definition["agent_id"], "version": definition["version"],
                "owner": definition["owner"], "workspace": definition["workspace"], "name": definition["name"],
                "definition_digest": definition["digest"], "prompt": definition["prompt"],
                "model_id": model["model_id"], "supports_temperature": model.get("supports_temperature", True),
                "output_format": definition["output_format"],
                "skill_instructions": [resolved[cid]["binding"]["instructions"] for cid in definition["skills"]],
                "capability_versions": definition["component_versions"],
                "tools": [{"name": resolved[cid]["binding"]["name"], "description": resolved[cid]["description"],
                           "inputSchema": resolved[cid]["binding"]["inputSchema"],
                           "schema_digest": resolved[cid]["binding"]["schema_digest"]} for cid in definition["tools"]],
                "region": self.settings["region"], "gateway_url": self.settings["gateway_url"],
                "evidence_bucket": self.settings["bucket"], "evidence_prefix": "journey/evidence",
                "log_group": self.settings["log_group"]}

    def enqueue(self, db, actor, definition, kind, token, *, session_hash=None, text=None, conversation_id=None):
        request_key = "journey-request:" + digest([actor["id"], kind, token])
        previous = get(db, request_key)
        signature = digest([definition["digest"], kind, text, conversation_id])
        if previous:
            if previous["signature"] != signature:
                raise HTTPException(409, "This request key already belongs to another operation")
            return previous["job_id"]
        recent = db.select("jobs", count=True, where=[("requester", "=", actor["id"]), ("created", ">", time.time() - 3600)]).fetchone()[0]
        if recent >= 100 and kind != "delete":
            raise HTTPException(429, "Workspace job budget reached; try again later")
        if kind == "deploy":
            deployment = get(db, self.deployment_key(definition))
            if deployment and deployment["status"] not in ("FAILED", "UNKNOWN", "STALE"):
                return deployment["job_id"]
        if kind in ("invoke", "evaluation"):
            deployment = get(db, self.deployment_key(definition))
            if not deployment or deployment["status"] != "DEPLOYED":
                raise HTTPException(409, "Deploy this version successfully before running it")
        conversation = None
        if kind == "invoke":
            if db.select("jobs", where=[("agent", "=", definition["agent_id"]),
                                       ("stage", "not_in", sorted(TERMINAL))]).fetchone():
                raise HTTPException(409, "Wait for the current operation before sending another message")
            conversation_key = f"journey-conversation:{definition['agent_id']}:{definition['version']}:"
            if conversation_id:
                conversation = get(db, conversation_key + conversation_id)
                if not conversation:
                    raise HTTPException(404, "Conversation not found for this agent version")
            else:
                conversation_id = uuid4().hex
                conversation = {"id": conversation_id, "messages": []}
                put(db, conversation_key + conversation_id, conversation)
            put(db, f"journey-chat:{definition['agent_id']}:{definition['version']}", conversation_id)
        if kind == "evaluation":
            evaluation = get(db, self.evaluation_key(definition))
            if evaluation and evaluation["status"] not in TERMINAL | {"NOT_STARTED", "SKIPPED"}:
                return evaluation["job_id"]
        job_id, now = uuid4().hex, time.time()
        state = {"id": job_id, "kind": kind, "owner": actor["id"], "workspace": actor["workspace"],
                 "agent": definition["agent_id"], "version": definition["version"], "definition_digest": definition["digest"],
                 "phase": "QUEUED", "created": now, "deadline": now + 3600, "claim": None,
                 "case_index": 0, "cases": [], "input": text, "mode": self.cloud.mode}
        if conversation is not None:
            state.update(conversation_id=conversation_id,
                         history=[{"role": item["role"], "text": item["text"][:4000]}
                                  for item in conversation["messages"][-6:]])
        db.insert("jobs", {"id": job_id, "agent": definition["agent_id"], "version": definition["version"],
                          "requester": actor["id"], "idem": token, "stage": "QUEUED",
                          "created": now, "updated": now, "deadline": state["deadline"]})
        put(db, PREFIX + job_id, state)
        put(db, request_key, {"signature": signature, "job_id": job_id})
        if session_hash:
            db.insert("job_authority", {"id": job_id, "session_hash": session_hash})
        if kind == "deploy":
            put(db, self.deployment_key(definition), {"status": "QUEUED", "job_id": job_id})
        if kind == "evaluation":
            put(db, self.evaluation_key(definition), {"status": "QUEUED", "job_id": job_id, "score": None, "cases": []})
        if kind == "invoke":
            put(db, f"journey-invocation:{definition['agent_id']}:{definition['version']}", job_id)
        return job_id

    @staticmethod
    def deployment_key(definition):
        return f"journey-deployment:{definition['agent_id']}:{definition['version']}"

    @staticmethod
    def evaluation_key(definition):
        return f"journey-evaluation:{definition['agent_id']}:{definition['version']}"

    def save(self, actor, request, *, agent_id=None, session_hash=None):
        self.actor(actor)
        def commit(db):
            request_key = "journey-save:" + digest([actor["id"], request.idempotency_key])
            signature = digest([agent_id, request.model_dump()])
            previous = get(db, request_key)
            if previous:
                if previous["signature"] != signature:
                    raise HTTPException(409, "Save request key already used for different content")
                self.owned(db, actor, previous["agent_id"])
                return previous["response"]
            payload = request.definition.model_dump(exclude_none=True)
            template, resolved = self.validate(db, actor, payload)
            if agent_id:
                row, _ = self.owned(db, actor, agent_id)
                lifecycle.ensure_available(db, agent_id)
                if request.base_version != row["current_version"]:
                    raise HTTPException(409, "Agent changed; reload before saving a new version")
                version = row["current_version"] + 1
                current_id = agent_id
                db.update("agents", {"current_version": version}, where=[("id", "=", agent_id)])
            else:
                if request.base_version is not None:
                    raise HTTPException(422, "A new agent cannot have a base version")
                active_agents = [row for row in db.select("agents", where=[("owner", "=", actor["id"])])
                                 if (get(db, lifecycle.deletion_key(row["id"])) or {}).get("status") != "DELETED"]
                if len(active_agents) >= 100:
                    raise HTTPException(429, "Agent limit reached")
                current_id, version = uuid4().hex, 1
                db.insert("agents", {"id": current_id, "owner": actor["id"], "workspace": actor["workspace"],
                                     "current_version": version, "created": time.time()})
            payload.update(agent_id=current_id, version=version, owner=actor["id"], workspace=actor["workspace"],
                           schema_version="journey-v1", catalog_mode="journey",
                           foundation_id=template["id"], foundation_version=template["version"],
                           foundation_name=template["name"], template_version=template["version"],
                           platform_foundation=copy.deepcopy(self.settings["foundation"]),
                           foundation_artifact=copy.deepcopy(self.settings["artifact"]),
                           resolved_model_id=resolved[payload["model_id"]]["binding"]["model_id"],
                           capability_digests={key: item["binding_digest"] for key, item in resolved.items()})
            payload["digest"] = digest(payload)
            put(db, "journey-manifest:" + payload["digest"], self.manifest(payload, resolved))
            db.insert("versions", {"agent": current_id, "version": version, "digest": payload["digest"],
                                   "body": json.dumps(payload), "created": time.time()})
            put(db, self.evaluation_key(payload), {"status": "NOT_STARTED" if payload["dataset"] else "SKIPPED",
                                                   "reason": None if payload["dataset"] else "NO_DATASET", "score": None, "cases": []})
            job = self.enqueue(db, actor, payload, "deploy", request.idempotency_key, session_hash=session_hash) if request.deploy else None
            response = {"agent_id": current_id, "version": version, "job_id": job}
            put(db, request_key, {"signature": signature, "agent_id": current_id, "response": response})
            return response
        return self.transaction(commit)

    def detail(self, actor, agent_id):
        def load(db):
            agent, definition = self.owned(db, actor, agent_id)
            deletion = get(db, lifecycle.deletion_key(agent_id))
            if deletion and deletion["status"] == "DELETED":
                return {**agent, "definition": definition, "deletion": deletion,
                        "deployment": {"status": "DELETED"}, "evaluation": {"status": "SKIPPED", "score": None, "cases": []},
                        "versions": [], "conversation": None, "last_invocation": None,
                        "readiness": {"deployable": False, "issues": []}, "mode": self.cloud.mode}
            deployment = get(db, self.deployment_key(definition)) or {"status": "NOT_DEPLOYED"}
            evaluation = get(db, self.evaluation_key(definition)) or {"status": "NOT_STARTED", "score": None, "cases": []}
            versions = [dict(row) for row in db.select("versions", columns=["version", "digest", "created"],
                                                      where=[("agent", "=", agent_id)], order="version", descending=True)]
            issues = []
            try:
                self.validate(db, actor, definition)
            except HTTPException as exc:
                issues.append(str(exc.detail))
            invocation_id = get(db, f"journey-invocation:{agent_id}:{definition['version']}")
            invocation = job_state(db, invocation_id) if invocation_id else None
            last_invocation = ({key: invocation[key] for key in ("id", "phase", "output", "error", "trace_id", "model_id", "tool_calls")
                                if key in invocation} if invocation else None)
            conversation_id = get(db, f"journey-chat:{agent_id}:{definition['version']}")
            conversation = get(db, f"journey-conversation:{agent_id}:{definition['version']}:{conversation_id}") if conversation_id else None
            return {**agent, "definition": definition, "deployment": deployment, "evaluation": evaluation,
                    "conversation": conversation, "deletion": deletion,
                    "last_invocation": last_invocation,
                    "versions": versions, "readiness": {"deployable": not issues, "issues": issues}, "mode": self.cloud.mode}
        return self.transaction(load)

    def action(self, actor, agent_id, request, kind, session_hash=None):
        def commit(db):
            agent, definition = self.owned(db, actor, agent_id)
            lifecycle.ensure_available(db, agent_id)
            if request.version != agent["current_version"]:
                raise HTTPException(409, "Use the current version of this agent")
            self.validate(db, actor, definition)
            if kind == "evaluation" and not definition["dataset"]:
                raise HTTPException(422, "Add an evaluation dataset in a new version before running evaluation")
            job_id = self.enqueue(db, actor, definition, kind, request.idempotency_key,
                                  session_hash=session_hash, text=getattr(request, "input", None),
                                  conversation_id=getattr(request, "conversation_id", None))
            state = job_state(db, job_id)
            return {"job_id": job_id, **({"conversation_id": state["conversation_id"]} if state.get("conversation_id") else {})}
        return self.transaction(commit)

    def result(self, actor, job_id):
        def load(db):
            state = job_state(db, job_id)
            if not state:
                raise HTTPException(404, "Job not found")
            self.owned(db, actor, state["agent"])
            return {key: state[key] for key in ("id", "kind", "phase", "version", "mode", "cases", "output", "error", "trace_id",
                                               "model_id", "tool_calls", "score") if key in state}
        return self.transaction(load)

    def authority(self, db, state):
        if self.hosted:
            authority = db.select("job_authority", where=[("id", "=", state["id"])]).fetchone()
            session = db.select("hosted_sessions", where=[("id_hash", "=", authority["session_hash"]) ]).fetchone() if authority else None
            principal = db.select("principals", where=[("id", "=", state["owner"])]).fetchone()
            if not session or not principal or min(session["expires"], principal["expires"]) <= time.time():
                raise HTTPException(403, "Job authorization expired; sign in and retry")
            if session["subject"] != state["owner"]:
                raise HTTPException(403, "Invalid job authority")
            claims = self.auth.verify(session["access_token"], "access")
            membership = self.auth.membership(claims)
            actor = json.loads(principal["body"])
            if claims["sub"] != actor["id"] or membership["workspace"] != actor["workspace"]:
                raise HTTPException(403, "Workspace membership changed")
        else:
            from .catalog import PERSONAS
            actor = PERSONAS[state["owner"]]
        agent, definition = self.owned(db, actor, state["agent"], state["version"])
        if agent["current_version"] != state["version"] or definition["digest"] != state["definition_digest"]:
            raise HTTPException(409, "Agent was revised while this task was running")
        if state["kind"] != "delete":
            lifecycle.ensure_available(db, state["agent"])
            self.validate(db, actor, definition)
        return actor, definition

    def persist(self, db, state):
        put(db, PREFIX + state["id"], state)
        public = {key: state[key] for key in ("phase", "kind", "error", "score") if key in state}
        db.update("jobs", {"stage": state["phase"], "updated": time.time(), "result": json.dumps(public)},
                  where=[("id", "=", state["id"])])

    def step(self, job_id):
        task = None
        def prepare(db):
            state = job_state(db, job_id)
            if not state or state["phase"] in TERMINAL:
                return None
            actor, definition = self.authority(db, state)
            if time.time() > state["deadline"]:
                raise HTTPException(408, "Task exceeded its time budget")
            claim = state.get("claim")
            if claim and claim["expires"] > time.time():
                return None
            recovery = bool(claim)
            token = uuid4().hex
            state["claim"] = {"token": token, "expires": time.time() + 330}
            self.persist(db, state)
            deployment = get(db, self.deployment_key(definition))
            return state, definition, actor, deployment, token, recovery
        try:
            task = self.transaction(prepare)
            if not task:
                return
            state, definition, actor, deployment, token, recovery = task
            outcome = self.perform(state, definition, deployment, recovery)
            def commit(db):
                current = job_state(db, job_id)
                if not current or (current.get("claim") or {}).get("token") != token:
                    return
                self.authority(db, current)
                current.update(outcome)
                current["claim"] = None
                self.finish_step(db, current, definition, actor)
                self.persist(db, current)
            self.transaction(commit)
        except Exception as exc:
            # Do not include SDK payloads, credentials or raw trace contents in errors.
            from botocore.exceptions import ClientError, BotoCoreError
            uncertain = bool(task and (isinstance(exc, (BotoCoreError, TimeoutError))
                             or isinstance(exc, ClientError) and exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0) >= 500)
                             and (task[0]["kind"] in ("invoke", "evaluation") or task[0]["phase"] == "SMOKE"))
            message = str(exc.detail) if isinstance(exc, HTTPException) else (
                str(exc) if isinstance(exc, (ValueError, TimeoutError)) else
                (exc.operation_name + " failed: " + exc.response["Error"]["Code"]
                 + " (request " + exc.response.get("ResponseMetadata", {}).get("RequestId", "unavailable") + ")") if isinstance(exc, ClientError) else
                "The task could not complete. Review its status before retrying.")
            def fail(db):
                state = job_state(db, job_id)
                if not state or state["phase"] in TERMINAL:
                    return
                if task and (state.get("claim") or {}).get("token") != task[4]:
                    return
                state["phase"] = ("DELETE_FAILED" if state["kind"] == "delete" else
                                  "UNKNOWN" if uncertain else "ERROR" if state["kind"] == "evaluation" else "FAILED")
                state["error"] = ("The service outcome is uncertain. No automatic replay was sent." if uncertain else message[:400])
                state["claim"] = None
                self.persist(db, state)
                if state["kind"] == "delete":
                    put(db, lifecycle.deletion_key(state["agent"]), {
                        "status": "DELETE_FAILED", "job_id": job_id, "error": state["error"]})
                key = (f"journey-evaluation:{state['agent']}:{state['version']}" if state["kind"] == "evaluation" else
                       f"journey-deployment:{state['agent']}:{state['version']}" if state["kind"] == "deploy" else None)
                if key:
                    previous = get(db, key) or {}
                    put(db, key, {**previous, "status": state["phase"], "error": state["error"], "job_id": job_id})
            self.transaction(fail)

    def invocation(self, state, definition, binding, text, suffix, recovery):
        request_id = digest([state["id"], suffix])[:32]
        if recovery:
            receipt = self.cloud.recover_invocation(definition, request_id)
            if receipt is None:
                return None
            return receipt
        return self.cloud.invoke(binding, definition, text, request_id,
                                 **({"history": state["history"]} if state.get("history") else {}))

    def perform(self, state, definition, deployment, recovery):
        phase, kind = state["phase"], state["kind"]
        if kind == "delete":
            return lifecycle.perform(self, state)
        if kind == "deploy":
            if phase == "QUEUED":
                manifest = self.transaction(lambda db: get(db, "journey-manifest:" + definition["digest"]))
                binding = self.cloud.create(manifest, digest([definition["digest"], "deploy"]))
                return {"phase": "WAIT_RUNTIME", "binding": binding}
            if phase == "WAIT_RUNTIME":
                return {"phase": "SMOKE" if self.cloud.ready(state["binding"]) else "WAIT_RUNTIME"}
            if phase == "SMOKE":
                receipt = self.invocation(state, definition, state["binding"],
                                          "Reply with one short sentence confirming that you are ready to help.", "smoke", recovery)
                return {"phase": "DEPLOYED", "smoke": self.public_receipt(receipt)} if receipt else {
                    "phase": "UNKNOWN", "error": "Smoke invocation outcome is uncertain; no duplicate invocation was sent"}
        binding = deployment["binding"] if deployment else None
        if kind == "invoke":
            receipt = self.invocation(state, definition, binding, state["input"], "invoke", recovery)
            return {"phase": "SUCCEEDED", **self.public_receipt(receipt)} if receipt else {
                "phase": "UNKNOWN", "error": "Invocation outcome is uncertain; no duplicate invocation was sent"}
        if kind == "evaluation":
            index = state["case_index"]
            case = definition["dataset"][index]
            if phase in ("QUEUED", "EVAL_INVOKE"):
                receipt = self.invocation(state, definition, binding, case["input"], "case:" + case["id"], recovery)
                return {"phase": "EVAL_SCORE", "case_receipt": receipt["evidence"],
                        "case_output": self.public_receipt(receipt)} if receipt else {
                    "phase": "UNKNOWN", "error": "Case invocation outcome is uncertain; no duplicate invocation was sent"}
            if phase == "EVAL_SCORE":
                if recovery:
                    return {"phase": "UNKNOWN", "error": "Evaluation outcome is uncertain; Evaluate was not called again"}
                receipt = self.cloud.read(state["case_receipt"])
                result = self.cloud.evaluate(definition, case, receipt, state["id"])
                checks = self.structural_checks(case, receipt["output"])
                passed = (result["score"] >= definition["minimum_score"] and all(c["passed"] for c in checks)
                          and not result["ignored_reference_fields"])
                cases = [*state["cases"], {"id": case["id"], "input": case["input"], **state["case_output"],
                                          **result, "checks": checks, "passed": passed}]
                done = index + 1 == len(definition["dataset"])
                return {"phase": ("PASSED" if all(c["passed"] for c in cases) else "FAILED_QUALITY") if done else "EVAL_INVOKE",
                        "case_index": index + 1, "cases": cases, "score": sum(c["score"] for c in cases) / len(cases)}
        raise ValueError("Unknown journey job phase")

    @staticmethod
    def public_receipt(receipt):
        return {key: receipt[key] for key in ("output", "trace_id", "model_id", "tool_calls", "usage", "latency_ms")}

    @staticmethod
    def structural_checks(case, output):
        checks = [{"name": "Required term: " + term, "passed": term.casefold() in output.casefold()}
                  for term in case.get("required_terms", [])]
        if case.get("require_citation"):
            checks.append({"name": "Contains a source reference", "passed": bool(re.search(r"https://|\[[^\]]+\]|aurora-(launch|support|data)", output))})
        if case.get("expected_format") == "json":
            try:
                json.loads(output)
                valid = True
            except ValueError:
                valid = False
            checks.append({"name": "Valid JSON", "passed": valid})
        return checks

    def finish_step(self, db, state, definition, actor):
        if state["kind"] == "delete":
            lifecycle.finish(self, db, state, definition)
        if state["kind"] == "invoke" and state["phase"] == "SUCCEEDED":
            key = f"journey-conversation:{state['agent']}:{state['version']}:{state['conversation_id']}"
            conversation = get(db, key)
            if not any(item.get("job_id") == state["id"] for item in conversation["messages"]):
                conversation["messages"] = [*conversation["messages"][-18:],
                    {"role": "user", "text": state["input"], "job_id": state["id"]},
                    {"role": "assistant", "text": state["output"], "job_id": state["id"],
                     "trace_id": state["trace_id"], "model_id": state["model_id"],
                     "tools": [call["name"] for call in state["tool_calls"]]}]
                put(db, key, conversation)
        if state["kind"] == "deploy":
            deployment = {"status": state["phase"], "job_id": state["id"]}
            if state.get("binding"):
                deployment["binding"] = state["binding"]
            if state.get("smoke"):
                deployment["smoke"] = state["smoke"]
            if state.get("error"):
                deployment["error"] = state["error"]
            put(db, self.deployment_key(definition), deployment)
            if state["phase"] == "DEPLOYED" and definition["dataset"]:
                auth = db.select("job_authority", where=[("id", "=", state["id"])]).fetchone()
                self.enqueue(db, actor, definition, "evaluation", state["id"] + "-evaluation",
                             session_hash=auth["session_hash"] if auth else None)
        if state["kind"] == "evaluation":
            put(db, self.evaluation_key(definition), {"status": state["phase"], "job_id": state["id"],
                                                      "cases": state["cases"], "score": state.get("score"),
                                                      "error": state.get("error"), "total": len(definition["dataset"])})


def router(journey, who):
    routes = APIRouter(prefix="/api/journey")

    def session_hash(request):
        if not journey.hosted:
            return None
        from .hosted_auth import SESSION_COOKIE, sha
        return sha(request.cookies.get(SESSION_COOKIE, ""))

    @routes.get("/options")
    def options(request: Request):
        return journey.options(who(request))

    @routes.post("/agents", status_code=202)
    def create(body: SaveAgent, request: Request):
        result = journey.save(who(request), body, session_hash=session_hash(request))
        request.app.state.wake.set()
        return result

    @routes.post("/agents/{agent_id}/versions", status_code=202)
    def revise(agent_id: str, body: SaveAgent, request: Request):
        result = journey.save(who(request), body, agent_id=agent_id, session_hash=session_hash(request))
        request.app.state.wake.set()
        return result

    @routes.get("/agents/{agent_id}")
    def detail(agent_id: str, request: Request):
        return journey.detail(who(request), agent_id)

    @routes.post("/agents/{agent_id}/deploy", status_code=202)
    def deploy(agent_id: str, body: VersionAction, request: Request):
        result = journey.action(who(request), agent_id, body, "deploy", session_hash(request))
        request.app.state.wake.set()
        return result

    @routes.post("/agents/{agent_id}/invoke", status_code=202)
    def invoke(agent_id: str, body: InvokeAgent, request: Request):
        result = journey.action(who(request), agent_id, body, "invoke", session_hash(request))
        request.app.state.wake.set()
        return result

    @routes.post("/agents/{agent_id}/evaluate", status_code=202)
    def evaluate(agent_id: str, body: VersionAction, request: Request):
        result = journey.action(who(request), agent_id, body, "evaluation", session_hash(request))
        request.app.state.wake.set()
        return result

    @routes.post("/agents/{agent_id}/deletion-preview")
    def deletion_preview(agent_id: str, body: DeletePreview, request: Request):
        return lifecycle.preview(journey, who(request), agent_id, body, session_hash(request))

    @routes.post("/agents/{agent_id}/delete", status_code=202)
    def delete(agent_id: str, body: DeleteAgent, request: Request):
        result = lifecycle.confirm(journey, who(request), agent_id, body, session_hash(request))
        request.app.state.wake.set()
        return result

    @routes.get("/jobs/{job_id}")
    def result(job_id: str, request: Request):
        return journey.result(who(request), job_id)

    @routes.post("/catalog/skills", status_code=201)
    def skill(body: catalog.SkillPublication, request: Request):
        actor = who(request, True)
        return journey.transaction(lambda db: catalog.publish_skill(db, actor, body))

    return routes
