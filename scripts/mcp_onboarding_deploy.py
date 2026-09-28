"""Release generic MCP onboarding into the existing, explicitly bound Studio."""
import argparse
import copy
import hashlib
import json
import mimetypes
import re
import time
from pathlib import Path

from botocore.config import Config

from backend.dynamo_store import DynamoStore
from backend.foundation_runs import get, put
from foundation_harness.config import digest
from infra.resource_tags import validate_resource_tags
from infra.serverless import template
from scripts.deployment_target import DeploymentTarget, target_arguments

ROOT = Path(__file__).resolve().parents[1]


def evidence_name(value):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", value):
        raise argparse.ArgumentTypeError("Use a release name of lowercase letters, digits and hyphens")
    return value


def review_existing_ui_change(previous, proposed):
    """Update onboarding and retire creator access; preserve unrelated resources."""
    expected = copy.deepcopy(previous)
    proposed = copy.deepcopy(proposed)
    if any(p["PolicyName"] == "McpCreation"
           for p in proposed["Resources"]["WorkerRole"]["Properties"]["Policies"]):
        raise ValueError("Remote MCP provisioning permissions have been retired")
    existing_worker = expected["Resources"]["WorkerRole"]["Properties"]["Policies"]
    existing_worker[:] = [p for p in existing_worker if p["PolicyName"] != "McpCreation"]
    for role, name in (("WorkerRole", "McpOnboarding"), ("BusinessRole", "McpRegistryRead"), ("BusinessRole", "McpCredentialSetup")):
        before = expected["Resources"][role]["Properties"]["Policies"]
        additions = [p for p in proposed["Resources"][role]["Properties"]["Policies"] if p["PolicyName"] == name]
        if not additions and name == "McpCredentialSetup":
            continue
        if len(additions) != 1:
            raise ValueError("Expected one onboarding policy")
        before[:] = [p for p in before if p["PolicyName"] != name] + additions
        before.sort(key=lambda p: p["PolicyName"])
        proposed["Resources"][role]["Properties"]["Policies"].sort(key=lambda p: p["PolicyName"])
    if expected != proposed:
        raise ValueError("Unrelated existing application change")


def review_evaluated_changes(changes, resources):
    allowed = {"BusinessRole", "WorkerRole"} | {
        k for k, r in resources.items() if r["Type"] == "AWS::Lambda::Function"}
    dependencies = {
        "AuthIntegration": ("Auth.Arn", "IntegrationUri"),
        "BusinessIntegration": ("Business.Arn", "IntegrationUri"),
        "FoundationExchangeIntegration": ("FoundationExchange.Arn", "IntegrationUri"),
        "SessionAuthorizer": ("Authorizer.Arn", "AuthorizerUri")}
    result = []
    for entry in changes:
        r = entry["ResourceChange"]
        name = r["LogicalResourceId"]
        if name in dependencies:
            cause, field = dependencies[name]
            if not r.get("Details") or any(
                d.get("ChangeSource") != "ResourceAttribute" or d.get("CausingEntity") != cause
                or d["Target"].get("Name") != field or d["Target"].get("RequiresRecreation") != "Never"
                for d in r["Details"]):
                raise ValueError("Unexpected endpoint dependency change")
        if name not in allowed | dependencies.keys() or r["Action"] != "Modify" or r.get("Replacement") != "False":
            raise ValueError("Unexpected evaluated change set resource")
        result.append({k: r.get(k) for k in ("LogicalResourceId", "Action", "Replacement")})
    if not result:
        raise ValueError("Empty change set")
    return result


