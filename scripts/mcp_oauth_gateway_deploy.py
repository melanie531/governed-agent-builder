"""Deploy and activate an empty Cognito Gateway for generic per-user MCP OAuth."""
import argparse
import copy
import json
from pathlib import Path
import time

from botocore.exceptions import ClientError

from backend.dynamo_store import DynamoStore
from backend.foundation_runs import get, put
from foundation_harness.config import digest
from infra.mcp_oauth_gateway import template
from infra.resource_tags import validate_resource_tags
from scripts.deployment_target import target_arguments
from scripts.mcp_onboarding_deploy import Release, evidence_name


class UserGatewayRelease:
    def __init__(self, release, gateway_name, artifact_receipt):
        self.release, self.name = release, gateway_name
        state, account = release.state, release.target.binding["account"]
        region, outputs = release.target.binding["region"], state["app"]["outputs"]
        worker = release.client("lambda").get_function_configuration(FunctionName=outputs["WorkerFunction"])
        if not worker["Role"].startswith(f"arn:aws:iam::{account}:role/"):
            raise ValueError("Worker role account changed")
        self.config = {
            "account": account, "region": region, "gateway_name": gateway_name,
            "credential_prefix": state["journeyPlatform"]["mcp_onboarding"]["credential_prefix"],
            "table_name": outputs["StateTable"], "worker_role_name": worker["Role"].rsplit("/", 1)[-1],
            "issuer": f"https://cognito-idp.{region}.amazonaws.com/" + outputs["UserPoolId"],
            "client_id": outputs["ClientId"]}
        self.body = template(**self.config)
        validate_resource_tags(self.body["Resources"])
        self.artifact = json.loads(Path(artifact_receipt).read_text())["artifact_uploads"]["lambda"]
        if self.artifact["bucket"] != state["artifacts"]["outputs"]["Bucket"] or not self.artifact.get("version"):
            raise ValueError("Use a verified immutable Lambda artifact from this installation")
        head = release.client("s3").head_object(Bucket=self.artifact["bucket"], Key=self.artifact["key"],
                                                VersionId=self.artifact["version"])
        if head["Metadata"].get("sha256") != self.artifact["digest"]:
            raise ValueError("Artifact receipt differs from S3")
        self.parameters = {"ArtifactBucket": self.artifact["bucket"], "ArtifactKey": self.artifact["key"],
                           "ArtifactVersion": self.artifact["version"]}
        self.stack_name = gateway_name + "-gateway"
        self.return_url = outputs["ApplicationOrigin"] + "/oauth/callback"
        self.tags = {"auto-delete": "no", "project": "governed-agent-builder",
                     "deployment": self.config["credential_prefix"]}

    def plan(self):
        (self.release.evidence / "user-gateway-template.json").write_text(json.dumps(self.body, indent=2))
        self.release.client("cloudformation").validate_template(TemplateBody=json.dumps(self.body))
        print("Empty Cognito Gateway plan validated; providers and targets remain manual.", flush=True)

    def stack(self):
        try:
            return self.release.client("cloudformation").describe_stacks(StackName=self.stack_name)["Stacks"][0]
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ValidationError" and "does not exist" in exc.response["Error"]["Message"]:
                return None
            raise

    def deploy(self):
        self.plan()
        release, cf = self.release, self.release.client("cloudformation")
        request = {"StackName": self.stack_name, "TemplateBody": json.dumps(self.body),
                   "Parameters": [{"ParameterKey": k, "ParameterValue": v} for k, v in self.parameters.items()],
                   "Capabilities": ["CAPABILITY_IAM", "CAPABILITY_NAMED_IAM"],
                   "Tags": [{"Key": k, "Value": v} for k, v in self.tags.items()]}
        operation = "create-user-gateway"
        if self.stack() and operation not in release.receipt["operations"]:
            raise ValueError("Refusing to adopt an existing Gateway stack without its submission receipt")
        release.dispatch(operation, request, lambda: cf.create_stack(**request, ClientRequestToken=digest(request)))
        for _ in range(60):
            current = self.stack()
            if current and not current["StackStatus"].endswith("IN_PROGRESS"):
                break
            time.sleep(3)
        if not current or current["StackStatus"] != "CREATE_COMPLETE":
            raise RuntimeError("Gateway stack is not complete. Reconcile its saved creation; no request was repeated.")
        actual = cf.get_template(StackName=current["StackId"], TemplateStage="Original")["TemplateBody"]
        actual = json.loads(actual) if isinstance(actual, str) else actual
        if actual != self.body or {p["ParameterKey"]: p["ParameterValue"] for p in current["Parameters"]} != self.parameters:
            raise ValueError("Gateway stack differs from the retained template")
        outputs = {o["OutputKey"]: o["OutputValue"] for o in current["Outputs"]}
        release.target.save("mcpOAuthGateway", {"stack_id": current["StackId"], "outputs": outputs, "config": self.config})
        print("Empty Cognito Gateway CREATE_COMPLETE.", flush=True)

    def generated(self):
        outputs = self.release.state["mcpOAuthGateway"]["outputs"]
        native = self.release.client("bedrock-agentcore-control").get_gateway(gatewayIdentifier=outputs["GatewayId"])
        if (native["gatewayArn"] != outputs["GatewayArn"] or native["gatewayUrl"] != outputs["GatewayUrl"]
                or native["status"] != "READY" or native["authorizerType"] != "CUSTOM_JWT"
                or native["authorizerConfiguration"] != {"customJWTAuthorizer": {
                    "discoveryUrl": self.config["issuer"] + "/.well-known/openid-configuration",
                    "allowedClients": [self.config["client_id"]], "allowedScopes": ["openid"]}}):
            raise ValueError("Cognito Gateway binding changed")
        return outputs, native

    def configure(self):
        release, control = self.release, self.release.client("bedrock-agentcore-control")
        outputs, native = self.generated()
        identity = native["workloadIdentityDetails"]["workloadIdentityArn"]
        name = identity.rsplit("/", 1)[-1]
        if not name.startswith(self.name + "-"):
            raise ValueError("Gateway workload identity is not owned by this stack")
        before = control.get_workload_identity(name=name).get("allowedResourceOauth2ReturnUrls", [])
        if before not in ([], [self.return_url]):
            raise ValueError("The Gateway already has unrelated callback URLs")
        request = {"name": name, "allowedResourceOauth2ReturnUrls": [self.return_url]}
        release.dispatch("gateway-callback", request, lambda: control.update_workload_identity(**request))
        request = {"resourceArn": identity, "tags": self.tags}
        release.dispatch("gateway-identity-tags", request, lambda: control.tag_resource(**request))
        request = {"PolicyArn": outputs["OnboardingPolicyArn"],
                   "Tags": [{"Key": k, "Value": v} for k, v in self.tags.items()]}
        release.dispatch("gateway-policy-tags", request, lambda: release.client("iam").tag_policy(**request))
        self.audit()

    def audit(self):
        outputs, native = self.generated()
        release, control = self.release, self.release.client("bedrock-agentcore-control")
        identity = native["workloadIdentityDetails"]["workloadIdentityArn"]
        gateway_tags = control.list_tags_for_resource(resourceArn=outputs["GatewayArn"])["tags"]
        identity_tags = control.list_tags_for_resource(resourceArn=identity)["tags"]
        checks = {
            "cognito_inbound": True,
            "mcp_protocol_versions": native.get("protocolConfiguration", {}).get("mcp", {}).get(
                "supportedVersions") == ["2025-03-26", "2025-11-25"],
            "callback": control.get_workload_identity(name=identity.rsplit("/", 1)[-1]).get(
                "allowedResourceOauth2ReturnUrls") == [self.return_url],
            "gateway_tags": all(gateway_tags.get(k) == v for k, v in self.tags.items()),
            "identity_tags": all(identity_tags.get(k) == v for k, v in self.tags.items()),
            "managed_policy_tags": {t["Key"]: t["Value"] for t in release.client("iam").list_policy_tags(
                PolicyArn=outputs["OnboardingPolicyArn"])["Tags"]}.get("auto-delete") == "no",
            "worker_policy_attached": outputs["OnboardingPolicyArn"] in [
                policy["PolicyArn"] for policy in release.client("iam").list_attached_role_policies(
                    RoleName=self.config["worker_role_name"])["AttachedPolicies"]],
            "request_grant_interceptor": native.get("interceptorConfigurations") == [{
                "interceptor": {"lambda": {"arn": outputs["InterceptorArn"]}},
                "interceptionPoints": ["REQUEST"], "inputConfiguration": {"passRequestHeaders": True}}],
        }
        resources = release.client("cloudformation").list_stack_resources(
            StackName=release.state["mcpOAuthGateway"]["stack_id"])["StackResourceSummaries"]
        for resource in resources:
            rid, kind = resource["PhysicalResourceId"], resource["ResourceType"]
            if kind == "AWS::IAM::Role":
                tags = release.client("iam").get_role(RoleName=rid)["Role"].get("Tags", [])
                checks[resource["LogicalResourceId"] + "_tag"] = {t["Key"]: t["Value"] for t in tags}.get("auto-delete") == "no"
            elif kind == "AWS::Lambda::Function":
                tags = release.client("lambda").list_tags(Resource=outputs["InterceptorArn"])["Tags"]
                checks["interceptor_tag"] = tags.get("auto-delete") == "no"
                checks["no_function_urls"] = not release.client("lambda").list_function_url_configs(FunctionName=rid)["FunctionUrlConfigs"]
            elif kind == "AWS::Logs::LogGroup":
                arn = f"arn:aws:logs:{self.config['region']}:{self.config['account']}:log-group:{rid}"
                checks["log_tag"] = release.client("logs").list_tags_for_resource(resourceArn=arn)["tags"].get("auto-delete") == "no"
        result = {"passed": all(checks.values()), "checks": checks}
        (release.evidence / "user-gateway-security.json").write_text(json.dumps(result, indent=2))
        if not result["passed"]:
            raise RuntimeError("User Gateway security/tag audit failed")
        print("User Gateway security, callback and tag audit passed.", flush=True)

    def activate(self):
        self.audit()
        release = self.release
        outputs, _ = self.generated()
        route = {"gateway_id": outputs["GatewayId"], "gateway_url": outputs["GatewayUrl"],
                 "issuer": self.config["issuer"], "client_id": self.config["client_id"]}
        store = DynamoStore(self.config["table_name"], release.target.session.resource("dynamodb"))
        intent = release.receipt.get("user_gateway_activation")
        if intent is None:
            with store.tx() as db:
                before = get(db, "mcp-onboarding")
                journey = get(db, "journey-platform")
            if before.get("oauth_gateway") not in (None, route):
                raise ValueError("An unrelated user Gateway is already configured")
            intent = {"before": before, "journey_before": journey,
                      "after": {**before, "oauth_gateway": route}}
            intent["journey_after"] = {**journey, "mcp_onboarding": intent["after"]}
            release.receipt["user_gateway_activation"] = intent
            release.save()
        if intent["after"]["oauth_gateway"] != route:
            raise ValueError("Retained activation differs from this Gateway")
        with store.tx() as db:
            current, journey = get(db, "mcp-onboarding"), get(db, "journey-platform")
            if current == intent["before"] and journey == intent["journey_before"]:
                put(db, "mcp-onboarding", intent["after"])
                put(db, "journey-platform", intent["journey_after"])
            elif current != intent["after"] or journey != intent["journey_after"]:
                raise ValueError("Platform settings changed after activation was planned")
        release.target.save("journeyPlatform", copy.deepcopy(intent["journey_after"]))
        intent["phase"] = "VERIFIED"
        release.save()
        print("Generic user OAuth onboarding activated. No provider or target was registered.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    target_arguments(parser)
    parser.add_argument("action", choices=["plan", "deploy", "configure", "audit", "activate"])
    parser.add_argument("--gateway-name", default="gab-user-tools")
    parser.add_argument("--evidence-name", required=True, type=evidence_name)
    parser.add_argument("--artifact-receipt", required=True, type=Path)
    args = parser.parse_args()
    getattr(UserGatewayRelease(Release(args), args.gateway_name, args.artifact_receipt), args.action)()
