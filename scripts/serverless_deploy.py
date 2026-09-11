"""Authorized deployment of separate managed stacks. No old-stack mutations.

Progress/status output contains no account IDs, credentials or auth responses.
Run with uv run python scripts/serverless_deploy.py preflight|artifacts|deploy|publish|status.
"""
import argparse
import hashlib
import json
import mimetypes
from pathlib import Path
import sys
import time

from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from infra.serverless import artifacts_template, template
from scripts.deployment_target import DeploymentTarget, target_arguments

PREFIX = "governed-agent-builder-serverless"
STATE = None
SESSION = None
CF = None
TARGET = None


def save(key, value):
    TARGET.save(key, value)


def safety(body):
    forbidden = ("AWS::EC2::", "AWS::CloudFront::VpcOrigin")
    for resource in body["Resources"].values():
        if resource["Type"].startswith(forbidden): raise RuntimeError("VPC-dependent resource prohibited")
        if resource["Type"] == "AWS::Lambda::Function" and "VpcConfig" in resource["Properties"]: raise RuntimeError("VPC configuration prohibited")
    text = json.dumps(body)
    if "AdministratorAccess" in text or "BlockPublicAccessExclusion" in text: raise RuntimeError("Prohibited permission or exception")


def preflight():
    if TARGET is None:
        raise RuntimeError("Explicit target initialization required")
    TARGET.check_stacks()
    body = template(); safety(body)
    for name, current in (("artifacts", artifacts_template()), ("app", body)):
        CF.validate_template(TemplateBody=json.dumps(current))
    save("preflight", {"accountMatch": True, "noVpcDependencies": True, "cloudFormationValidated": True, "time": time.time()})
    print("Preflight PASS: explicit STS account and bound stack identities, no VPC dependency, both templates AWS-validated", flush=True)


def deploy(name, body, parameters=None):
    if TARGET is None:
        raise RuntimeError("Explicit target initialization required")
    TARGET.check_stacks()
    safety(body)
    stack_name = PREFIX + "-" + name
    request = {"StackName": stack_name, "TemplateBody": json.dumps(body), "Capabilities": ["CAPABILITY_IAM"], "Tags": [{"Key": "project", "Value": "governed-agent-builder"}, {"Key": "architecture", "Value": "managed-serverless"}], "Parameters": [{"ParameterKey": k, "ParameterValue": v} for k,v in (parameters or {}).items()]}
    try:
        prior = CF.describe_stacks(StackName=stack_name)["Stacks"][0]
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ValidationError" or "does not exist" not in exc.response["Error"].get("Message", ""): raise
        prior = None
    if prior:
        TARGET.check_stack(prior)
        if not any(t["Key"] == "architecture" and t["Value"] == "managed-serverless" for t in prior.get("Tags", [])): raise RuntimeError("Refusing unowned stack")
        if prior["StackStatus"].endswith("IN_PROGRESS"):
            raise RuntimeError("Existing stack operation still running; inspect and wait, do not submit twice")
        if prior["StackStatus"] in ("CREATE_FAILED", "UPDATE_FAILED"):
            request["DisableRollback"] = True
        try: CF.update_stack(**request)
        except ClientError as exc:
            if "No updates are to be performed" not in exc.response["Error"]["Message"]: raise
    else:
        CF.create_stack(**request, DisableRollback=True)
    print(stack_name + ": submitted", flush=True)
    while True:
        stack = CF.describe_stacks(StackName=stack_name)["Stacks"][0]
        status = stack["StackStatus"]
        if not status.endswith("IN_PROGRESS"): break
        time.sleep(20)
    TARGET.check_stack(stack)
    save(name, {"stackId": stack["StackId"], "status": status, "outputs": {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}})
    print(stack_name + ": " + status, flush=True)
    if status not in ("CREATE_COMPLETE", "UPDATE_COMPLETE"):
        failures = [{"resource": e["LogicalResourceId"], "status": e["ResourceStatus"], "reason": e.get("ResourceStatusReason", "")} for e in CF.describe_stack_events(StackName=stack_name)["StackEvents"] if "FAILED" in e["ResourceStatus"]]
        # Save service diagnostics locally only for targeted redacted inspection.
        (ROOT / "artifacts/serverless-failures.json").write_text(json.dumps(failures, indent=2))
        raise RuntimeError("Stack incomplete; inspect local redacted failure report")
    return {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}


