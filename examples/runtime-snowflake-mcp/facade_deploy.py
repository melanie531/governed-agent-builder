"""Deploy the optional OAuth-compatible facade for an existing IAM MCP Runtime."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import zipfile

import boto3

from deploy import Deployment
from facade_infrastructure import template


class FacadeDeployment(Deployment):
    def __init__(self, runtime_state, directory, *, python_config=None):
        if bool(runtime_state) == bool(python_config):
            raise ValueError("Choose one existing Runtime or a Python onboarding configuration")
        if python_config:
            self.config = json.loads(Path(python_config).read_text())
            if (set(self.config) - {"reserved_concurrency"} != {"prefix", "profile", "account", "region", "python_onboarding",
                                     "bundle_name", "bundle_digest"}
                    or not re.fullmatch(r"[a-f0-9]{64}", self.config["bundle_digest"])
                    or not isinstance(self.config["bundle_name"], str)
                    or not 1 <= len(self.config["bundle_name"]) <= 100):
                raise ValueError("Bind Python onboarding to a named, approved dependency bundle")
            self.config["artifact_bucket"] = self.config["python_onboarding"]["artifact"]["bucket"]
        else:
            source = json.loads(Path(runtime_state).read_text())
            self.config = {**{k: source["config"][k] for k in (
                "prefix", "profile", "account", "region", "snowflake_account", "snowflake_role", "warehouse")},
                           "runtime_arn": source["runtime"]["outputs"]["RuntimeArn"],
                           "artifact_bucket": source["artifacts"]["outputs"]["Bucket"]}
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "state.json"
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {"config": self.config, "operations": {}}
        if self.state["config"] != self.config:
            raise ValueError("Facade target differs from its saved Runtime binding")
        self.session = boto3.Session(profile_name=self.config["profile"], region_name=self.config["region"])
        self.clients = {}
        if self.client("sts").get_caller_identity()["Account"] != self.config["account"]:
            raise ValueError("AWS account mismatch")
        fields = ("prefix", "account", "region", "python_onboarding") if python_config else (
            "prefix", "account", "region", "runtime_arn", "snowflake_account", "snowflake_role", "warehouse")
        if "reserved_concurrency" in self.config:
            fields += ("reserved_concurrency",)
        self.body = template(**{k: self.config[k] for k in fields})
        self.save()

    def plan(self):
        if self.config.get("python_onboarding"):
            config = self.config["python_onboarding"]
            table = self.client("dynamodb").describe_table(TableName=config["table_name"])["Table"]
            arn = f"arn:aws:dynamodb:{self.config['region']}:{self.config['account']}:table/{config['table_name']}"
            role = self.client("iam").get_role(RoleName=config["worker_role_name"])["Role"]
            if (table["TableArn"] != arn or table["TableStatus"] != "ACTIVE"
                    or role["Arn"] != f"arn:aws:iam::{self.config['account']}:role/{config['worker_role_name']}"
                    or {t["Key"]: t["Value"] for t in role.get("Tags", [])}.get("auto-delete") != "no"):
                raise ValueError("Existing Studio table or worker binding changed")
            tags = self.client("dynamodb").list_tags_of_resource(ResourceArn=arn)["Tags"]
            if {t["Key"]: t["Value"] for t in tags}.get("auto-delete") != "no":
                raise ValueError("Existing Studio state table must carry its retention tag")
            artifact = config["artifact"]
            s3 = self.client("s3")
            if s3.get_bucket_versioning(Bucket=artifact["bucket"])["Status"] != "Enabled":
                raise ValueError("Python dependency bucket must retain immutable versions")
            value = s3.get_object(Bucket=artifact["bucket"], Key=artifact["key"],
                VersionId=artifact["version_id"], ExpectedBucketOwner=self.config["account"])
            digest = hashlib.sha256()
            try:
                while data := value["Body"].read(1024 * 1024):
                    digest.update(data)
            finally:
                value["Body"].close()
            if (value["ContentLength"] > 64 * 1024 * 1024
                    or value.get("ServerSideEncryption") not in ("AES256", "aws:kms")
                    or digest.hexdigest() != self.config["bundle_digest"]):
                raise ValueError("Approved Python dependency bundle changed")
        else:
            value = self.client("bedrock-agentcore-control").get_agent_runtime(
                agentRuntimeId=self.config["runtime_arn"].rsplit("/", 1)[-1])
            if value["agentRuntimeArn"] != self.config["runtime_arn"] or value.get("authorizerConfiguration"):
                raise ValueError("Facade requires this exact IAM Runtime")
        if not all(self.client("s3").get_public_access_block(
                Bucket=self.config["artifact_bucket"])["PublicAccessBlockConfiguration"].values()):
            raise ValueError("Artifact bucket must block public access")
        (self.directory / "facade-template.json").write_text(json.dumps(self.body, indent=2))
        self.client("cloudformation").validate_template(TemplateBody=json.dumps(self.body))
        print("Authenticated facade plan validated.", flush=True)

    def deploy(self, *, new_release=False):
        self.plan()
        package = self.directory / "facade.zip"
        with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_DEFLATED) as output:
            info = zipfile.ZipInfo("facade.py", (2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            output.writestr(info, Path(__file__).with_name("facade.py").read_bytes())
        sha = hashlib.sha256(package.read_bytes()).hexdigest()
        previous = self.state.get("upload")
        operation = self.state["operations"].get("facade")
        if previous and operation:
            parameters = {"ArtifactBucket": previous["bucket"], "ArtifactKey": previous["key"],
                          "ArtifactVersion": previous.get("version")}
            intent = hashlib.sha256(json.dumps([self.body, parameters], sort_keys=True).encode()).hexdigest()
            if previous["digest"] != sha or operation["digest"] != intent:
                if not new_release or operation["phase"] not in ("CREATE_COMPLETE", "UPDATE_COMPLETE"):
                    raise ValueError("A changed facade requires an explicit new release from a successful prior receipt")
                self.audit()
                self.state.setdefault("release_history", []).append(copy.deepcopy({
                    "upload": previous, "facade": self.state["facade"], "operation": operation}))
                self.state["operations"].pop("facade")
                if previous["digest"] != sha:
                    self.state.pop("upload")
                self.save()
        upload = self.upload_package(package, self.config["artifact_bucket"], sha)
        outputs = self.stack("facade", self.body, {
            "ArtifactBucket": upload["bucket"], "ArtifactKey": upload["key"], "ArtifactVersion": upload["version"]},
            update=bool(self.state.get("facade")))
        self.state["endpoint"] = outputs["McpEndpoint"]
        self.save()
        if self.config.get("python_onboarding"):
            self.tag_python_policy()
        self.audit()
        print(json.dumps({"mcp_endpoint": self.state["endpoint"]}), flush=True)

    def tag_python_policy(self):
        arn = self.state["facade"]["outputs"]["DeploymentPolicyArn"]
        required = {"auto-delete": "no", "project": "governed-agent-builder",
                    "deployment": self.config["python_onboarding"]["deployment_prefix"]}
        iam = self.client("iam")
        def read():
            result = iam.list_policy_tags(PolicyArn=arn)
            if result.get("IsTruncated"):
                raise ValueError("Managed policy tag inventory is incomplete")
            return {t["Key"]: t["Value"] for t in result["Tags"]}
        tags = read()
        if any(tags.get(k) not in (None, v) for k, v in required.items()):
            raise ValueError("Managed policy ownership tags conflict")
        request = {"PolicyArn": arn, "Tags": [{"Key": k, "Value": v} for k, v in required.items()]}
        operation = self.state["operations"].get("policy-tags")
        if operation and operation["request"] != request:
            raise ValueError("Retained managed policy tagging intent changed")
        if any(tags.get(k) != v for k, v in required.items()):
            if operation:
                raise RuntimeError("Managed policy tags are unverified; the write was not repeated")
            operation = {"request": request, "phase": "INTENT"}
            self.state["operations"]["policy-tags"] = operation
            self.save()
            iam.tag_policy(**request)
            tags = read()
            if any(tags.get(k) != v for k, v in required.items()):
                raise RuntimeError("Managed policy tags have not reconciled; the write was not repeated")
        self.state["operations"]["policy-tags"] = {"request": request, "phase": "VERIFIED"}
        self.save()

    def configure(self):
        """Enable the UI only after the installed bridge and tags pass live checks."""
        from backend.dynamo_store import DynamoStore
        from backend.foundation_runs import get, put
        from backend.mcp_python_cloud import configuration

        if not self.config.get("python_onboarding"):
            raise ValueError("Only the Python onboarding facade has a Studio configuration")
        self.audit()
        config = self.config["python_onboarding"]
        value = {"bundle_name": self.config["bundle_name"],
                 "artifact": {**config["artifact"], "digest": self.config["bundle_digest"]},
                 "runtime_prefix": config["runtime_prefix"], "deployment_prefix": config["deployment_prefix"],
                 "runtime_role": self.state["facade"]["outputs"]["RuntimeRoleArn"],
                 "facade_url": self.state["endpoint"].removesuffix("/mcp")}
        store = DynamoStore(config["table_name"], self.session.resource("dynamodb"))
        with store.tx() as db:
            settings = get(db, "journey-platform")
            if (not settings or settings["account"] != self.config["account"]
                    or settings["region"] != self.config["region"]
                    or settings["mcp_onboarding"]["credential_prefix"] != config["deployment_prefix"]):
                raise ValueError("The existing Studio platform binding changed")
            configuration(value, settings)
            previous = get(db, "mcp-python-config")
            if previous is not None and previous != value:
                raise ValueError("An existing Python onboarding configuration must not be overwritten")
            operation = self.state["operations"].get("configure")
            if operation and operation["value"] != value:
                raise ValueError("Retained Python configuration intent changed")
            if previous is None:
                if operation:
                    raise RuntimeError("Configuration outcome is unverified; the write was not repeated")
                self.state["operations"]["configure"] = {"value": value, "phase": "INTENT"}
                self.save()
                put(db, "mcp-python-config", value)
        self.state["operations"]["configure"] = {"value": value, "phase": "VERIFIED"}
        self.save()
        print("Python MCP onboarding enabled in the existing Studio.", flush=True)

    def audit(self):
        cf = self.client("cloudformation")
        stack = cf.describe_stacks(StackName=self.state["facade"]["id"])["Stacks"][0]
        outputs = self.state["facade"]["outputs"]
        resources = cf.list_stack_resources(StackName=stack["StackId"])["StackResourceSummaries"]
        checks = {"stack_complete": stack["StackStatus"] in ("CREATE_COMPLETE", "UPDATE_COMPLETE")}
        for item in resources:
            name, kind, rid = item["LogicalResourceId"], item["ResourceType"], item["PhysicalResourceId"]
            if kind == "AWS::IAM::Role":
                value = self.client("iam").get_role(RoleName=rid)["Role"]
                tags = {t["Key"]: t["Value"] for t in value.get("Tags", [])}
            elif kind == "AWS::IAM::ManagedPolicy":
                iam = self.client("iam")
                tags = {t["Key"]: t["Value"] for t in iam.list_policy_tags(PolicyArn=rid)["Tags"]}
                required = {"auto-delete": "no", "project": "governed-agent-builder",
                    "deployment": self.config["python_onboarding"]["deployment_prefix"]}
                checks[name + "_ownership"] = all(tags.get(k) == v for k, v in required.items())
                attached = iam.list_attached_role_policies(RoleName=self.config["python_onboarding"]["worker_role_name"])
                checks[name + "_attached"] = any(p["PolicyArn"] == rid for p in attached["AttachedPolicies"])
            elif kind == "AWS::Lambda::Function":
                value = self.client("lambda").get_function_configuration(FunctionName=rid)
                tags = self.client("lambda").list_tags(Resource=value["FunctionArn"])["Tags"]
                checks[name + "_private_invocation"] = not self.client("lambda").list_function_url_configs(FunctionName=rid)["FunctionUrlConfigs"]
                checks[name + "_code"] = value["CodeSha256"] == __import__("base64").b64encode(bytes.fromhex(self.state["upload"]["digest"])).decode()
                checks[name + "_ready"] = value["State"] == "Active" and value["LastUpdateStatus"] == "Successful"
            elif kind == "AWS::Logs::LogGroup":
                arn = f"arn:aws:logs:{self.config['region']}:{self.config['account']}:log-group:{rid}"
                tags = self.client("logs").list_tags_for_resource(resourceArn=arn)["tags"]
            elif kind == "AWS::SecretsManager::Secret":
                tags = {t["Key"]: t["Value"] for t in self.client("secretsmanager").describe_secret(SecretId=rid)["Tags"]}
            elif kind == "AWS::CloudFront::Distribution":
                value = self.client("cloudfront").get_distribution(Id=rid)["Distribution"]
                tags = {t["Key"]: t["Value"] for t in self.client("cloudfront").list_tags_for_resource(
                    Resource=value["ARN"])["Tags"]["Items"]}
                checks["cloudfront_deployed"] = value["Status"] == "Deployed"
                behavior = value["DistributionConfig"]["DefaultCacheBehavior"]
                checks["https_and_no_cache"] = (behavior["ViewerProtocolPolicy"] == "https-only"
                    and behavior["CachePolicyId"] == "4135ea2d-6df8-44a3-9df3-4b5a84be39ad")
            elif kind == "AWS::ApiGatewayV2::Api":
                tags = self.client("apigatewayv2").get_api(ApiId=rid)["Tags"]
            elif kind == "AWS::ApiGatewayV2::Stage":
                tags = self.client("apigatewayv2").get_stage(ApiId=outputs["ApiId"], StageName="$default")["Tags"]
            else:
                continue
            checks[name + "_tag"] = tags.get("auto-delete") == "no"
        api = self.client("apigatewayv2")
        routes = api.get_routes(ApiId=outputs["ApiId"])["Items"]
        checks["all_routes_authenticated"] = bool(routes) and all(r["AuthorizationType"] == "CUSTOM" and r["AuthorizerId"] for r in routes)
        authorizers = api.get_authorizers(ApiId=outputs["ApiId"])["Items"]
        checks["no_cached_user_auth"] = bool(authorizers) and all(a["AuthorizerResultTtlInSeconds"] == 0 for a in authorizers)
        result = {"passed": all(checks.values()), "checks": checks}
        (self.directory / "security.json").write_text(json.dumps(result, indent=2))
        if not result["passed"]:
            raise RuntimeError("Facade security/tag checks failed; inspect security.json before registration")
        print("Facade security and tag audit passed.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["plan", "deploy", "audit", "configure"])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--runtime-state", type=Path)
    source.add_argument("--python-config", type=Path)
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--new-release", action="store_true", help="Update the same receipt-bound facade after a successful release")
    args = parser.parse_args()
    deployment = FacadeDeployment(args.runtime_state, args.state_dir, python_config=args.python_config)
    if args.action == "deploy":
        deployment.deploy(new_release=args.new_release)
    else:
        getattr(deployment, args.action)()
