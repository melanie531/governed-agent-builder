"""Journal and remove the unneeded service-linked Gateway workload IAM grant."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import time

from infra.resource_tags import validate_resource_tags
from infra.serverless import template
from scripts.deployment_target import DeploymentTarget, target_arguments


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def reviewed_role_change(before, after, gateway_arn):
    expected = copy.deepcopy(before)
    prior = expected["Resources"]["BusinessRole"]["Properties"]["Policies"]
    policy = next(p for p in prior if p["PolicyName"] == "McpUserAuthorization")
    for statement in policy["PolicyDocument"]["Statement"]:
        actions = statement["Action"]
        if ("bedrock-agentcore:GetWorkloadIdentity" in actions
                or "bedrock-agentcore:GetWorkloadAccessTokenForJWT" in actions
                or "bedrock-agentcore:GetResourceOauth2Token" in actions):
                if statement["Resource"].count(gateway_arn) != 1:
                    raise ValueError("Expected exactly one Gateway IAM grant to remove")
                statement["Resource"].remove(gateway_arn)
    if canonical(expected) != canonical(after):
        raise ValueError("Proposed template changes more than the exact Gateway workload grant removal")
    return True


def reviewed_evaluated_changes(change):
    if change["Status"] != "CREATE_COMPLETE" or change.get("NextToken"):
        raise ValueError("Change set is not complete")
    expected = {
        "BusinessRole": ("DirectModification", None, "Policies"),
        "Business": ("ResourceAttribute", "BusinessRole.Arn", "Role"),
        "BusinessIntegration": ("ResourceAttribute", "Business.Arn", "IntegrationUri"),
    }
    changes = change.get("Changes", [])
    if len(changes) != len(expected):
        raise ValueError("Change set is not the reviewed Business-role-only update")
    for entry in changes:
        item = entry["ResourceChange"]
        name = item["LogicalResourceId"]
        if (name not in expected or item["Action"] != "Modify"
                or item.get("Replacement") != "False" or len(item.get("Details", [])) != 1):
            raise ValueError("Change set is not the reviewed Business-role-only update")
        detail = item["Details"][0]
        source, cause, field = expected[name]
        if (detail.get("ChangeSource") != source or detail.get("CausingEntity") != cause
                or detail.get("Target", {}).get("Name") != field
                or detail["Target"].get("RequiresRecreation") != "Never"):
            raise ValueError("Change set has an unexpected dependency change")
    return sorted(expected)


def save(path, receipt):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, indent=2) + "\n")
    path.chmod(0o600)


def run(args):
    target = DeploymentTarget(args.expected_account, args.profile, args.region, args.state)
    cf = target.cf
    stack_id = target.state["app"]["stackId"]
    stack = cf.describe_stacks(StackName=stack_id)["Stacks"][0]
    before = cf.get_template(StackName=stack_id)["TemplateBody"]
    if isinstance(before, str):
        before = json.loads(before)
    after = template(journey=target.state["journeyPlatform"])
    gateway_id = target.state["journeyPlatform"]["mcp_onboarding"]["oauth_gateway"]["gateway_id"]
    gateway_arn = (f"arn:aws:bedrock-agentcore:{args.region}:{args.expected_account}:"
                   "workload-identity-directory/default/workload-identity/" + gateway_id)
    reviewed_role_change(before, after, gateway_arn)
    validate_resource_tags(after["Resources"])
    role = after["Resources"]["BusinessRole"]["Properties"]
    size = sum(len(canonical(p["PolicyDocument"])) for p in role["Policies"])
    if size >= 10240:
        raise ValueError("Business role exceeds the IAM inline-policy limit")
    body = canonical(after)
    cf.validate_template(TemplateBody=body)
    digest = hashlib.sha256(body.encode()).hexdigest()
    change_set_name = "reauth-business-role-" + digest[:20]
    expected = {"target": target.binding, "stack_id": stack_id,
                "gateway_workload_arn": gateway_arn, "template_sha256": digest,
                "previous_status": stack["StackStatus"], "change_set_name": change_set_name,
                "artifact_parameters": stack["Parameters"], "phase": "INTENT"}
    path = args.receipt.resolve()
    if path.exists():
        receipt = json.loads(path.read_text())
        if any(receipt.get(k) != v for k, v in expected.items() if k != "phase"):
            raise ValueError("Retained IAM release intent differs")
    else:
        receipt = expected
        save(path, receipt)
    if args.action == "plan":
        if receipt["phase"] == "INTENT":
            try:
                cf.create_change_set(
                    StackName=stack_id, ChangeSetName=change_set_name, ChangeSetType="UPDATE",
                    TemplateBody=body, Capabilities=["CAPABILITY_IAM"],
                    Parameters=[{"ParameterKey": p["ParameterKey"], "UsePreviousValue": True}
                                for p in stack["Parameters"]],
                    ClientToken=change_set_name)
            except Exception:
                # The intent is retained for read-only reconciliation, never blind retry.
                raise
            receipt["phase"] = "CHANGE_SET_ACKNOWLEDGED"
            save(path, receipt)
        for _ in range(30):
            change = cf.describe_change_set(StackName=stack_id, ChangeSetName=change_set_name)
            if change["Status"] not in ("CREATE_PENDING", "CREATE_IN_PROGRESS"):
                break
            time.sleep(2)
        receipt["evaluated_changes"] = reviewed_evaluated_changes(change)
        receipt["phase"] = "PLANNED"
        save(path, receipt)
        print(json.dumps({"phase": receipt["phase"], "changes": receipt["evaluated_changes"],
                          "inline_policy_bytes": size, "change_set": change_set_name}))
        return
    if receipt["phase"] == "VERIFIED":
        print("IAM change already verified")
        return
    if receipt["phase"] != "PLANNED":
        raise ValueError("Change set has not been reviewed")
    current = cf.describe_change_set(StackName=stack_id, ChangeSetName=change_set_name)
    if reviewed_evaluated_changes(current) != receipt.get("evaluated_changes"):
        raise ValueError("Reviewed change set changed before execution")
    receipt["phase"] = "EXECUTE_INTENT"
    save(path, receipt)
    cf.execute_change_set(StackName=stack_id, ChangeSetName=change_set_name,
                          ClientRequestToken=change_set_name)
    receipt["phase"] = "EXECUTE_ACKNOWLEDGED"
    save(path, receipt)
    for _ in range(60):
        live = cf.describe_stacks(StackName=stack_id)["Stacks"][0]
        if not live["StackStatus"].endswith("IN_PROGRESS"):
            break
        time.sleep(5)
    if live["StackStatus"] != "UPDATE_COMPLETE":
        raise RuntimeError("Business-role update is not complete; reconcile retained intent")
    actual = cf.get_template(StackName=stack_id)["TemplateBody"]
    if isinstance(actual, str):
        actual = json.loads(actual)
    if canonical(actual) != body:
        raise ValueError("Live template differs from the reviewed Business-role update")
    receipt["phase"] = "VERIFIED"
    save(path, receipt)
    print(json.dumps({"phase": "VERIFIED", "stack_status": live["StackStatus"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "apply"))
    target_arguments(parser)
    parser.add_argument("--receipt", required=True, type=Path)
    run(parser.parse_args())
