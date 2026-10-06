"""Release generic MCP onboarding into the existing, explicitly bound Studio."""
import argparse
import base64
import copy
import hashlib
import json
import mimetypes
import re
import time
from pathlib import Path
from urllib.parse import quote

from botocore.config import Config
from botocore.exceptions import ClientError

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


def verified_onboarding_release(path, state, logical, function_name):
    receipt = json.loads(Path(path).read_text())
    sha = receipt.get("release_sha256", "")
    if (receipt.get("stack_id") != state["app"]["stackId"]
            or receipt.get("deployment_verified") is not True
            or not re.fullmatch(r"[a-f0-9]{64}", sha)
            or sha != state.get("releaseSha256")
            or receipt.get("functions", {}).get(logical) != {"name": function_name, "sha256": sha}):
        raise ValueError("The onboarding release is not verified for this Studio function")
    return sha


def review_existing_ui_change(previous, proposed, package_settings=None):
    """Update onboarding and retire creator access; preserve unrelated resources."""
    expected = copy.deepcopy(previous)
    proposed = copy.deepcopy(proposed)
    if any(p["PolicyName"] == "McpCreation"
           for p in proposed["Resources"]["WorkerRole"]["Properties"]["Policies"]):
        raise ValueError("Remote MCP provisioning permissions have been retired")
    existing_worker = expected["Resources"]["WorkerRole"]["Properties"]["Policies"]
    existing_worker[:] = [p for p in existing_worker if p["PolicyName"] not in ("McpCreation", "McpOnboarding")]
    managed = proposed["Resources"].get("McpOnboardingPolicy", {})
    if (managed.get("Type") != "AWS::IAM::ManagedPolicy"
            or managed["Properties"].get("Roles") != [{"Ref": "WorkerRole"}]
            or set(managed["Properties"]) != {"Description", "Roles", "PolicyDocument"}):
        raise ValueError("Expected one worker-bound onboarding managed policy")
    expected["Resources"]["McpOnboardingPolicy"] = managed
    from infra.mcp_packages import NAMES, policies
    if any(name in proposed["Resources"] for name in NAMES):
        if not package_settings:
            raise ValueError("Package policies require an explicitly bound deployment")
        package_policies = policies(package_settings)
        if any(proposed["Resources"].get(name) != package_policies[name] for name in NAMES):
            raise ValueError("Package permissions differ from the bounded deployment contract")
        expected["Resources"].update(package_policies)
    for role, name in (("BusinessRole", "McpRegistryRead"), ("BusinessRole", "McpCredentialSetup"),
                       ("BusinessRole", "McpUserAuthorization")):
        before = expected["Resources"][role]["Properties"]["Policies"]
        additions = [p for p in proposed["Resources"][role]["Properties"]["Policies"] if p["PolicyName"] == name]
        if not additions and name in ("McpCredentialSetup", "McpUserAuthorization"):
            continue
        if len(additions) != 1:
            raise ValueError("Expected one onboarding policy")
        before[:] = [p for p in before if p["PolicyName"] != name] + additions
        before.sort(key=lambda p: p["PolicyName"])
        proposed["Resources"][role]["Properties"]["Policies"].sort(key=lambda p: p["PolicyName"])
    if expected != proposed:
        raise ValueError("Unrelated existing application change")


