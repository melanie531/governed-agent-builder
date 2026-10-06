"""Journal a same-agent revision onto a reviewed native Gateway foundation release."""
import argparse
import json
from pathlib import Path
import time

from backend.dynamo_store import DynamoStore
from backend.foundation_runs import get
from backend.journey import Journey, TERMINAL
from backend.journey_cloud import JourneyCloud
from backend.journey_schema import AgentDefinition, SaveAgent
from foundation_harness.config import digest
from scripts.deployment_target import DeploymentTarget, target_arguments


def build_request(row, old, platform, expected_version, expected_digest,
                  old_sha, new_sha, name, key):
    if (row["current_version"] != expected_version or old["version"] != expected_version
            or old["agent_id"] != row["id"] or old["owner"] != row["owner"]
            or old["workspace"] != row["workspace"] or old["name"] != name
            or old["digest"] != expected_digest
            or old["foundation_artifact"].get("sha256") != old_sha
            or platform["artifact"].get("sha256") != new_sha
            or platform.get("gateway_force_auth_v1") is not True
            or old_sha == new_sha):
        raise ValueError("Agent version, owner, or foundation release differs from the reviewed revision")
    fields = {field: old[field] for field in AgentDefinition.model_fields}
    return SaveAgent(definition=AgentDefinition(**fields), base_version=expected_version,
                     deploy=True, idempotency_key=key)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")
    path.chmod(0o600)


def actor_from_principal(db, agent, now):
    """Use the current hosted membership, with its real workspace policy."""
    row = db.select("principals", columns=["body"],
                    where=[("id", "=", agent["owner"]), ("expires", ">", now)]).fetchone()
    actor = json.loads(row[0]) if row else None
    if (not isinstance(actor, dict) or actor.get("id") != agent["owner"]
            or actor.get("role") != "business"
            or actor.get("workspace") != agent["workspace"]
            or type(actor.get("external_allowed")) is not bool):
        raise ValueError("Agent owner has no current matching business membership")
    return actor


