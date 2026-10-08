"""Scoped reviewed recovery: resume a second-region CREATE_FAILED app stack.

A fresh install in a second region ends CREATE_FAILED when the three
account-global CloudFront names (OAC origin access control, SPA function,
Headers response headers policy) collide with the same solution already
installed in another region of the account. The reviewed fix region-scopes
exactly those three names. This script resumes the SAME failed stack with
update_stack + DisableRollback=True (the documented CREATE_FAILED recovery),
accepting ONLY that three-name template difference and reusing the stack's
existing ArtifactBucket/ArtifactKey parameters unchanged (recovery must not
change code). Anything else fails closed before any write.

Portable inputs (no absolute paths or account literals in this file):
  --expected-account  12-digit target account; verified against STS before any write
  --profile           operator-approved AWS SDK profile (credentials stay in the
                      shared AWS config/keychain; never passed on the command line)
  --region            target region, e.g. ap-southeast-2
  --state             fresh target-specific local JSON state path (see
                      scripts/deployment_target.py; legacy state names are refused)
  --reconciled-create-failed-stack-id
                      exact StackId of the operator-reviewed CREATE_FAILED app stack
  --evidence          directory for template-live/proposed and the receipt

Run from a checkout: python scripts/scoped_second_region_recovery.py ...
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from infra.serverless import template  # noqa: E402
from scripts.deployment_target import DeploymentTarget, target_arguments  # noqa: E402
from scripts.serverless_deploy import safety  # noqa: E402

RENAMES = (
    (("OAC", "Properties", "OriginAccessControlConfig", "Name"), "${AWS::StackName}-s3"),
    (("SPA", "Properties", "Name"), "${AWS::StackName}-spa"),
    (("Headers", "Properties", "ResponseHeadersPolicyConfig", "Name"), "${AWS::StackName}-headers"),
)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def reviewed_template_change(before, after):
    expected = copy.deepcopy(before)
    for path, old in RENAMES:
        node = expected["Resources"]
        for key in path[:-1]:
            node = node[key]
        if node[path[-1]] != {"Fn::Sub": old}:
            raise ValueError("Live " + path[0] + " name is not the expected pre-fix global name")
        node[path[-1]] = {"Fn::Sub": old + "-${AWS::Region}"}
    if canonical(expected) != canonical(after):
        raise ValueError("Proposed template changes more than the three reviewed CloudFront names")
    return True


def run(args):
    target = DeploymentTarget(args.expected_account, args.profile, args.region, args.state,
                              reconciled_create_failed_stack_id=args.reconciled_create_failed_stack_id)
    cf = target.session.client("cloudformation")
    state = target.state
    stack_id = args.reconciled_create_failed_stack_id
    if state["app"]["stackId"] != stack_id:
        raise RuntimeError("Reviewed stack id does not match the bound app stack")
    stack = cf.describe_stacks(StackName=stack_id)["Stacks"][0]
    if stack["StackStatus"] != "CREATE_FAILED":
        raise RuntimeError("Stack is not CREATE_FAILED; use the standard deploy path")
    previous = cf.get_template(StackName=stack_id)["TemplateBody"]
    previous = json.loads(previous) if isinstance(previous, str) else previous
    body = template(journey=state.get("journeyPlatform"))
    # Reviewed and submitted identically; see retain_legacy_pool_owner_tag.
    from infra.resource_tags import retain_legacy_pool_owner_tag
    body = retain_legacy_pool_owner_tag(previous, body)
    safety(body)
    reviewed_template_change(previous, body)
    parameters = {p["ParameterKey"]: p.get("ParameterValue") for p in stack.get("Parameters", [])}
    if not parameters.get("ArtifactBucket") or not parameters.get("ArtifactKey"):
        raise RuntimeError("Failed stack is missing artifact parameters; recovery must not change code")

    evidence = Path(args.evidence).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "template-live.json").write_text(json.dumps(previous, indent=2))
    (evidence / "template-proposed.json").write_text(json.dumps(body, indent=2))
    receipt = {"stack_id": stack_id, "prior_status": "CREATE_FAILED",
               "allowed_diff": "region-scope the OAC/SPA/Headers CloudFront names "
                               "(${AWS::StackName}-{s3,spa,headers} gain -${AWS::Region})",
               "time": time.time()}
    (evidence / "receipt.json").write_text(json.dumps(receipt, indent=2))

    content = json.dumps(body, separators=(",", ":")).encode()
    if len(content) <= 51200:
        template_request = {"TemplateBody": content.decode()}
    else:
        bucket = state["artifacts"]["outputs"]["Bucket"]
        key = "templates/scoped-second-region-recovery/" + hashlib.sha256(content).hexdigest() + ".json"
        uploaded = target.session.client("s3").put_object(
            Bucket=bucket, Key=key, Body=content, ServerSideEncryption="AES256",
            ContentType="application/json", Tagging="auto-delete=no")
        from urllib.parse import quote
        template_request = {"TemplateURL": f"https://{bucket}.s3.{args.region}.amazonaws.com/{key}?versionId="
                            + quote(uploaded["VersionId"], safe="")}
    cf.update_stack(StackName=stack_id, **template_request, Capabilities=["CAPABILITY_IAM"],
                    Tags=[{"Key": "project", "Value": "governed-agent-builder"},
                          {"Key": "architecture", "Value": "managed-serverless"},
                          {"Key": "auto-delete", "Value": "no"}],
                    Parameters=[{"ParameterKey": k, "ParameterValue": v} for k, v in parameters.items()],
                    DisableRollback=True)
    print("app: recovery submitted", flush=True)
    while True:
        current = cf.describe_stacks(StackName=stack_id)["Stacks"][0]
        status = current["StackStatus"]
        print("app: " + status, flush=True)
        if not status.endswith("IN_PROGRESS"):
            break
        time.sleep(20)
    receipt["final_status"] = status
    if status != "UPDATE_COMPLETE":
        receipt["failures"] = [{"resource": e["LogicalResourceId"], "status": e["ResourceStatus"],
                                "reason": e.get("ResourceStatusReason", "")}
                               for e in cf.describe_stack_events(StackName=stack_id)["StackEvents"]
                               if "FAILED" in e["ResourceStatus"]]
        (evidence / "receipt.json").write_text(json.dumps(receipt, indent=2))
        raise RuntimeError("Stack recovery incomplete: " + status)
    outputs = {o["OutputKey"]: o["OutputValue"] for o in current.get("Outputs", [])}
    target.save("app", {"stackId": current["StackId"], "status": status, "outputs": outputs})
    (evidence / "receipt.json").write_text(json.dumps(receipt, indent=2))
    print(json.dumps({"pass": True, **receipt}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reconciled-create-failed-stack-id", required=True,
                        help="Exact StackId of the operator-reviewed CREATE_FAILED app stack")
    parser.add_argument("--evidence", required=True)
    target_arguments(parser)
    run(parser.parse_args())