def review_evaluated_changes(changes, resources):
    from infra.mcp_packages import NAMES
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
        if (name in ("McpOnboardingPolicy", *NAMES) and r.get("ResourceType") == "AWS::IAM::ManagedPolicy"
                and (r["Action"] == "Add" or (r["Action"] == "Modify" and r.get("Replacement") == "False"))):
            result.append({k: r.get(k) for k in ("LogicalResourceId", "Action", "Replacement")})
            continue
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
        self.package = getattr(args, "package", None) or ROOT / "artifacts/serverless-release.zip"
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

    def upload(self, key, content):
        sha = hashlib.sha256(content).hexdigest()
        checksum = base64.b64encode(bytes.fromhex(sha)).decode()
        bucket = self.state["artifacts"]["outputs"]["Bucket"]
        try:
            self.dispatch("artifact-" + sha, {"bucket": bucket, "key": key, "sha256": sha},
                lambda: self.client("s3").put_object(Bucket=bucket, Key=key, Body=content,
                    ServerSideEncryption="AES256", Metadata={"sha256": sha},
                    IfNoneMatch="*", Tagging="auto-delete=no", ChecksumSHA256=checksum))
        except ClientError as error:
            if error.response["Error"]["Code"] != "PreconditionFailed":
                raise
        artifact = self.client("s3").head_object(Bucket=bucket, Key=key, ChecksumMode="ENABLED")
        tags = self.client("s3").get_object_tagging(Bucket=bucket, Key=key, VersionId=artifact["VersionId"])["TagSet"]
        if (artifact.get("Metadata", {}).get("sha256") != sha or artifact.get("ChecksumSHA256") != checksum
                or artifact.get("ServerSideEncryption") != "AES256"
                or artifact.get("VersionId") in (None, "null")
                or {t["Key"]: t["Value"] for t in tags}.get("auto-delete") != "no"):
            raise ValueError("Canonical release artifact is not verified")
        self.receipt["operations"]["artifact-" + sha].update(status="VERIFIED", version_id=artifact["VersionId"])
        self.save()
        return artifact["VersionId"]

    def template_input(self, body):
        content = json.dumps(body, separators=(",", ":")).encode()
        if len(content) <= 51200:
            return {"TemplateBody": content.decode()}
        key = "templates/mcp-onboarding/" + hashlib.sha256(content).hexdigest() + ".json"
        version = self.upload(key, content)
        bucket, region = self.state["artifacts"]["outputs"]["Bucket"], self.target.binding["region"]
        return {"TemplateURL": f"https://{bucket}.s3.{region}.amazonaws.com/{key}?versionId=" + quote(version, safe="")}

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

    def configure_packages(self):
        """Bind complete uploads to the existing private bucket, Runtime and Gateway."""
        from backend.mcp_python_cloud import artifact_bucket, configuration
        from infra.mcp_packages import policies
        settings = copy.deepcopy(self.state["journeyPlatform"])
        store = DynamoStore(self.state["app"]["outputs"]["StateTable"], self.target.session.resource("dynamodb"))
        with store.tx() as db:
            python = configuration(get(db, "mcp-python-config"), settings)
        gateway = self.client("bedrock-agentcore-control").get_gateway(gatewayIdentifier=settings["gateway_id"])
        expected_arn = f"arn:aws:bedrock-agentcore:{settings['region']}:{settings['account']}:gateway/{settings['gateway_id']}"
        if gateway["gatewayArn"] != expected_arn or gateway["status"] != "READY":
            raise ValueError("The existing Gateway is not ready at its bound identity")
        bucket = artifact_bucket(python)
        if bucket != self.state["artifacts"]["outputs"]["Bucket"]:
            raise ValueError("Package uploads must use this deployment's private artifact bucket")
        s3 = self.client("s3")
        if (s3.get_bucket_versioning(Bucket=bucket).get("Status") != "Enabled"
                or not all(s3.get_public_access_block(Bucket=bucket)["PublicAccessBlockConfiguration"].values())):
            raise ValueError("Package storage must be private and versioned")
        binding = {"bucket": bucket, "runtime_prefix": python["runtime_prefix"], "gateway_role_arn": gateway["roleArn"]}
        settings["mcp_package_upload"] = binding
        policies(settings)
        intent = {"binding": binding, "python_configuration_digest": digest(python), "gateway_arn": expected_arn}
        if self.receipt.get("package_configuration") not in (None, intent):
            raise ValueError("The retained package configuration intent changed")
        self.receipt["package_configuration"] = intent
        self.save()
        with store.tx() as db:
            current = get(db, "journey-platform")
            if (any(current.get(k) != settings[k] for k in ("account", "region", "gateway_id"))
                    or digest(get(db, "mcp-python-config")) != intent["python_configuration_digest"]
                    or current.get("mcp_package_upload") not in (None, binding)):
                raise ValueError("Live package deployment settings changed")
            put(db, "journey-platform", {**current, "mcp_package_upload": binding})
        self.target.save("journeyPlatform", settings)
        print("Bound complete MCP uploads to the existing private package storage and Gateway", flush=True)

    def deploy(self):
        cf = self.client("cloudformation")
        stack_id = self.state["app"]["stackId"]
        stack = cf.describe_stacks(StackName=stack_id)["Stacks"][0]
        previous = cf.get_template(StackName=stack_id)["TemplateBody"]
        previous = json.loads(previous) if isinstance(previous, str) else previous
        body = template(journey=self.state["journeyPlatform"])
        if self.state["journeyPlatform"].get("mcp_package_upload"):
            settings = self.state["journeyPlatform"]
            gateway = self.client("bedrock-agentcore-control").get_gateway(gatewayIdentifier=settings["gateway_id"])
            if gateway["roleArn"] != settings["mcp_package_upload"]["gateway_role_arn"]:
                raise ValueError("The package Gateway role binding changed")
        review_existing_ui_change(previous, body, package_settings=self.state["journeyPlatform"])
        validate_resource_tags(body["Resources"])
        sizes = {name: sum(len(json.dumps(p["PolicyDocument"], separators=(",", ":")))
                           for p in r["Properties"].get("Policies", []))
                 for name, r in body["Resources"].items() if r["Type"] == "AWS::IAM::Role"}
        managed_sizes = {name: len(json.dumps(r["Properties"]["PolicyDocument"], separators=(",", ":")))
                         for name, r in body["Resources"].items() if r["Type"] == "AWS::IAM::ManagedPolicy"}
        physical = {r["LogicalResourceId"]: r["PhysicalResourceId"] for r in cf.list_stack_resources(
            StackName=stack_id)["StackResourceSummaries"]}
        # Count the resulting policies: the old creator policy is removed by
        # this reviewed change set, so it must not inflate the post-update size.
        for role, names in (("WorkerRole", {"McpOnboarding", "McpCreation"}),
                            ("BusinessRole", {"McpRegistryRead", "McpCredentialSetup", "McpUserAuthorization"})):
            iam = self.client("iam")
            existing = [iam.get_role_policy(RoleName=physical[role], PolicyName=n)["PolicyDocument"]
                        for n in iam.list_role_policies(RoleName=physical[role])["PolicyNames"] if n not in names]
            additions = [p["PolicyDocument"] for p in body["Resources"][role]["Properties"]["Policies"] if p["PolicyName"] in names]
            sizes[role] = sum(len(json.dumps(p, separators=(",", ":"))) for p in [*existing, *additions])
        if (max(sizes.values()) >= 10240 or any(size >= 6144 for size in managed_sizes.values())
                or "AWS::Lambda::Url" in json.dumps(body)):
            raise ValueError("Infrastructure preflight failed")
        template_request = self.template_input(body)
        cf.validate_template(**template_request)
        if not (self.evidence / "template-before.json").exists():
            (self.evidence / "template-before.json").write_text(json.dumps(previous, indent=2))
            (self.evidence / "stack-before.json").write_text(json.dumps(stack, indent=2, default=str))
        (self.evidence / "template-proposed.json").write_text(json.dumps(body, indent=2))
        (self.evidence / "preflight.json").write_text(json.dumps({
            "retention_tags": True, "no_function_urls": True, "inline_policy_bytes": sizes,
            "managed_policy_bytes": managed_sizes,
            "only_mcp_policies_changed": True,
            "retired_creator_policy_removed": any(p["PolicyName"] == "McpCreation"
                for p in previous["Resources"]["WorkerRole"]["Properties"]["Policies"])}, indent=2))
        package = self.package.read_bytes()
        sha = hashlib.sha256(package).hexdigest()
        key = "releases/" + sha + "/lambda.zip"
        self.upload(key, package)
        name = "mcp-onboarding-" + digest([body, sha])[:20]
        request = {"StackName": stack_id, "ChangeSetName": name, "ChangeSetType": "UPDATE",
                   **template_request, "Capabilities": ["CAPABILITY_IAM"],
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
        resources = cf.list_stack_resources(StackName=stack_id)["StackResourceSummaries"]
        required = {"auto-delete": "no", "project": "governed-agent-builder",
                    "deployment": self.state["journeyPlatform"]["mcp_onboarding"]["credential_prefix"]}
        iam = self.client("iam")
        from infra.mcp_packages import NAMES
        self.receipt["package_policies"] = {}
        for resource in resources:
            logical = resource["LogicalResourceId"]
            if logical not in ("McpOnboardingPolicy", *NAMES):
                continue
            policy_arn = resource["PhysicalResourceId"]
            tagging = {"PolicyArn": policy_arn, "Tags": [{"Key": k, "Value": v} for k, v in required.items()]}
            current_tags = {t["Key"]: t["Value"] for t in iam.list_policy_tags(PolicyArn=policy_arn)["Tags"]}
            if any(current_tags.get(k) not in (None, v) for k, v in required.items()):
                raise ValueError("Onboarding policy ownership tags changed")
            if any(current_tags.get(k) != v for k, v in required.items()):
                operation = "onboarding-policy-tags" if logical == "McpOnboardingPolicy" else logical + "-tags"
                self.dispatch(operation, tagging, lambda: iam.tag_policy(**tagging))
                current_tags = {t["Key"]: t["Value"] for t in iam.list_policy_tags(PolicyArn=policy_arn)["Tags"]}
            if any(current_tags.get(k) != v for k, v in required.items()):
                raise ValueError("Onboarding policy tags have not reconciled; no write was repeated")
            receipt = {"arn": policy_arn, "tags_verified": True}
            if logical == "McpOnboardingPolicy":
                self.receipt["managed_policy"] = receipt
            else:
                self.receipt["package_policies"][logical] = receipt
        self.receipt["functions"] = {}
        for logical, resource in body["Resources"].items():
            if resource["Type"] != "AWS::Lambda::Function":
                continue
            live = self.client("lambda").get_function_configuration(FunctionName=physical[logical])
            if (live["CodeSha256"] != base64.b64encode(bytes.fromhex(sha)).decode()
                    or live["State"] != "Active" or live["LastUpdateStatus"] != "Successful"):
                raise RuntimeError("A Studio function has not reached the verified canonical release")
            self.receipt["functions"][logical] = {"name": physical[logical], "sha256": sha}
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
    parser.add_argument("action", choices=["registry", "configure", "configure_packages", "deploy", "publish"])
    parser.add_argument("--evidence-name", default="mcp-onboarding-main", type=evidence_name,
                        help="Unique release directory under artifacts; reuse it only to reconcile that release")
    parser.add_argument("--package", type=Path, help="Reviewed canonical Lambda ZIP; defaults to artifacts/serverless-release.zip")
    target_arguments(parser)
    args = parser.parse_args()
    getattr(Release(args), args.action)()