def run(args):
    target = DeploymentTarget(args.expected_account, args.profile, args.region, args.state)
    store = DynamoStore(target.state["app"]["outputs"]["StateTable"], target.session.resource("dynamodb"))
    key = digest([args.agent_id, args.base_version, args.definition_digest,
                  args.new_artifact_sha256, "native-gateway-revision"])[:32]
    expected = {"target": target.binding, "agent_id": args.agent_id,
                "base_version": args.base_version, "definition_digest": args.definition_digest,
                "old_artifact_sha256": args.old_artifact_sha256,
                "new_artifact_sha256": args.new_artifact_sha256, "name": args.expected_name,
                "request_key": key}
    path = args.receipt.resolve()
    if path.exists():
        receipt = json.loads(path.read_text())
        if any(receipt.get(field) != value for field, value in expected.items()):
            raise ValueError("Retained agent revision intent differs")
    else:
        receipt = None

    def read():
        with store.tx() as db:
            row = db.select("agents", where=[("id", "=", args.agent_id)]).fetchone()
            if not row:
                raise ValueError("Reviewed agent is missing")
            old_row = db.select("versions", where=[("agent", "=", args.agent_id),
                                                   ("version", "=", args.base_version)]).fetchone()
            if not old_row:
                raise ValueError("Reviewed base version is missing")
            old = json.loads(old_row["body"])
            platform = get(db, "journey-platform")
            if platform != target.state["journeyPlatform"]:
                raise ValueError("Live platform foundation differs from the bound release")
            saved = get(db, "journey-save:" + digest([row["owner"], key]))
            current = db.select("versions", where=[("agent", "=", args.agent_id),
                                                   ("version", "=", args.base_version + 1)]).fetchone()
            pending = list(db.select("jobs", where=[("agent", "=", args.agent_id),
                                                    ("stage", "not_in", sorted(TERMINAL))]))
            actor = actor_from_principal(db, row, time.time())
            return dict(row), old, platform, saved, current, pending, actor

    row, old, platform, saved, current, pending, actor = read()
    # Build from the retained base version even if an acknowledged call advanced
    # the current pointer. No user-authored field is synthesized or replaced.
    base_row = {**row, "current_version": args.base_version}
    request = build_request(base_row, old, platform, args.base_version, args.definition_digest,
                            args.old_artifact_sha256, args.new_artifact_sha256,
                            args.expected_name, key)
    signature = digest([args.agent_id, request.model_dump()])
    if receipt and receipt.get("request_signature") != signature:
        raise ValueError("Retained revision request content differs")

    if saved:
        if saved.get("signature") != signature or saved.get("agent_id") != args.agent_id:
            raise ValueError("Revision request key belongs to different content")
        response = saved["response"]
        if (not current or response.get("version") != args.base_version + 1
                or response.get("agent_id") != args.agent_id):
            raise ValueError("Revision acknowledgement lacks the expected new version")
        definition = json.loads(current["body"])
        with store.tx() as db:
            manifest = get(db, "journey-manifest:" + definition["digest"])
            deployment = get(db, f"journey-deployment:{args.agent_id}:{args.base_version + 1}") or {}
        if (definition.get("foundation_artifact", {}).get("sha256") != args.new_artifact_sha256
                or not manifest or manifest.get("gateway_force_auth_v1") is not True
                or manifest["artifact"]["sha256"] != args.new_artifact_sha256
                or deployment.get("job_id") != response.get("job_id")):
            raise ValueError("New agent version is not bound to the reviewed foundation and deployment")
        receipt = receipt or {**expected, "request_signature": signature}
        receipt.update(phase="VERIFIED" if deployment.get("status") == "DEPLOYED" else "ACKNOWLEDGED",
                       version=response["version"], job_id=response["job_id"],
                       deployment_status=deployment.get("status"))
        save(path, receipt)
        print(json.dumps({"phase": receipt["phase"], "version": receipt["version"],
                          "deployment": receipt["deployment_status"]}))
        return

    if row["current_version"] != args.base_version or current:
        raise ValueError("Agent changed after the reviewed base version")
    if pending:
        raise ValueError("Agent has an unfinished operation; do not revise during an active job")
    if receipt and receipt["phase"] != "PLANNED":
        raise ValueError("Revision outcome is uncertain; reconcile the retained request without retrying")
    if args.action == "verify":
        raise ValueError("No revision request has been submitted")
    if receipt is None:
        receipt = {**expected, "request_signature": signature, "phase": "PLANNED"}
        save(path, receipt)
    if args.action == "plan":
        print(json.dumps({"phase": "PLANNED", "agent_id": args.agent_id,
                          "base_version": args.base_version, "next_version": args.base_version + 1}))
        return

    # Use the same governed service method as the authenticated API, pinned to
    # the exact user-owned agent, base version, catalog grants, and request key.
    cloud = JourneyCloud(platform, target.session)
    journey = Journey(store, platform, cloud, hosted=True)
    with store.tx() as db:
        journey.validate(db, actor, old)
    receipt["phase"] = "INTENT"
    save(path, receipt)
    result = journey.save(actor, request, agent_id=args.agent_id)
    if result.get("version") != args.base_version + 1:
        raise ValueError("Revision returned an unexpected agent version")
    # The next explicit verify call reconciles the atomic request record and
    # waits for the existing DynamoDB-stream → SQS Worker deployment.
    receipt.update(phase="ACKNOWLEDGED", version=result["version"], job_id=result["job_id"])
    save(path, receipt)
    print(json.dumps({"phase": "ACKNOWLEDGED", "version": result["version"],
                      "job_id": result["job_id"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "apply", "verify"))
    target_arguments(parser)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--base-version", required=True, type=int)
    parser.add_argument("--definition-digest", required=True)
    parser.add_argument("--old-artifact-sha256", required=True)
    parser.add_argument("--new-artifact-sha256", required=True)
    parser.add_argument("--expected-name", required=True)
    parser.add_argument("--receipt", required=True, type=Path)
    run(parser.parse_args())
