"""Actual Journey grant/version authority, with an offline workload proof adapter."""
from dataclasses import replace
import json
import time

import pytest
from fastapi import HTTPException

from backend import harness, journey_alpr
from backend.app import put_caller_scope
from backend.catalog import PERSONAS
from backend.foundation_runs import get, put
from backend.journey_schema import AgentDefinition, InvokeAgent, SaveAgent
from backend.live_catalog import grant_scope
from backend.store import Store
from foundation_harness.config import digest
from foundation_harness.context import Binding, Denied
from tests.journey_support import definition, make_journey

TOOL = "agent-alpr-remediation"
ARGS = {"question": "Investigate ALPR-C001"}
ROLE = "arn:aws:iam::123456789012:role/alpr-specialist"
SPECIALIST = {"workload": ROLE, "runtime_arn": "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/alpr-B",
              "runtime_version": "1", "deployment_digest": "b" * 64}
PRINCIPAL = "arn:aws:sts::123456789012:assumed-role/alpr-specialist/offline"


@pytest.fixture
def setup(tmp_path):
    journey, _ = make_journey(Store(str(tmp_path / "state.sqlite")))
    actor = PERSONAS["alex"]
    with journey.store.tx() as db:
        old = db.select("components", where=[("id", "=", "knowledge-search")]).fetchone()
        tool = json.loads(old["body"])
        tool["id"] = TOOL
        tool["binding"]["name"] = "alpr-investigation-specialists___" + TOOL
        tool["binding"]["inputSchema"] = harness.list_tools({"tools": [TOOL]})[0]["inputSchema"]
        tool["binding"]["schema_digest"] = digest(tool["binding"]["inputSchema"])
        tool["binding_digest"] = digest(tool["binding"])
        db.insert("components", {"id": TOOL, "body": json.dumps(tool)}, upsert=True)
        row = db.select("foundations", where=[("id", "=", "knowledge")]).fetchone()
        template = json.loads(row["body"])
        template["tools"] = [TOOL]
        db.update("foundations", {"body": json.dumps(template)}, where=[("id", "=", "knowledge")])
        db.insert("grants", {"persona": actor["id"], "component": TOOL}, ignore=True)
        put(db, grant_scope(actor, TOOL), True)
        put_caller_scope(db, actor, TOOL)
    saved = journey.save(actor, SaveAgent(definition=AgentDefinition(**definition(journey)),
        idempotency_key="offline-alpr-save", deploy=False))
    with journey.store.tx() as db:
        _, domain = journey.owned(db, actor, saved["agent_id"])
        manifest = get(db, "journey-manifest:" + domain["digest"])
        runtime = {"arn": "arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/alpr-A",
                   "version": "1", "manifest": {"digest": digest(manifest)}}
        put(db, journey.deployment_key(domain), {"status": "DEPLOYED", "binding": runtime})
    job = journey.action(actor, saved["agent_id"], InvokeAgent(
        version=1, idempotency_key="offline-alpr-invoke", input=ARGS["question"]), "invoke")
    binding = Binding(job["job_id"], actor["id"], actor["workspace"],
        "arn:aws:iam::123456789012:role/dedicated-A", runtime["arn"], "1", "s" * 33,
        digest(manifest), digest(manifest["foundation"]), 0, time.time() + 120)
    return journey, binding


def issue(setup, **changes):
    journey, binding = setup
    with journey.store.tx() as db:
        return journey_alpr.issue(db, journey, binding=binding, specialist=SPECIALIST,
            tool=TOOL, arguments=ARGS, verify_workload=lambda db, bound, specialist: bound, **changes)


def exchange(setup, reference, operation, **extra):
    journey, _ = setup
    with journey.store.tx() as db:
        return journey_alpr.exchange(db, journey, principal_arn=PRINCIPAL, body={
            "reference": reference, "operation": operation,
            **{k: SPECIALIST[k] for k in ("runtime_arn", "runtime_version", "deployment_digest")}, **extra})


def test_issue_defaults_closed_without_exact_workload_proof(setup):
    journey, binding = setup
    with journey.store.tx() as db, pytest.raises(Denied, match="EXACT_RUNTIME_WORKLOAD_PROOF_REQUIRED"):
        journey_alpr.issue(db, journey, binding=binding, specialist=SPECIALIST, tool=TOOL, arguments=ARGS)


def test_real_journey_grants_redeem_once_then_recheck_each_view(setup):
    ref = issue(setup)
    assert len(ref) == 43
    listed = exchange(setup, ref, "inspect")
    assert listed["tool"] == TOOL
    allowed = exchange(setup, ref, "redeem", tool=TOOL, arguments_digest=digest(ARGS))
    with pytest.raises(Denied, match="REPLAY"):
        exchange(setup, ref, "redeem", tool=TOOL, arguments_digest=digest(ARGS))
    for view in harness.tool_scope(TOOL)["data"]:
        exchange(setup, ref, "view", binding_digest=allowed["binding_digest"], view=view)
    exchange(setup, ref, "finish", binding_digest=allowed["binding_digest"])
    with pytest.raises(Denied):
        exchange(setup, ref, "authorize", binding_digest=allowed["binding_digest"])


