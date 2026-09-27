"""Server-owned, single-use ALPR call capabilities on the existing run repository.

The concrete IAM run exchange supplies exact workload proof. The legacy shared
Journey role is insufficient. Redemption reuses current authority and scopes.
"""
from dataclasses import asdict
import math
import re
import secrets
import time

from foundation_harness.config import digest
from foundation_harness.context import Binding, Denied
from . import harness
from .foundation_runs import get, put
from .journey import job_state, TERMINAL

PREFIX = "journey-alpr-call:"
TOOLS = ("agent-alpr-account-vehicle", "agent-alpr-billing-notice", "agent-alpr-remediation")


def require(value, code):
    if not value:
        raise Denied(code)


def missing_workload_verifier(*args):
    raise Denied("ALPR_EXACT_RUNTIME_WORKLOAD_PROOF_REQUIRED")


def current(db, journey, row):
    """No cached grant decisions: checks membership, version, lifecycle and scopes."""
    from .app import caller_scopes
    if row.get("deployment_ref"):
        from .journey_alpr_workload import load
        record = load(db, row["deployment_ref"])
        require(record["specialist"] == row["specialist"], "ALPR_DEPLOYMENT_BINDING_DENIED")
    binding = Binding(**row["binding"])
    require(math.isfinite(binding.expires_at) and binding.expires_at > time.time(), "ALPR_AUTHORITY_EXPIRED")
    state = job_state(db, binding.run_ref)
    require(state and state["phase"] not in TERMINAL and state["deadline"] > time.time(),
            "ALPR_RUN_NOT_ACTIVE")
    actor, definition = journey.authority(db, state)
    manifest = get(db, "journey-manifest:" + definition["digest"])
    deployed = get(db, journey.deployment_key(definition))
    if state["kind"] == "deploy":
        require(state["phase"] == "SMOKE", "ALPR_RUNTIME_NOT_DEPLOYED")
        runtime = state.get("binding")
    else:
        require(state["kind"] in {"invoke", "evaluation"}
                and (deployed or {}).get("status") == "DEPLOYED", "ALPR_RUNTIME_NOT_DEPLOYED")
        runtime = deployed.get("binding")
    require(runtime and (actor["id"], actor["workspace"], definition["agent_id"], definition["version"],
                         digest(manifest), runtime["arn"], runtime["version"]) ==
            (binding.owner, binding.workspace, row["agent_id"], row["agent_version"],
             binding.manifest_digest, binding.runtime, binding.runtime_version),
            "ALPR_RUN_BINDING_DENIED")
    require(runtime.get("manifest", {}).get("digest") == binding.manifest_digest,
            "ALPR_MANIFEST_BINDING_DENIED")
    if row.get("deployment_ref"):
        require(runtime.get("alpr_deployment") == row["deployment_ref"], "ALPR_DEPLOYMENT_BINDING_DENIED")
    require(digest(manifest["foundation"]) == binding.foundation_digest, "ALPR_FOUNDATION_BINDING_DENIED")
    name = row["tool"]
    require(name in definition["tools"] and definition["component_versions"].get(name) == "1",
            "ALPR_SPECIALIST_VERSION_DENIED")
    # Scope remains the existing admin-written grant, never tool_scope() as an
    # authorization fallback. A grant narrowing invalidates an issued capability.
    scope = caller_scopes(db, actor, definition).get(name)
    require(scope == row["scope"] and isinstance(scope, dict), "ALPR_CURRENT_SCOPE_DENIED")
    harness.require_scope(scope, harness.SPECIALIST_AGENTS[name]["operation"])
    for view in harness.tool_scope(name)["data"]:
        harness.require_scope(scope, harness.READ_VIEW, view)
    return row