def review_verification_template(previous, proposed):
    """Constrain both direct template changes and subsequent CF evaluated changes."""
    old, new = previous["Resources"], proposed["Resources"]
    additions = {"Verification", "Route5", "Route6", "Route7", "AuthPermission3", "AuthPermission4", "AuthPermission5"}
    if set(old)-set(new) or set(new)-set(old) != additions:
        raise RuntimeError("Unexpected resource addition/removal")
    for name, original in old.items():
        expected = json.loads(json.dumps(original))
        if name == "Client":
            expected["Properties"]["AllowedOAuthScopes"].append("aws.cognito.signin.user.admin")
        elif name == "Auth":
            expected["Properties"]["Environment"]["Variables"]["VERIFICATION_TABLE"] = {"Ref": "Verification"}
        elif name == "AuthRole":
            expected["Properties"]["Policies"][0]["PolicyDocument"]["Statement"].append({
                "Effect": "Allow", "Action": ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem"],
                "Resource": {"Fn::GetAtt": ["Verification", "Arn"]}})
        if expected != new[name]:
            raise RuntimeError("Unexpected template change: " + name)
    for field in set(previous) | set(proposed):
        if field != "Resources" and previous.get(field) != proposed.get(field):
            raise RuntimeError("Unexpected template section change: " + field)


def review_verification_changes(changes, existing):
    allowed = {"AuthRole", "Client", "Business", "Auth", "Authorizer", "Worker", "Dispatcher",
        "Verification", "Route5", "Route6", "Route7", "AuthPermission3", "AuthPermission4", "AuthPermission5"}
    dependencies = {"AuthIntegration": ("Auth.Arn", "IntegrationUri"),
        "BusinessIntegration": ("Business.Arn", "IntegrationUri"),
        "SessionAuthorizer": ("Authorizer.Arn", "AuthorizerUri")}
    if not changes: raise RuntimeError("Empty change set")
    for change in changes:
        r = change["ResourceChange"]
        name = r["LogicalResourceId"]
        if name in dependencies:
            cause, field = dependencies[name]
            if not r.get("Details") or any(d.get("ChangeSource") != "ResourceAttribute" or d.get("CausingEntity") != cause
                    or d["Target"].get("Name") != field or d["Target"].get("RequiresRecreation") != "Never" for d in r["Details"]):
                raise RuntimeError("Unexpected integration/authorizer change")
        if name not in allowed | dependencies.keys() or r["Action"] not in ("Add", "Modify"):
            raise RuntimeError("Unexpected change set resource/action")
        if r["LogicalResourceId"] in existing and (r["Action"] != "Modify" or r.get("Replacement") != "False"):
            raise RuntimeError("Existing resource replacement prohibited")


def verification_deploy():
    """Update only the owned serverless app through a fully evaluated change set."""
    if TARGET is None:
        raise RuntimeError("Explicit target initialization required")
    TARGET.check_stacks()
    stack_name = PREFIX + "-app"
    stack = CF.describe_stacks(StackName=stack_name)["Stacks"][0]
    TARGET.check_stack(stack)
    tags = {t["Key"]: t["Value"] for t in stack.get("Tags", [])}
    if tags.get("project") != "governed-agent-builder" or tags.get("architecture") != "managed-serverless":
        raise RuntimeError("Unowned app stack")
    if stack["StackStatus"] not in ("CREATE_COMPLETE", "UPDATE_COMPLETE"):
        raise RuntimeError("App stack is not ready for an update")
    state = TARGET.state
    outputs = {o["OutputKey"]: o["OutputValue"] for o in stack["Outputs"]}
    if outputs != state["app"]["outputs"]:
        raise RuntimeError("Unexpected app identity")
    previous = CF.get_template(StackName=stack_name, TemplateStage="Original")["TemplateBody"]
    if isinstance(previous, str): previous = json.loads(previous)
    body = template(); safety(body); review_verification_template(previous, body)
    CF.validate_template(TemplateBody=json.dumps(body))
    parameters = {p["ParameterKey"]: p["ParameterValue"] for p in stack["Parameters"]}
    if parameters["ArtifactBucket"] != state["artifacts"]["outputs"]["Bucket"]:
        raise RuntimeError("Unexpected release bucket")
    package = ROOT / "artifacts/serverless-release.zip"
    sha = hashlib.sha256(package.read_bytes()).hexdigest()
    artifact_key = "releases/" + sha + "/lambda.zip"
    SESSION.client("s3").upload_file(str(package), parameters["ArtifactBucket"], artifact_key, ExtraArgs={"ServerSideEncryption": "AES256"})
    name = "email-verification-" + str(time.time_ns())
    CF.create_change_set(StackName=stack_name, ChangeSetName=name, ChangeSetType="UPDATE", TemplateBody=json.dumps(body),
        Capabilities=["CAPABILITY_IAM"], Parameters=[{"ParameterKey": "ArtifactBucket", "UsePreviousValue": True}, {"ParameterKey": "ArtifactKey", "ParameterValue": artifact_key}])
    while True:
        change = CF.describe_change_set(StackName=stack_name, ChangeSetName=name)
        if change["Status"] not in ("CREATE_PENDING", "CREATE_IN_PROGRESS"): break
        time.sleep(3)
    if change["Status"] != "CREATE_COMPLETE" or change.get("NextToken"):
        raise RuntimeError("Change set unavailable or incomplete; not executed")
    review_verification_changes(change["Changes"], previous["Resources"])
    summary = [{k: c["ResourceChange"].get(k) for k in ("LogicalResourceId", "Action", "Replacement")} for c in change["Changes"]]
    print("Reviewed changes: " + json.dumps(summary), flush=True)
    Path("/tmp/gab-verification-changes.json").write_text(json.dumps(summary, indent=2))
    CF.execute_change_set(StackName=stack_name, ChangeSetName=name)
    print("Reviewed change set executed", flush=True)
    while True:
        stack = CF.describe_stacks(StackName=stack_name)["Stacks"][0]
        status = stack["StackStatus"]
        print("App stack: " + status, flush=True)
        if not status.endswith("IN_PROGRESS"): break
        time.sleep(15)
    if status != "UPDATE_COMPLETE": raise RuntimeError("App update did not complete")
    after = {o["OutputKey"]: o["OutputValue"] for o in stack["Outputs"]}
    if after != outputs: raise RuntimeError("Unexpected output identity change")
    save("releaseSha256", sha)
    save("app", {"stackId": stack["StackId"], "status": status, "outputs": after})
    print("Verification update complete; Pool and all output identities unchanged", flush=True)