@pytest.mark.parametrize("when", ["before-redeem", "after-redeem"])
def test_current_grant_revocation_denies_existing_capability(setup, when):
    ref = issue(setup)
    allowed = exchange(setup, ref, "inspect")
    if when == "after-redeem":
        allowed = exchange(setup, ref, "redeem", tool=TOOL, arguments_digest=digest(ARGS))
    journey, _ = setup
    with journey.store.tx() as db:
        db.delete("grants", where=[("persona", "=", "alex"), ("component", "=", TOOL)])
    with pytest.raises(HTTPException, match="403"):
        if when == "before-redeem":
            exchange(setup, ref, "redeem", tool=TOOL, arguments_digest=digest(ARGS))
        else:
            exchange(setup, ref, "view", binding_digest=allowed["binding_digest"],
                     view=harness.tool_scope(TOOL)["data"][0])


@pytest.mark.parametrize("field,value", [("workspace", "operations"), ("runtime_version", "2"),
    ("manifest_digest", "f" * 64), ("owner", "other"), ("expires_at", 0)])
def test_wrong_workspace_version_manifest_or_owner_cannot_issue(setup, field, value):
    journey, binding = setup
    with pytest.raises(Denied):
        issue((journey, replace(binding, **{field: value})))


@pytest.mark.parametrize("change", ["scope", "version", "workspace"])
def test_current_policy_changes_invalidate_redeemed_binding(setup, change):
    ref = issue(setup)
    allowed = exchange(setup, ref, "redeem", tool=TOOL, arguments_digest=digest(ARGS))
    journey, binding = setup
    with journey.store.tx() as db:
        state = get(db, "journey-job:" + binding.run_ref)
        if change == "scope":
            put_caller_scope(db, PERSONAS["alex"], TOOL, {"operations": harness.tool_scope(TOOL)["operations"], "data": []})
        else:
            db.update("agents", {"current_version": 2} if change == "version" else {"workspace": "operations"},
                      where=[("id", "=", state["agent"])])
    with pytest.raises((Denied, HTTPException)):
        exchange(setup, ref, "view", binding_digest=allowed["binding_digest"],
                 view=harness.tool_scope(TOOL)["data"][0])


@pytest.mark.parametrize("extra", [{"runtime_version": "2"}, {"role": ROLE}, {"scope": {}},
                                  {"arguments_digest": digest({"question": "ALPR-C002"})}])
def test_redemption_rejects_spoof_and_wrong_call(setup, extra):
    ref = issue(setup)
    with pytest.raises(Denied):
        exchange(setup, ref, "redeem", **{"tool": TOOL, "arguments_digest": digest(ARGS), **extra})


def test_wrong_iam_workload_denied_even_with_valid_reference(setup):
    ref = issue(setup)
    journey, _ = setup
    with journey.store.tx() as db, pytest.raises(Denied, match="IAM_WORKLOAD"):
        journey_alpr.exchange(db, journey, principal_arn=ROLE, body={
            "reference": ref, "operation": "inspect",
            **{k: SPECIALIST[k] for k in ("runtime_arn", "runtime_version", "deployment_digest")}})


def test_view_claim_cannot_be_replayed_and_finish_requires_all_views(setup):
    ref = issue(setup)
    bound = exchange(setup, ref, "redeem", tool=TOOL, arguments_digest=digest(ARGS))
    args = {"binding_digest": bound["binding_digest"], "view": harness.tool_scope(TOOL)["data"][0]}
    exchange(setup, ref, "view", **args)
    with pytest.raises(Denied, match="VIEW_REPLAY"):
        exchange(setup, ref, "view", **args)
    with pytest.raises(Denied, match="EVIDENCE_INCOMPLETE"):
        exchange(setup, ref, "finish", binding_digest=bound["binding_digest"])


def test_persistent_call_cap_bounds_issuance(setup):
    assert len({issue(setup) for _ in range(6)}) == 6
    with pytest.raises(Denied, match="CALL_BUDGET_EXCEEDED"):
        issue(setup)


def test_deployment_withdrawal_revokes_pending_capability(setup):
    ref = issue(setup)
    journey, binding = setup
    with journey.store.tx() as db:
        state = get(db, "journey-job:" + binding.run_ref)
        _, domain = journey.authority(db, state)
        key = journey.deployment_key(domain)
        put(db, key, {**get(db, key), "status": "STALE"})
    with pytest.raises(Denied, match="RUNTIME_NOT_DEPLOYED"):
        exchange(setup, ref, "redeem", tool=TOOL, arguments_digest=digest(ARGS))