def issue(db, journey, *, binding, specialist, tool, arguments, verify_workload=missing_workload_verifier,
          deployment_ref=None):
    """Called inside the producer's existing transaction, after signed A admission.

    verify_workload is an explicit host integration port, not a bool/env override.
    It must recheck exact Runtime A/B versions, immutable artifacts, dedicated
    roles, backend-only A invocation and Gateway-only B invocation policies.
    """
    from .app import caller_scopes
    require(isinstance(binding, Binding) and tool in TOOLS, "ALPR_ISSUER_BINDING_REQUIRED")
    require(isinstance(arguments, dict) and set(arguments) == {"question"}
            and isinstance(arguments["question"], str) and 1 <= len(arguments["question"]) <= 4000,
            "ALPR_ARGUMENTS_DENIED")
    require(isinstance(specialist, dict) and set(specialist) ==
            {"runtime_arn", "runtime_version", "workload", "deployment_digest"}, "ALPR_SPECIALIST_BINDING_REQUIRED")
    # No capability is minted before this independent workload proof.
    require(verify_workload(db, binding, specialist) == binding, "ALPR_EXACT_RUNTIME_WORKLOAD_PROOF_REQUIRED")
    state = job_state(db, binding.run_ref)
    require(state is not None, "ALPR_RUN_NOT_ACTIVE")
    actor, definition = journey.authority(db, state)
    reference = secrets.token_urlsafe(32)
    row = {"binding": asdict(binding), "specialist": dict(specialist), "tool": tool, "version": "1",
           "arguments_digest": digest(arguments), "scope": caller_scopes(db, actor, definition).get(tool),
           "agent_id": definition["agent_id"], "agent_version": definition["version"],
           "expires_at": min(binding.expires_at, time.time() + 60), "state": "ISSUED", "views": []}
    if deployment_ref:
        row["deployment_ref"] = deployment_ref
    current(db, journey, row)
    counter_key = "journey-alpr-issued:" + binding.run_ref + ":" + binding.runtime_session
    count = get(db, counter_key) or 0
    require(type(count) is int and 0 <= count < 6, "ALPR_CALL_BUDGET_EXCEEDED")
    put(db, counter_key, count + 1)
    row["binding_digest"] = digest(row)
    put(db, PREFIX + reference, row)
    return reference


def exchange(db, journey, *, principal_arn, body):
    """principal_arn comes exclusively from a separate API Gateway AWS_IAM route."""
    base = {"reference", "operation", "runtime_arn", "runtime_version", "deployment_digest"}
    require(isinstance(body, dict), "ALPR_EXCHANGE_SHAPE_DENIED")
    operation = body.get("operation")
    fields = ({"tool", "arguments_digest"} if operation == "redeem" else
              {"binding_digest", "view"} if operation == "view" else
              {"binding_digest"} if operation in {"authorize", "finish"} else set())
    require(operation in {"inspect", "redeem", "authorize", "view", "finish"}
            and set(body) == base | fields, "ALPR_EXCHANGE_SHAPE_DENIED")
    reference = body["reference"]
    require(isinstance(reference, str) and re.fullmatch(r"[A-Za-z0-9_-]{43}", reference),
            "ALPR_CALLER_REFERENCE_REQUIRED")
    row = get(db, PREFIX + reference)
    require(row and row["expires_at"] > time.time(), "ALPR_CAPABILITY_EXPIRED")
    match = re.fullmatch(r"arn:aws:sts::(\d{12}):assumed-role/([^/]+)/[^/]+", principal_arn or "")
    role = f"arn:aws:iam::{match[1]}:role/{match[2]}" if match else None
    require(role and role == row["specialist"]["workload"]
            and all(body[key] == row["specialist"][key] for key in
                    ("runtime_arn", "runtime_version", "deployment_digest")), "ALPR_IAM_WORKLOAD_BINDING_DENIED")
    current(db, journey, row)
    if operation == "inspect":
        require(row["state"] == "ISSUED", "ALPR_CALL_REPLAY_DENIED")
    elif operation == "redeem":
        require(row["state"] == "ISSUED", "ALPR_CALL_REPLAY_DENIED")
        require(body["tool"] == row["tool"] and body["arguments_digest"] == row["arguments_digest"],
                "ALPR_CALL_BINDING_DENIED")
        row["state"] = "REDEEMED"
    else:
        require(row["state"] == "REDEEMED" and body["binding_digest"] == row["binding_digest"],
                "ALPR_CALL_BINDING_DENIED")
        if operation == "view":
            harness.require_scope(row["scope"], harness.READ_VIEW, body["view"])
            require(body["view"] in harness.tool_scope(row["tool"])["data"], "ALPR_VIEW_DENIED")
            require(body["view"] not in row["views"], "ALPR_VIEW_REPLAY_DENIED")
            row["views"].append(body["view"])
        if operation == "finish":
            require(set(row["views"]) == set(harness.tool_scope(row["tool"])["data"]),
                    "ALPR_VIEW_EVIDENCE_INCOMPLETE")
            row["state"] = "FINISHED"
    put(db, PREFIX + reference, row)
    return {"reference": reference, **{key: row[key] for key in
            ("binding_digest", "expires_at", "tool", "version", "arguments_digest", "scope")}}