def main(action):
    if TARGET is None:
        raise RuntimeError("Explicit target initialization required")
    TARGET.check_stacks()
    if action == "verification-deploy": verification_deploy()
    elif action == "preflight": preflight()
    elif action == "artifacts":
        preflight(); deploy("artifacts", artifacts_template())
    elif action == "deploy":
        preflight()
        state = TARGET.state
        bucket = state["artifacts"]["outputs"]["Bucket"]
        package = ROOT / "artifacts/serverless-release.zip"
        sha = hashlib.sha256(package.read_bytes()).hexdigest()
        key = "releases/" + sha + "/lambda.zip"
        SESSION.client("s3").upload_file(str(package), bucket, key, ExtraArgs={"ServerSideEncryption": "AES256"})
        save("releaseSha256", sha)
        outputs = deploy("app", template(), {"ArtifactBucket": bucket, "ArtifactKey": key})
        from backend.dynamo_store import DynamoStore
        DynamoStore(outputs["StateTable"], SESSION.resource("dynamodb")).initialize()
        print("DynamoDB catalog initialized without demo personas", flush=True)
    elif action == "publish":
        outputs = TARGET.state["app"]["outputs"]
        for path in sorted((ROOT / "frontend/dist").rglob("*")):
            if path.is_file():
                SESSION.client("s3").upload_file(str(path), outputs["FrontendBucket"], str(path.relative_to(ROOT / "frontend/dist")), ExtraArgs={"ServerSideEncryption": "AES256", "ContentType": mimetypes.guess_type(path.name)[0] or "application/octet-stream", "CacheControl": "no-cache" if path.name == "index.html" else "public,max-age=31536000,immutable"})
        SESSION.client("cloudfront").create_invalidation(DistributionId=outputs["DistributionId"], InvalidationBatch={"Paths": {"Quantity": 1, "Items": ["/*"]}, "CallerReference": "gab-" + str(time.time_ns())})
        print("Frontend published: " + outputs["ApplicationOrigin"], flush=True)
    elif action == "status":
        for name in ("artifacts", "app"):
            stack = CF.describe_stacks(StackName=PREFIX+"-"+name)["Stacks"][0]
            print(name + ": " + stack["StackStatus"])
            for output in stack.get("Outputs", []):
                if output["OutputKey"] in ("ApplicationOrigin", "ApiEndpoint"): print(output["OutputKey"]+": "+output["OutputValue"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("action", choices=["preflight", "artifacts", "deploy", "publish", "status", "verification-deploy"])
    target_arguments(parser)
    args = parser.parse_args()
    try:
        TARGET = DeploymentTarget(args.expected_account, args.profile, args.region, args.state)
        SESSION, CF, STATE = TARGET.session, TARGET.cf, TARGET.path
        main(args.action)
    except ClientError as exc:
        print("AWS operation failed: " + exc.response["Error"]["Code"], flush=True)
        sys.exit(1)
