"""Scoped reviewed release: PlatformAdministration GetInferenceProfile statement + combined code update.

Template change allowed: exactly one statement added to the BusinessRole inline
policy "PlatformAdministration" (bedrock:GetInferenceProfile on the account's
inference profiles). Code change: the reviewed combined Lambda package.
Everything else must match the live template byte for byte. Evaluated change
sets may additionally contain ONLY the benign reference cascade CloudFormation
emits for a Lambda code update: ApiGatewayV2 Integration/Authorizer "Modify"
entries whose every Detail is a ResourceAttribute change caused by one of the
reviewed functions with RequiresRecreation=Never. Anything else fails closed.

Portable inputs (no absolute paths or account literals in this file):
  --expected-account  12-digit target account; verified against STS before any write
  --profile           operator-approved AWS SDK profile (credentials stay in the
                      shared AWS config/keychain; never passed on the command line)
  --region            target region, e.g. us-west-2
  --state             fresh target-specific local JSON state path (see
                      scripts/deployment_target.py; legacy state names are refused)
  --release-sha256    sha256 of the reviewed artifacts/serverless-release.zip
  --evidence          directory for template-before/proposed, change-set and receipt

Run from a checkout: python scripts/scoped_global_policy_release.py ...
"""
import argparse
import base64
import copy
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from infra.serverless import template  # noqa: E402
from infra.resource_tags import validate_resource_tags  # noqa: E402
from scripts.deployment_target import DeploymentTarget, target_arguments  # noqa: E402

FUNCTIONS = {"Worker", "Dispatcher", "Auth", "Authorizer", "Business", "FoundationExchange"}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def reviewed_template_change(before, after, account):
    expected = copy.deepcopy(before)
    policies = expected["Resources"]["BusinessRole"]["Properties"]["Policies"]
    policy = next(p for p in policies if p["PolicyName"] == "PlatformAdministration")
    statements = policy["PolicyDocument"]["Statement"]
    addition = {"Effect": "Allow", "Action": ["bedrock:GetInferenceProfile"],
                "Resource": [f"arn:aws:bedrock:*:{account}:inference-profile/*"]}
    if addition in statements:
        raise ValueError("Live policy already contains the reviewed statement")
    anchor = next(i for i, s in enumerate(statements) if "bedrock:InvokeModel" in s.get("Action", []))
    statements.insert(anchor, addition)
    if canonical(expected) != canonical(after):
        raise ValueError("Proposed template changes more than the exact reviewed IAM statement")
    return True


def reviewed_evaluated_changes(change):
    if change["Status"] != "CREATE_COMPLETE" or change.get("NextToken"):
        raise ValueError("Change set is not complete")
    allowed = {"BusinessRole"} | FUNCTIONS
    cascade_types = {"AWS::ApiGatewayV2::Integration", "AWS::ApiGatewayV2::Authorizer"}
    seen = set()
    for entry in change.get("Changes", []):
        item = entry["ResourceChange"]
        name = item["LogicalResourceId"]
        if item["Action"] != "Modify" or item.get("Replacement") not in (None, "False"):
            raise ValueError("Unreviewed change: " + json.dumps(item)[:200])
        if name in allowed:
            seen.add(name)
            continue
        if item.get("ResourceType") in cascade_types and all(
                d.get("ChangeSource") == "ResourceAttribute"
                and str(d.get("CausingEntity", "")).split(".")[0] in FUNCTIONS
                and d.get("Target", {}).get("RequiresRecreation") == "Never"
                for d in item.get("Details", [])) and item.get("Details"):
            continue
        raise ValueError("Unreviewed change: " + json.dumps(item)[:200])
    if "BusinessRole" not in seen or not seen & FUNCTIONS:
        raise ValueError("Change set is missing the reviewed role or code updates")
    return sorted(seen)


