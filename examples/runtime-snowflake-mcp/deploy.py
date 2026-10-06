"""Account-bound CDK/CloudFormation deployment, with saved intents and GET reconciliation."""
import argparse
import base64
import copy
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import quote
from uuid import uuid4

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from infrastructure import templates
from oauth_infrastructure import names, runtime_oauth
from snowflake_mcp.identity import USER_TOKEN_HEADER
from snowflake_mcp.gateway_token import SNOWFLAKE_TOKEN_HEADER
from artifact_upload import multipart_upload


class Deployment:
    def __init__(self, config, directory):
        self.config, self.directory = config, directory
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "state.json"
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {"config": config, "operations": {}}
        if self.state["config"] != config:
            raise ValueError("Deployment configuration differs from the saved target. Use a separate state directory.")
        self.session = boto3.Session(profile_name=config["profile"], region_name=config["region"])
        self.clients = {}
        if self.client("sts").get_caller_identity()["Account"] != config["account"]:
            raise ValueError("AWS account mismatch")
        self.artifacts, self.runtime = templates(**{k: v for k, v in config.items() if k != "profile"})
        self.save()

    def client(self, name):
        if name not in self.clients:
            self.clients[name] = self.session.client(name, config=Config(
                retries={"total_max_attempts": 1}, connect_timeout=10, read_timeout=300 if name == "s3" else 60,
                request_checksum_calculation="when_required"))
        return self.clients[name]

    def save(self):
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.state, indent=2))
        temporary.replace(self.path)

    def plan(self):
        role = self.client("iam").get_role(RoleName=self.config["gateway_role_arn"].rsplit("/", 1)[-1])["Role"]
        if role["Arn"] != self.config["gateway_role_arn"]:
            raise ValueError("Gateway role mismatch")
        for name, body in (("artifacts", self.artifacts), ("runtime", self.runtime)):
            (self.directory / (name + "-template.json")).write_text(json.dumps(body, indent=2))
            self.client("cloudformation").validate_template(TemplateBody=json.dumps(body))
        print("Plan validated: exact account, Gateway role, CDK templates; no AWS writes.", flush=True)

    def stack(self, section, body, parameters=None, *, update=False):
        cf = self.client("cloudformation")
        name = self.config["prefix"] + "-" + section
        fingerprint = hashlib.sha256(json.dumps([body, parameters], sort_keys=True).encode()).hexdigest()
        operation = self.state["operations"].get(section)
        if operation and operation["digest"] != fingerprint:
            raise ValueError("A different deployment intent exists. Review the saved templates before a new release.")
        try:
            prior = cf.describe_stacks(StackName=name)["Stacks"][0]
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ValidationError" or "does not exist" not in exc.response["Error"]["Message"]:
                raise
            prior = None
        if operation:
            if not prior:
                raise RuntimeError("Submission outcome is uncertain. Inspect CloudFormation using the retained client token; no retry was sent.")
        else:
            if prior and (not update or prior["StackId"] != self.state.get(section, {}).get("id")):
                raise ValueError("Stack already exists without this deployment's receipt. Refusing to adopt it.")
            if update and (not prior or prior["StackStatus"] not in ("CREATE_COMPLETE", "UPDATE_COMPLETE")):
                raise ValueError("Only a successful, receipt-bound stack can receive a new release")
            token = str(uuid4())
            self.state["operations"][section] = {"digest": fingerprint, "token": token, "phase": "SUBMITTING",
                                                  "action": "update" if update else "create"}
            self.save()
            submit = cf.update_stack if update else cf.create_stack
            submit(StackName=name, TemplateBody=json.dumps(body),
                Capabilities=["CAPABILITY_NAMED_IAM"], ClientRequestToken=token,
                Parameters=[{"ParameterKey": k, "ParameterValue": v} for k, v in (parameters or {}).items()],
                Tags=[{"Key": "auto-delete", "Value": "no"}, {"Key": "project", "Value": self.config["prefix"]}])
        for _ in range(240):
            current = cf.describe_stacks(StackName=name)["Stacks"][0]
            if not current["StackStatus"].endswith("IN_PROGRESS"):
                break
            time.sleep(5)
        self.state["operations"][section]["phase"] = current["StackStatus"]
        self.state[section] = {"id": current["StackId"], "outputs": {
            o["OutputKey"]: o["OutputValue"] for o in current.get("Outputs", [])}}
        self.save()
        if current["StackStatus"] not in ("CREATE_COMPLETE", "UPDATE_COMPLETE"):
            events = cf.describe_stack_events(StackName=name)["StackEvents"]
            failures = [{"resource": e["LogicalResourceId"], "status": e["ResourceStatus"],
                         "reason": e.get("ResourceStatusReason")} for e in events if "FAILED" in e["ResourceStatus"]]
            (self.directory / "stack-failures.json").write_text(json.dumps(failures, indent=2))
            raise RuntimeError("Stack is not successful; see stack-failures.json. No retry was sent.")
        actual_template = cf.get_template(StackName=name, TemplateStage="Original")["TemplateBody"]
        actual_template = json.loads(actual_template) if isinstance(actual_template, str) else actual_template
        actual_parameters = {p["ParameterKey"]: p["ParameterValue"] for p in current.get("Parameters", [])}
        if actual_template != body or any(actual_parameters.get(k) != v for k, v in (parameters or {}).items()):
            self.state["operations"][section]["phase"] = "OUTCOME_UNKNOWN"
            self.save()
            raise RuntimeError("CloudFormation is stable but does not contain the retained template and parameters. No submission was repeated.")
        print(name + ": " + current["StackStatus"], flush=True)
        return self.state[section]["outputs"]

    def upload_package(self, package, bucket, digest, *, retry_upload=False):
        key = "releases/" + digest + "/runtime.zip"
        upload = self.state.get("upload")
        write = upload is None
        if upload:
            if (upload["bucket"], upload["key"], upload["digest"]) != (bucket, key, digest):
                raise ValueError("Retained upload binding differs from the requested release")
            if not upload.get("version"):
                try:
                    value = self.client("s3").head_object(Bucket=bucket, Key=key)
                except ClientError as exc:
                    if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 404:
                        raise
                    if not retry_upload:
                        raise RuntimeError("Upload is absent after reconciliation. Use --retry-upload for an explicit conditional retry.") from None
                    upload["retry_requested_at"] = time.time()
                    self.save()
                    write = True
                else:
                    if value["Metadata"].get("sha256") != digest:
                        raise ValueError("S3 upload outcome is unresolved")
                    upload["version"] = value["VersionId"]
                    self.save()
        else:
            self.state["upload"] = upload = {"digest": digest, "bucket": bucket, "key": key}
            self.save()
        if write:
            if package.stat().st_size > 50 * 1024 * 1024:
                raise ValueError("Runtime package exceeds the example's 50MB upload limit")
            if package.stat().st_size > 8 * 1024 * 1024:
                multipart_upload(self.client("s3"), package, upload, self.save)
                return upload
            response = self.client("s3").put_object(Bucket=bucket, Key=key, Body=package.read_bytes(),
                ServerSideEncryption="AES256", Metadata={"sha256": digest}, Tagging="auto-delete=no",
                IfNoneMatch="*", ChecksumSHA256=base64.b64encode(bytes.fromhex(digest)).decode())
            upload["version"] = response["VersionId"]
            self.save()
        return upload

    def deploy(self, package, *, new_release=False, retry_upload=False):
        gateway_mode = self.config.get("auth_source") == "gateway"
        if not gateway_mode and not self.config.get("oauth"):
            raise ValueError("Configure per-user OAuth before deploying this package. Use migrate-oauth for an existing WIF deployment.")
        if not gateway_mode:
            workload, provider = names(self.config["oauth"])
            control = self.client("bedrock-agentcore-control")
            native = control.get_oauth2_credential_provider(name=provider)
            identity = control.get_workload_identity(name=workload)
            if (native.get("status") != "READY"
                or native["oauth2ProviderConfigOutput"]["customOauth2ProviderConfig"]["clientId"] == "setup-required"
                or identity["allowedResourceOauth2ReturnUrls"] != [self.config["oauth"]["origin"] + "/oauth/callback"]):
                raise ValueError("Complete and audit the OAuth prerequisites before deploying the Runtime")
        self.plan()
        outputs = self.stack("artifacts", self.artifacts)
        digest = hashlib.sha256(package.read_bytes()).hexdigest()
        if self.state.get("pending_release", {}).get("to", digest) != digest:
            raise ValueError("A different release is already pending")
        upload = self.state.get("upload")
        if upload and upload["digest"] != digest:
            operation = self.state["operations"].get("runtime", {})
            if not new_release or operation.get("phase") not in ("CREATE_COMPLETE", "UPDATE_COMPLETE"):
                raise ValueError("Package differs from the retained release; a successful prior release and explicit --new-release are required")
            self.state.setdefault("release_history", []).append({
                "upload": upload, "runtime_operation": operation, "runtime": self.state["runtime"]})
            self.state["pending_release"] = {"from": upload["digest"], "to": digest}
            self.state.pop("upload")
            self.state["operations"].pop("runtime")
            self.save()
        upload = self.upload_package(package, outputs["Bucket"], digest, retry_upload=retry_upload)
        outputs = self.stack("runtime", self.runtime, {
            "ArtifactBucket": upload["bucket"], "ArtifactKey": upload["key"], "ArtifactVersion": upload["version"]},
            update=bool(self.state.get("pending_release")))
        self.state["endpoint"] = (f"https://bedrock-agentcore.{self.config['region']}.amazonaws.com/runtimes/"
                                  + quote(outputs["RuntimeArn"], safe="") + "/invocations?qualifier=DEFAULT")
        self.save()
        self.tag_generated()
        self.audit()
        self.state.pop("pending_release", None)
        self.save()
        print(json.dumps({"endpoint": self.state["endpoint"], "execution_role": outputs["ExecutionRoleArn"]}), flush=True)

    def migrate_oauth(self, config):
        """Explicitly supersede an absent pending WIF package; never replay its upload."""
        if not config.get("oauth") or self.config.get("oauth") or {k: v for k, v in config.items() if k != "oauth"} != self.config:
            raise ValueError("OAuth migration must preserve every existing deployment binding")
        artifacts, runtime_template = templates(**{k: v for k, v in config.items() if k != "profile"})
        runtime, endpoint = self.generated()
        if (runtime["status"] != "READY" or endpoint["status"] != "READY"
                or endpoint["liveVersion"] != runtime["agentRuntimeVersion"]
                or runtime["agentRuntimeArn"] != self.state["runtime"]["outputs"]["RuntimeArn"]):
            raise ValueError("The receipt-bound live Runtime must be stable before migration")
        s3 = runtime["agentRuntimeArtifact"]["codeConfiguration"]["code"]["s3"]
        candidates = [{"upload": self.state.get("upload", {}), "runtime_operation": self.state["operations"].get("runtime", {})},
                      *self.state.get("release_history", [])]
        live = next((r for r in candidates if r["upload"].get("version")
            and s3 == {"bucket": r["upload"]["bucket"], "prefix": r["upload"]["key"], "versionId": r["upload"]["version"]}
            and r["runtime_operation"].get("phase") in ("CREATE_COMPLETE", "UPDATE_COMPLETE")), None)
        if not live:
            raise ValueError("No retained successful release matches the live Runtime artifact")
        pending = self.state.get("pending_release")
        if pending:
            upload = self.state["upload"]
            if upload.get("version") or upload.get("multipart"):
                raise ValueError("Pending upload has cloud state; reconcile it before OAuth migration")
            try:
                self.client("s3").head_object(Bucket=upload["bucket"], Key=upload["key"])
            except ClientError as exc:
                if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 404:
                    raise
            else:
                raise ValueError("Pending package exists; do not supersede its upload receipt")
        old = copy.deepcopy(self.state)
        self.state.setdefault("migration_history", []).append({"at": time.time(), "from": old["config"],
            "pending_release": old.get("pending_release"), "pending_upload": old.get("upload") if pending else None,
            "pending_outcome": "ABSENT" if pending else "NONE", "live_version": runtime["agentRuntimeVersion"]})
        self.state["upload"] = copy.deepcopy(live["upload"])
        self.state["operations"]["runtime"] = copy.deepcopy(live["runtime_operation"])
        self.state.pop("pending_release", None)
        self.state["config"] = self.config = config
        self.artifacts, self.runtime = artifacts, runtime_template
        self.save()
        self.plan()
        print("OAuth migration journal saved. Live Runtime unchanged; a new-release deployment is still required.", flush=True)

    def migrate_gateway(self, config):
        expected = {k: v for k, v in self.config.items() if k not in ("oauth", "auth_source")}
        if config != {**expected, "auth_source": "gateway"} or self.state.get("pending_release"):
            raise ValueError("Gateway migration must preserve the deployment target and finish any pending release")
        runtime, endpoint = self.generated()
        upload = self.state["upload"]
        if (runtime["status"] != "READY" or endpoint["status"] != "READY"
                or endpoint["liveVersion"] != runtime["agentRuntimeVersion"]
                or runtime["agentRuntimeArn"] != self.state["runtime"]["outputs"]["RuntimeArn"]
                or runtime["agentRuntimeArtifact"]["codeConfiguration"]["code"]["s3"] != {
                    "bucket": upload["bucket"], "prefix": upload["key"], "versionId": upload["version"]}):
            raise ValueError("The live Runtime must match its retained successful artifact")
        artifacts, runtime_template = templates(**{k: v for k, v in config.items() if k != "profile"})
        self.state.setdefault("migration_history", []).append({
            "at": time.time(), "from": copy.deepcopy(self.config), "to_auth_source": "gateway",
            "live_version": runtime["agentRuntimeVersion"]})
        self.state["config"] = self.config = config
        self.artifacts, self.runtime = artifacts, runtime_template
        self.save()
        print("Gateway-token migration journal saved. Deploy the new package to apply it.", flush=True)

    def generated(self):
        control = self.client("bedrock-agentcore-control")
        rid = self.state["runtime"]["outputs"]["RuntimeId"]
        runtime = control.get_agent_runtime(agentRuntimeId=rid)
        endpoint = control.get_agent_runtime_endpoint(agentRuntimeId=rid, endpointName="DEFAULT")
        return runtime, endpoint

    def tag_generated(self):
        runtime, endpoint = self.generated()
        for arn in (endpoint["agentRuntimeEndpointArn"], runtime["workloadIdentityDetails"]["workloadIdentityArn"]):
            self.client("bedrock-agentcore-control").tag_resource(
                resourceArn=arn, tags={"auto-delete": "no", "project": self.config["prefix"]})
        self.state["generated_tags_applied"] = True
        self.save()

    def audit(self):
        checks = {}
        runtime, endpoint = self.generated()
        checks["runtime_ready"] = runtime["status"] == "READY"
        checks["default_version_ready"] = endpoint["status"] == "READY" and endpoint["liveVersion"] == runtime["agentRuntimeVersion"]
        checks["iam_inbound"] = not runtime.get("authorizerConfiguration")
        checks["mcp_protocol"] = runtime["protocolConfiguration"]["serverProtocol"] == "MCP"
        checks["exact_execution_role"] = runtime["roleArn"] == self.state["runtime"]["outputs"]["ExecutionRoleArn"]
        checks["exact_code_version"] = runtime["agentRuntimeArtifact"]["codeConfiguration"]["code"]["s3"] == {
            "bucket": self.state["upload"]["bucket"], "prefix": self.state["upload"]["key"], "versionId": self.state["upload"]["version"]}
        user_env, user_policy = (runtime_oauth(self.config["account"], self.config["region"], self.config["oauth"],
            self.config["snowflake_role"]) if self.config.get("oauth") else ({}, []))
        if self.config.get("auth_source") == "gateway":
            user_env = {"SNOWFLAKE_AUTH_SOURCE": "gateway"}
            checks["gateway_token_header_only"] = runtime.get("requestHeaderConfiguration", {}).get("requestHeaderAllowlist") == [SNOWFLAKE_TOKEN_HEADER]
        checks["no_secret_environment"] = runtime["environmentVariables"] == {
            "SNOWFLAKE_ACCOUNT": self.config["snowflake_account"], "SNOWFLAKE_ROLE": self.config["snowflake_role"],
            "SNOWFLAKE_WAREHOUSE": self.config["warehouse"], **user_env}
        if self.config.get("oauth"):
            checks["user_header_allowlist"] = runtime.get("requestHeaderConfiguration", {}).get("requestHeaderAllowlist") == [USER_TOKEN_HEADER]
        control = self.client("bedrock-agentcore-control")
        for label, arn in (("runtime", runtime["agentRuntimeArn"]), ("endpoint", endpoint["agentRuntimeEndpointArn"]),
                           ("identity", runtime["workloadIdentityDetails"]["workloadIdentityArn"])):
            checks[label + "_tag"] = control.list_tags_for_resource(resourceArn=arn)["tags"].get("auto-delete") == "no"
        bucket = self.state["upload"]["bucket"]
        s3 = self.client("s3")
        checks["bucket_private"] = all(s3.get_public_access_block(Bucket=bucket)["PublicAccessBlockConfiguration"].values())
        checks["bucket_policy_private"] = not s3.get_bucket_policy_status(Bucket=bucket)["PolicyStatus"]["IsPublic"]
        checks["bucket_encrypted"] = bool(s3.get_bucket_encryption(Bucket=bucket)["ServerSideEncryptionConfiguration"]["Rules"])
        checks["bucket_tag"] = {t["Key"]: t["Value"] for t in s3.get_bucket_tagging(Bucket=bucket)["TagSet"]}.get("auto-delete") == "no"
        role = self.client("iam").get_role(RoleName=self.config["prefix"] + "-execution")["Role"]
        checks["execution_role_tag"] = {t["Key"]: t["Value"] for t in role["Tags"]}.get("auto-delete") == "no"
        iam = self.client("iam")
        role_name = role["RoleName"]
        checks["no_execution_managed_policies"] = iam.list_attached_role_policies(RoleName=role_name)["AttachedPolicies"] == []
        checks["only_execution_policy"] = iam.list_role_policies(RoleName=role_name)["PolicyNames"] == ["RuntimeArtifactsAndLogs"]
        execution_policy = iam.get_role_policy(RoleName=role_name, PolicyName="RuntimeArtifactsAndLogs")["PolicyDocument"]
        runtime_name = self.config["prefix"].replace("-", "_") + "_mcp"
        log_prefix = f"arn:aws:logs:{self.config['region']}:{self.config['account']}:log-group:/aws/bedrock-agentcore/runtimes/{runtime_name}-*"
        checks["execution_permissions_scoped"] = execution_policy["Statement"] == [
            {"Effect": "Allow", "Action": ["s3:GetObject", "s3:GetObjectVersion"],
             "Resource": f"arn:aws:s3:::{bucket}/{self.state['upload']['key']}"},
            {"Effect": "Allow", "Action": ["logs:CreateLogStream", "logs:DescribeLogStreams", "logs:PutLogEvents"],
             "Resource": log_prefix + ":*"}, *user_policy]
        checks["execution_trust_scoped"] = role["AssumeRolePolicyDocument"]["Statement"] == [{
            "Effect": "Allow", "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
            "Action": "sts:AssumeRole", "Condition": {
                "StringEquals": {"aws:SourceAccount": self.config["account"]},
                "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{self.config['region']}:{self.config['account']}:runtime/{runtime_name}-*"}}}]
        for section in ("artifacts", "runtime"):
            stack = self.client("cloudformation").describe_stacks(StackName=self.state[section]["id"])["Stacks"][0]
            checks[section + "_stack_complete"] = stack["StackStatus"] in ("CREATE_COMPLETE", "UPDATE_COMPLETE")
            checks[section + "_stack_tag"] = {t["Key"]: t["Value"] for t in stack["Tags"]}.get("auto-delete") == "no"
        policy = self.client("iam").get_role_policy(
            RoleName=self.config["gateway_role_arn"].rsplit("/", 1)[-1], PolicyName=self.config["prefix"] + "-invoke")["PolicyDocument"]
        checks["gateway_exact_grant"] = policy["Statement"] == [{
            "Effect": "Allow", "Action": "bedrock-agentcore:InvokeAgentRuntime",
            "Resource": [runtime["agentRuntimeArn"], runtime["agentRuntimeArn"] + "/runtime-endpoint/DEFAULT"]}]
        log_name = "/aws/bedrock-agentcore/runtimes/" + runtime["agentRuntimeId"] + "-DEFAULT"
        log_arn = f"arn:aws:logs:{self.config['region']}:{self.config['account']}:log-group:{log_name}"
        logs = self.client("logs")
        checks["logs_tag"] = logs.list_tags_for_resource(resourceArn=log_arn)["tags"].get("auto-delete") == "no"
        checks["logs_retention"] = next(g for g in logs.describe_log_groups(logGroupNamePrefix=log_name)["logGroups"]
                                       if g["logGroupName"] == log_name)["retentionInDays"] == 14
        result = {"pass": all(checks.values()), "checks": checks}
        (self.directory / "security.json").write_text(json.dumps(result, indent=2))
        if not result["pass"]:
            raise RuntimeError("Security/tag checks failed; see security.json")
        print("Runtime security and tag audit passed.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["plan", "deploy", "audit", "tag-generated", "migrate-oauth", "migrate-gateway"])
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--package", type=Path)
    parser.add_argument("--new-release", action="store_true", help="Explicitly update the same receipt-bound Runtime to a different locked package")
    parser.add_argument("--retry-upload", action="store_true", help="Retry the retained artifact PUT only after a fresh HEAD confirms absence")
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    original = json.loads((args.state_dir / "state.json").read_text())["config"] if args.action.startswith("migrate-") else config
    deployment = Deployment(original, args.state_dir.resolve())
    if args.action == "migrate-oauth":
        deployment.migrate_oauth(config)
    elif args.action == "migrate-gateway":
        deployment.migrate_gateway(config)
    elif args.action == "deploy":
        if not args.package:
            parser.error("deploy requires --package")
        deployment.deploy(args.package.resolve(), new_release=args.new_release, retry_upload=args.retry_upload)
    elif args.action == "tag-generated":
        deployment.tag_generated()
        deployment.audit()
    else:
        getattr(deployment, args.action)()