class Release:
    def __init__(self, args):
        self.evidence = ROOT / "artifacts" / evidence_name(getattr(args, "evidence_name", "mcp-onboarding-main"))
        self.evidence.mkdir(parents=True, exist_ok=True)
        self.path = self.evidence / "release-receipt.json"
        saved = json.loads(args.state.read_text())
        self.receipt = json.loads(self.path.read_text()) if self.path.exists() else {
            "authorization": "User explicitly requested the existing UI and removal of the preview",
            "stack_id": saved["app"]["stackId"], "operations": {}}
        recovery = self.receipt["stack_id"] if any(k.startswith("execute-") for k in self.receipt["operations"]) else None
        self.target = DeploymentTarget(args.expected_account, args.profile, args.region, args.state,
                                       reconciled_rollback_stack_id=recovery)
        self.state = self.target.state
        if self.receipt["stack_id"] != self.state["app"]["stackId"]:
            raise ValueError("Release receipt belongs to another application")
        self.clients = {}
        stack = self.client("cloudformation").describe_stacks(StackName=self.receipt["stack_id"])["Stacks"][0]
        if stack["StackStatus"] == "UPDATE_ROLLBACK_COMPLETE":
            observed = self.client("cloudformation").get_template(StackName=stack["StackId"])["TemplateBody"]
            observed = json.loads(observed) if isinstance(observed, str) else observed
            if (observed != json.loads((self.evidence / "template-before.json").read_text())
                    or stack["Parameters"] != self.receipt.get("previous_artifact_parameters")):
                raise ValueError("Rollback did not restore the pinned original application")
            self.receipt["rollback_reconciled"] = True
            self.save()

    def client(self, name):
        if name not in self.clients:
            self.clients[name] = self.target.session.client(name, config=Config(
                retries={"total_max_attempts": 1}, connect_timeout=5, read_timeout=60))
        return self.clients[name]

    def save(self):
        self.path.write_text(json.dumps(self.receipt, indent=2, default=str) + "\n")
        self.path.chmod(0o600)

    def dispatch(self, name, request, call):
        previous = self.receipt["operations"].get(name)
        if previous:
            if previous["digest"] != digest(request):
                raise ValueError("Retained release request changed")
            return
        self.receipt["operations"][name] = {"digest": digest(request), "status": "INTENT", "time": time.time()}
        self.save()
        call()
        self.receipt["operations"][name]["status"] = "ACKNOWLEDGED"
        self.save()

    def registry(self):
        client = self.client("agent-registry-control")
        name = "governed-agent-builder-serverless"
        request = {"name": name, "description": "MCP servers reviewed and published through Agent Studio",
                   "discoveryConfiguration": {"authorizerType": "AWS_IAM"},
                   "tags": {"auto-delete": "no", "project": "governed-agent-builder", "purpose": "mcp-onboarding"}}
        def read():
            if self.state.get("mcpOnboardingRegistry"):
                return client.get_registry(registryId=self.state["mcpOnboardingRegistry"]["registryId"])
            token = None
            for _ in range(30):
                page = client.list_registries(**({"nextToken": token} if token else {}))
                found = [r for r in page["registries"] if r["name"] == name]
                if found:
                    if len(found) != 1:
                        raise ValueError("Ambiguous canonical Registry")
                    return client.get_registry(registryId=found[0]["registryId"])
                token = page.get("nextToken")
                if not token:
                    return None
            raise ValueError("Incomplete Registry inventory")
        existing = read()
        if existing and not self.state.get("mcpOnboardingRegistry") and "registry" not in self.receipt["operations"]:
            raise ValueError("Refusing unrecorded Registry adoption")
        self.dispatch("registry", request, lambda: client.create_registry(**request, clientToken=digest(request)))
        for _ in range(60):
            value = read()
            if value and value["status"] == "READY":
                break
            time.sleep(3)
        else:
            raise RuntimeError("Registry readiness pending; resume using the saved receipt")
        if value.get("approvalConfiguration", {}).get("autoApprovalRules"):
            raise ValueError("Registry must require explicit approval")
        tags = client.list_tags_for_resource(resourceArn=value["registryArn"])["tags"]
        if any(tags.get(k) != v for k, v in request["tags"].items()):
            raise ValueError("Canonical Registry ownership mismatch")
        self.target.save("mcpOnboardingRegistry", {k: value[k] for k in ("registryId", "registryArn")})
        print("Canonical Registry READY", flush=True)

    def configure(self):
        from backend.mcp_onboarding import configuration
        from scripts.deployment_target import PREFIX

        registry = self.state["mcpOnboardingRegistry"]
        existing = self.state["journeyPlatform"].get("mcp_onboarding")
        config = copy.deepcopy(existing) if existing is not None else {
            "enabled": True, "registry_id": registry["registryId"], "registry_arn": registry["registryArn"],
            "workspaces": ["research", "operations"], "secret_arns": [], "connections": [],
            "credential_prefix": PREFIX}
        configuration(config, self.state["journeyPlatform"])
        if (config["registry_id"] != registry["registryId"] or config["registry_arn"] != registry["registryArn"]):
            raise ValueError("Existing onboarding belongs to a different Registry")
        settings = {**self.state["journeyPlatform"], "mcp_onboarding": config}
        store = DynamoStore(self.state["app"]["outputs"]["StateTable"], self.target.session.resource("dynamodb"))
        with store.tx() as db:
            current = get(db, "journey-platform")
            if current != self.state["journeyPlatform"] and current != settings:
                raise ValueError("Current platform settings changed")
            prior = get(db, "mcp-onboarding")
            if prior is not None and prior != config:
                raise ValueError("Existing MCP onboarding configuration differs")
            put(db, "journey-platform", settings)
            put(db, "mcp-onboarding", config)
        self.target.save("journeyPlatform", settings)
        print("Configured onboarding on the existing Gateway and data store", flush=True)

    def deploy(self):
        cf = self.client("cloudformation")
        stack_id = self.state["app"]["stackId"]
        stack = cf.describe_stacks(StackName=stack_id)["Stacks"][0]
        previous = cf.get_template(StackName=stack_id)["TemplateBody"]
        previous = json.loads(previous) if isinstance(previous, str) else previous
        body = template(journey=self.state["journeyPlatform"])
        review_existing_ui_change(previous, body)
        validate_resource_tags(body["Resources"])
        sizes = {name: sum(len(json.dumps(p["PolicyDocument"], separators=(",", ":")))
                           for p in r["Properties"].get("Policies", []))
                 for name, r in body["Resources"].items() if r["Type"] == "AWS::IAM::Role"}
        physical = {r["LogicalResourceId"]: r["PhysicalResourceId"] for r in cf.list_stack_resources(
            StackName=stack_id)["StackResourceSummaries"]}
        # Count the resulting policies: the old creator policy is removed by
        # this reviewed change set, so it must not inflate the post-update size.
        for role, names in (("WorkerRole", {"McpOnboarding", "McpCreation"}), ("BusinessRole", {"McpRegistryRead", "McpCredentialSetup"})):
            iam = self.client("iam")
            existing = [iam.get_role_policy(RoleName=physical[role], PolicyName=n)["PolicyDocument"]
                        for n in iam.list_role_policies(RoleName=physical[role])["PolicyNames"] if n not in names]
            additions = [p["PolicyDocument"] for p in body["Resources"][role]["Properties"]["Policies"] if p["PolicyName"] in names]
            sizes[role] = sum(len(json.dumps(p, separators=(",", ":"))) for p in [*existing, *additions])
        if max(sizes.values()) >= 10240 or "AWS::Lambda::Url" in json.dumps(body):
            raise ValueError("Infrastructure preflight failed")
        cf.validate_template(TemplateBody=json.dumps(body))
        if not (self.evidence / "template-before.json").exists():
            (self.evidence / "template-before.json").write_text(json.dumps(previous, indent=2))
            (self.evidence / "stack-before.json").write_text(json.dumps(stack, indent=2, default=str))
        (self.evidence / "template-proposed.json").write_text(json.dumps(body, indent=2))
        (self.evidence / "preflight.json").write_text(json.dumps({
            "retention_tags": True, "no_function_urls": True, "inline_policy_bytes": sizes,
            "only_mcp_policies_changed": True,
            "retired_creator_policy_removed": any(p["PolicyName"] == "McpCreation"
                for p in previous["Resources"]["WorkerRole"]["Properties"]["Policies"])}, indent=2))
        package = (ROOT / "artifacts/serverless-release.zip").read_bytes()
        sha = hashlib.sha256(package).hexdigest()
        bucket = self.state["artifacts"]["outputs"]["Bucket"]
        key = "releases/" + sha + "/lambda.zip"
        self.dispatch("artifact-" + sha, {"bucket": bucket, "key": key, "sha256": sha},
                      lambda: self.client("s3").put_object(Bucket=bucket, Key=key, Body=package,
                          ServerSideEncryption="AES256", Metadata={"sha256": sha}))
        name = "mcp-onboarding-" + digest([body, sha])[:20]
        request = {"StackName": stack_id, "ChangeSetName": name, "ChangeSetType": "UPDATE",
                   "TemplateBody": json.dumps(body), "Capabilities": ["CAPABILITY_IAM"],
                   "Parameters": [{"ParameterKey": "ArtifactBucket", "UsePreviousValue": True},
                                  {"ParameterKey": "ArtifactKey", "ParameterValue": key}]}
        self.dispatch("change-set-" + name, request, lambda: cf.create_change_set(**request, ClientToken=name))
        for _ in range(60):
            change = cf.describe_change_set(StackName=stack_id, ChangeSetName=name)
            if change["Status"] not in ("CREATE_PENDING", "CREATE_IN_PROGRESS"):
                break
            time.sleep(3)
        if change["Status"] != "CREATE_COMPLETE" or change.get("NextToken"):
            raise RuntimeError("Change set unavailable or incomplete")
        changes = review_evaluated_changes(change["Changes"], previous["Resources"])
        self.receipt.update(changes=changes, release_sha256=sha)
        self.receipt.setdefault("previous_artifact_parameters", stack["Parameters"])
        self.save()
        self.dispatch("execute-" + name, {"StackName": stack_id, "ChangeSetName": name},
                      lambda: cf.execute_change_set(StackName=stack_id, ChangeSetName=name, ClientRequestToken=name))
        for _ in range(90):
            current = cf.describe_stacks(StackName=stack_id)["Stacks"][0]
            print("Existing Studio: " + current["StackStatus"], flush=True)
            if not current["StackStatus"].endswith("IN_PROGRESS"):
                break
            time.sleep(10)
        if current["StackStatus"] != "UPDATE_COMPLETE":
            raise RuntimeError("Existing app update has not completed successfully")
        outputs = {o["OutputKey"]: o["OutputValue"] for o in current["Outputs"]}
        if outputs != self.state["app"]["outputs"]:
            raise ValueError("Existing application identity changed")
        self.target.save("releaseSha256", sha)
        self.target.save("app", {"stackId": stack_id, "status": current["StackStatus"], "outputs": outputs})
        self.receipt["deployment_verified"] = True
        self.save()

    def publish(self):
        if not self.receipt.get("deployment_verified"):
            raise ValueError("Backend deployment must finish before UI publication")
        outputs = self.state["app"]["outputs"]
        dist = ROOT / "frontend/dist"
        if not (dist / "index.html").is_file():
            raise ValueError("Build the frontend before publication")
        for path in sorted(dist.rglob("*"), key=lambda p: (p == dist / "index.html", p)):
            if path.is_file():
                self.client("s3").put_object(Bucket=outputs["FrontendBucket"],
                    Key=str(path.relative_to(ROOT / "frontend/dist")), Body=path.read_bytes(),
                    ServerSideEncryption="AES256", ContentType=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                    CacheControl="no-cache" if path.name == "index.html" else "public,max-age=31536000,immutable")
        sha = hashlib.sha256((ROOT / "frontend/dist/index.html").read_bytes()).hexdigest()
        response = self.client("cloudfront").create_invalidation(DistributionId=outputs["DistributionId"],
            InvalidationBatch={"Paths": {"Quantity": 1, "Items": ["/*"]}, "CallerReference": "mcp-main-" + sha})
        self.receipt["frontend"] = {"index_sha256": sha, "invalidation_id": response["Invalidation"]["Id"]}
        self.save()
        for _ in range(120):
            current = self.client("cloudfront").get_invalidation(
                DistributionId=outputs["DistributionId"], Id=response["Invalidation"]["Id"])
            if current["Invalidation"]["Status"] == "Completed":
                self.receipt["frontend"]["status"] = "Completed"
                self.save()
                break
            print("CloudFront publication pending", flush=True)
            time.sleep(5)
        else:
            raise RuntimeError("CloudFront invalidation still pending; inspect the retained invalidation ID")
        print("Published existing UI: " + outputs["ApplicationOrigin"], flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["registry", "configure", "deploy", "publish"])
    parser.add_argument("--evidence-name", default="mcp-onboarding-main", type=evidence_name,
                        help="Unique release directory under artifacts; reuse it only to reconcile that release")
    target_arguments(parser)
    args = parser.parse_args()
    getattr(Release(args), args.action)()
