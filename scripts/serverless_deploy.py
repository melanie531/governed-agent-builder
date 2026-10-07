"""Target-bound bootstrap and code deployment; preserve enabled platform features."""
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
from infra.resource_tags import validate_resource_tags
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
    validate_resource_tags(body["Resources"])


def preflight():
    if TARGET is None:
        raise RuntimeError("Explicit target initialization required")
    TARGET.check_stacks()
    body = template(journey=TARGET.state.get("journeyPlatform"))
    safety(body)
    if TARGET.state.get("app"):
        previous = CF.get_template(StackName=TARGET.state["app"]["stackId"])["TemplateBody"]
        previous = json.loads(previous) if isinstance(previous, str) else previous
        if previous != body:
            raise RuntimeError(
                "Live application template differs from the target configuration. "
                "Restore the complete bound state or use a scoped, reviewed infrastructure release.")
    for name, current in (("artifacts", artifacts_template()), ("app", body)):
        CF.validate_template(TemplateBody=json.dumps(current, separators=(",", ":")))
    save("preflight", {"accountMatch": True, "noVpcDependencies": True, "cloudFormationValidated": True, "time": time.time()})
    print("Preflight PASS: explicit STS account and bound stack identities, no VPC dependency, both templates AWS-validated", flush=True)


def template_input(name, body):
    content = json.dumps(body, separators=(",", ":")).encode()
    if len(content) <= 51200:
        return {"TemplateBody": content.decode()}
    bucket = TARGET.state["artifacts"]["outputs"]["Bucket"]
    key = "templates/" + PREFIX + "-" + name + "/" + hashlib.sha256(content).hexdigest() + ".json"
    uploaded = SESSION.client("s3").put_object(
        Bucket=bucket, Key=key, Body=content, ServerSideEncryption="AES256",
        ContentType="application/json", Tagging="auto-delete=no")
    from urllib.parse import quote
    return {"TemplateURL": f"https://{bucket}.s3.{TARGET.binding['region']}.amazonaws.com/{key}?versionId="
            + quote(uploaded["VersionId"], safe="")}


def deploy(name, body, parameters=None):
    if TARGET is None:
        raise RuntimeError("Explicit target initialization required")
    TARGET.check_stacks()
    safety(body)
    stack_name = PREFIX + "-" + name
    request = {"StackName": stack_name, **template_input(name, body), "Capabilities": ["CAPABILITY_IAM"], "Tags": [{"Key": "project", "Value": "governed-agent-builder"}, {"Key": "architecture", "Value": "managed-serverless"}, {"Key": "auto-delete", "Value": "no"}], "Parameters": [{"ParameterKey": k, "ParameterValue": v} for k,v in (parameters or {}).items()]}
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



def main(action):
    if TARGET is None:
        raise RuntimeError("Explicit target initialization required")
    TARGET.check_stacks()
    if action == "preflight": preflight()
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
        outputs = deploy("app", template(journey=state.get("journeyPlatform")),
                         {"ArtifactBucket": bucket, "ArtifactKey": key})
        save("releaseSha256", sha)
        from backend.dynamo_store import DynamoStore
        DynamoStore(outputs["StateTable"], SESSION.resource("dynamodb")).initialize()
        print("DynamoDB catalog initialized without demo personas", flush=True)
    elif action == "publish":
        outputs = TARGET.state["app"]["outputs"]
        dist = ROOT / "frontend/dist"
        if not (dist / "index.html").is_file():
            raise RuntimeError("Build the frontend before publication")
        for path in sorted(dist.rglob("*"), key=lambda p: (p == dist / "index.html", p)):
            if path.is_file():
                SESSION.client("s3").upload_file(str(path), outputs["FrontendBucket"], str(path.relative_to(ROOT / "frontend/dist")), ExtraArgs={"ServerSideEncryption": "AES256", "ContentType": mimetypes.guess_type(path.name)[0] or "application/octet-stream", "CacheControl": "no-cache" if path.name == "index.html" else "public,max-age=31536000,immutable"})
        cloudfront = SESSION.client("cloudfront")
        invalidation = cloudfront.create_invalidation(
            DistributionId=outputs["DistributionId"], InvalidationBatch={
                "Paths": {"Quantity": 1, "Items": ["/*"]}, "CallerReference": "gab-" + str(time.time_ns())})
        save("frontendPublication", {"invalidationId": invalidation["Invalidation"]["Id"],
                                    "indexSha256": hashlib.sha256((dist / "index.html").read_bytes()).hexdigest()})
        for _ in range(120):
            status = cloudfront.get_invalidation(
                DistributionId=outputs["DistributionId"], Id=invalidation["Invalidation"]["Id"])["Invalidation"]["Status"]
            if status == "Completed":
                break
            print("CloudFront publication pending", flush=True)
            time.sleep(5)
        else:
            raise RuntimeError("CloudFront invalidation pending; inspect frontendPublication in the target state")
        print("Frontend published: " + outputs["ApplicationOrigin"], flush=True)
    elif action == "status":
        for name in ("artifacts", "app"):
            stack = CF.describe_stacks(StackName=PREFIX+"-"+name)["Stacks"][0]
            print(name + ": " + stack["StackStatus"])
            for output in stack.get("Outputs", []):
                if output["OutputKey"] in ("ApplicationOrigin", "ApiEndpoint"): print(output["OutputKey"]+": "+output["OutputValue"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("action", choices=["preflight", "artifacts", "deploy", "publish", "status"])
    target_arguments(parser)
    args = parser.parse_args()
    try:
        TARGET = DeploymentTarget(args.expected_account, args.profile, args.region, args.state)
        SESSION, CF, STATE = TARGET.session, TARGET.cf, TARGET.path
        main(args.action)
    except ClientError as exc:
        print("AWS operation failed: " + exc.response["Error"]["Code"], flush=True)
        sys.exit(1)