def run(args):
    target = DeploymentTarget(args.expected_account, args.profile, args.region, args.state)
    cf = target.session.client("cloudformation")
    state = target.state
    stack_id = state["app"]["stackId"]
    stack = cf.describe_stacks(StackName=stack_id)["Stacks"][0]
    if stack["StackStatus"] not in ("CREATE_COMPLETE", "UPDATE_COMPLETE"):
        raise RuntimeError("Existing stack is not in a terminal success state")
    previous = cf.get_template(StackName=stack_id)["TemplateBody"]
    previous = json.loads(previous) if isinstance(previous, str) else previous
    body = template(journey=state["journeyPlatform"])
    validate_resource_tags(body["Resources"])
    reviewed_template_change(previous, body, args.expected_account)

    package = ROOT / "artifacts/serverless-release.zip"
    content = package.read_bytes()
    sha = hashlib.sha256(content).hexdigest()
    if sha != args.release_sha256:
        raise ValueError("Local package is not the reviewed artifact")
    bucket = state["artifacts"]["outputs"]["Bucket"]
    key = "releases/" + sha + "/lambda.zip"
    s3 = target.session.client("s3")
    s3.put_object(Bucket=bucket, Key=key, Body=content, ServerSideEncryption="AES256",
                  Tagging="auto-delete=no")

    evidence = Path(args.evidence).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "template-before.json").write_text(json.dumps(previous, indent=2))
    (evidence / "template-proposed.json").write_text(json.dumps(body, indent=2))

    content_body = json.dumps(body, separators=(",", ":")).encode()
    if len(content_body) <= 51200:
        template_request = {"TemplateBody": content_body.decode()}
    else:
        tkey = "templates/scoped-global-policy/" + hashlib.sha256(content_body).hexdigest() + ".json"
        uploaded = s3.put_object(Bucket=bucket, Key=tkey, Body=content_body, ServerSideEncryption="AES256",
                                 ContentType="application/json", Tagging="auto-delete=no")
        from urllib.parse import quote
        template_request = {"TemplateURL": f"https://{bucket}.s3.{args.region}.amazonaws.com/{tkey}?versionId="
                            + quote(uploaded["VersionId"], safe="")}
    name = "scoped-global-policy-" + sha[:16]
    try:
        cf.delete_change_set(StackName=stack_id, ChangeSetName=name)
        time.sleep(3)
    except Exception:
        pass
    cf.create_change_set(StackName=stack_id, ChangeSetName=name, ChangeSetType="UPDATE",
        **template_request, Capabilities=["CAPABILITY_IAM"],
        Parameters=[{"ParameterKey": "ArtifactBucket", "UsePreviousValue": True},
                    {"ParameterKey": "ArtifactKey", "ParameterValue": key}])
    for _ in range(60):
        change = cf.describe_change_set(StackName=stack_id, ChangeSetName=name)
        if change["Status"] not in ("CREATE_PENDING", "CREATE_IN_PROGRESS"):
            break
        time.sleep(3)
    changed = reviewed_evaluated_changes(change)
    (evidence / "change-set.json").write_text(json.dumps(change, indent=2, default=str))
    cf.execute_change_set(StackName=stack_id, ChangeSetName=name)
    for _ in range(90):
        current = cf.describe_stacks(StackName=stack_id)["Stacks"][0]
        print("app: " + current["StackStatus"], flush=True)
        if not current["StackStatus"].endswith("IN_PROGRESS"):
            break
        time.sleep(10)
    if current["StackStatus"] != "UPDATE_COMPLETE":
        raise RuntimeError("Stack update incomplete: " + current["StackStatus"])
    outputs = {o["OutputKey"]: o["OutputValue"] for o in current["Outputs"]}
    if outputs != state["app"]["outputs"]:
        raise ValueError("Application identity changed")
    physical = {r["LogicalResourceId"]: r["PhysicalResourceId"]
                for r in cf.list_stack_resources(StackName=stack_id)["StackResourceSummaries"]}
    lam = target.session.client("lambda")
    expected64 = base64.b64encode(bytes.fromhex(sha)).decode()
    for logical in FUNCTIONS:
        live = lam.get_function_configuration(FunctionName=physical[logical])
        if live["CodeSha256"] != expected64 or live["State"] != "Active" or live["LastUpdateStatus"] != "Successful":
            raise RuntimeError(logical + " did not reach the reviewed release")
    target.save("releaseSha256", sha)
    target.save("app", {"stackId": stack_id, "status": current["StackStatus"], "outputs": outputs})
    receipt = {"release_sha256": sha, "changed": changed, "change_set": name, "time": time.time()}
    (evidence / "receipt.json").write_text(json.dumps(receipt, indent=2))
    print(json.dumps({"pass": True, **receipt}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-sha256", required=True)
    parser.add_argument("--evidence", required=True)
    target_arguments(parser)
    run(parser.parse_args())
