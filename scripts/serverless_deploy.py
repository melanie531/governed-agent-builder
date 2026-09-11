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

import boto3
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from infra.serverless import artifacts_template, template

PREFIX = "governed-agent-builder-serverless"
STATE = ROOT / "artifacts/serverless-deployment.json"
SESSION = boto3.Session(profile_name="agentic-platform-prod", region_name="us-west-2")
CF = SESSION.client("cloudformation")


def save(key, value):
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    state[key] = value
    STATE.write_text(json.dumps(state, indent=2))


def safety(body):
    forbidden = ("AWS::EC2::", "AWS::CloudFront::VpcOrigin")
    for resource in body["Resources"].values():
        if resource["Type"].startswith(forbidden): raise RuntimeError("VPC-dependent resource prohibited")
        if resource["Type"] == "AWS::Lambda::Function" and "VpcConfig" in resource["Properties"]: raise RuntimeError("VPC configuration prohibited")
    text = json.dumps(body)
    if "AdministratorAccess" in text or "BlockPublicAccessExclusion" in text: raise RuntimeError("Prohibited permission or exception")


def preflight():
    # Compare against the approved profile's existing project stack ownership,
    # internally only. Never print account identifiers or full AWS exceptions.
    identity = SESSION.client("sts").get_caller_identity()
    prior = CF.describe_stacks(StackName="governed-agent-builder-network")["Stacks"][0]
    if prior["StackId"].split(":")[4] != identity["Account"]: raise RuntimeError("Account ownership mismatch")
    if not any(t["Key"] == "project" and t["Value"] == "governed-agent-builder" for t in prior.get("Tags", [])): raise RuntimeError("Project ownership mismatch")
    bpa = SESSION.client("ec2").describe_vpc_block_public_access_options()["VpcBlockPublicAccessOptions"]
    if bpa["InternetGatewayBlockMode"] != "block-ingress": raise RuntimeError("BPA changed; owner review required")
    body = template(); safety(body)
    for name, current in (("artifacts", artifacts_template()), ("app", body)):
        CF.validate_template(TemplateBody=json.dumps(current))
    save("preflight", {"accountMatch": True, "bpa": "block-ingress", "noVpcDependencies": True, "cloudFormationValidated": True, "time": time.time()})
    print("Preflight PASS: approved account ownership, BPA unchanged, no VPC dependency, both templates AWS-validated", flush=True)


def deploy(name, body, parameters=None):
    safety(body)
    stack_name = PREFIX + "-" + name
    request = {"StackName": stack_name, "TemplateBody": json.dumps(body), "Capabilities": ["CAPABILITY_IAM"], "Tags": [{"Key": "project", "Value": "governed-agent-builder"}, {"Key": "architecture", "Value": "managed-serverless"}], "Parameters": [{"ParameterKey": k, "ParameterValue": v} for k,v in (parameters or {}).items()]}
    try:
        prior = CF.describe_stacks(StackName=stack_name)["Stacks"][0]
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ValidationError": raise
        prior = None
    if prior:
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
    save(name, {"status": status, "outputs": {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}})
    print(stack_name + ": " + status, flush=True)
    if status not in ("CREATE_COMPLETE", "UPDATE_COMPLETE"):
        failures = [{"resource": e["LogicalResourceId"], "status": e["ResourceStatus"], "reason": e.get("ResourceStatusReason", "")} for e in CF.describe_stack_events(StackName=stack_name)["StackEvents"] if "FAILED" in e["ResourceStatus"]]
        # Save service diagnostics locally only for targeted redacted inspection.
        (ROOT / "artifacts/serverless-failures.json").write_text(json.dumps(failures, indent=2))
        raise RuntimeError("Stack incomplete; inspect local redacted failure report")
    return {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}


def main(action):
    if action == "preflight": preflight()
    elif action == "artifacts":
        preflight(); deploy("artifacts", artifacts_template())
    elif action == "deploy":
        preflight()
        state = json.loads(STATE.read_text())
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
        outputs = json.loads(STATE.read_text())["app"]["outputs"]
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
    parser = argparse.ArgumentParser(); parser.add_argument("action", choices=["preflight", "artifacts", "deploy", "publish", "status"])
    try: main(parser.parse_args().action)
    except ClientError as exc:
        print("AWS operation failed: " + exc.response["Error"]["Code"], flush=True)
        sys.exit(1)
