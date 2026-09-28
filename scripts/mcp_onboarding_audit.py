"""Read-only security/tag checks for an explicitly bound MCP deployment."""
import argparse
import base64
import json
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

from infra.resource_tags import TAG_PROPERTIES


def audit(state_path, runtime_id=None, existing=False, output=None):
    state = json.loads(state_path.read_text())
    if existing:
        state = {**state, "prefix": "governed-agent-builder-serverless",
                 "journey": {**state["journeyStack"], "stackId": state["journeyStack"]["id"]},
                 "gateway": {"gatewayId": state["journeyGateway"]["id"], "gatewayArn": state["journeyGateway"]["arn"]},
                 "gateway_identity_tagged": state["journeyGatewayIdentity"]["arn"],
                 "registry": state["mcpOnboardingRegistry"], "release": {"sha256": state["releaseSha256"]},
                 "qa-admin": state["journeyAdminQA"], "qa-business": state["journeyQA"]}
    target = state["target"]
    session = boto3.Session(profile_name=target["profile"], region_name=target["region"])
    if session.client("sts").get_caller_identity()["Account"] != target["account"]:
        raise ValueError("Audit account mismatch")
    clients, results = {}, []
    def client(name):
        if name not in clients:
            clients[name] = session.client(name)
        return clients[name]
    def tags(value):
        return value if isinstance(value, dict) else {t["Key"]: t["Value"] for t in value}
    def record(name, checks):
        results.append({"resource": name, "checks": checks, "pass": all(checks.values())})
    for section in ("artifacts", "journey", "app"):
        stack_id = state[section]["stackId"]
        expected_name = "governed-agent-builder-journey" if existing and section == "journey" else state["prefix"] + "-" + section
        if stack_id.split("/")[1] != expected_name:
            raise ValueError("Audit stack outside the bound deployment")
        stack = client("cloudformation").describe_stacks(StackName=stack_id)["Stacks"][0]
        record(section, {"terminal_success": stack["StackStatus"] in ("CREATE_COMPLETE", "UPDATE_COMPLETE"),
                         "retention_tag": tags(stack["Tags"]).get("auto-delete") == "no"})
        resources = client("cloudformation").list_stack_resources(StackName=stack_id)["StackResourceSummaries"]
        for resource in resources:
            kind, physical = resource["ResourceType"], resource["PhysicalResourceId"]
            label = section + "/" + resource["LogicalResourceId"]
            checks, actual_tags = {}, None
            if kind == "AWS::S3::Bucket":
                c = client("s3")
                actual_tags = c.get_bucket_tagging(Bucket=physical)["TagSet"]
                checks.update(public_access_blocked=all(c.get_public_access_block(Bucket=physical)["PublicAccessBlockConfiguration"].values()),
                              encrypted=bool(c.get_bucket_encryption(Bucket=physical)["ServerSideEncryptionConfiguration"]["Rules"]),
                              private_policy=not c.get_bucket_policy_status(Bucket=physical)["PolicyStatus"]["IsPublic"])
            elif kind == "AWS::DynamoDB::Table":
                c = client("dynamodb"); value = c.describe_table(TableName=physical)["Table"]
                actual_tags = c.list_tags_of_resource(ResourceArn=value["TableArn"])["Tags"]
                checks.update(active=value["TableStatus"] == "ACTIVE", encrypted=value.get("SSEDescription", {}).get("Status", "ENABLED") == "ENABLED")
            elif kind == "AWS::IAM::Role":
                c = client("iam"); value = c.get_role(RoleName=physical)["Role"]
                actual_tags = value.get("Tags", [])
                policies = [c.get_role_policy(RoleName=physical, PolicyName=p)["PolicyDocument"]
                            for p in c.list_role_policies(RoleName=physical)["PolicyNames"]]
                checks["no_administrator_wildcard"] = not any(
                    s["Effect"] == "Allow" and s["Action"] in ("*", ["*"]) for p in policies for s in p["Statement"])
                checks["inline_policy_limit"] = sum(len(json.dumps(p, separators=(",", ":"))) for p in policies) < 10240
            elif kind == "AWS::Lambda::Function":
                c = client("lambda"); value = c.get_function_configuration(FunctionName=physical)
                actual_tags = c.list_tags(Resource=value["FunctionArn"])["Tags"]
                checks.update(active=value["State"] == "Active" and value.get("LastUpdateStatus") == "Successful",
                              no_demo_mode=value.get("Environment", {}).get("Variables", {}).get("DEMO_MODE") != "1")
                if section == "app":
                    checks["release_digest"] = value["CodeSha256"] == base64.b64encode(bytes.fromhex(state["release"]["sha256"])).decode()
                try:
                    c.get_function_url_config(FunctionName=physical); checks["no_function_url"] = False
                except ClientError as exc:
                    if exc.response["Error"]["Code"] != "ResourceNotFoundException":
                        raise
                    checks["no_function_url"] = True
            elif kind == "AWS::Lambda::EventSourceMapping":
                c = client("lambda"); value = c.get_event_source_mapping(UUID=physical)
                actual_tags = c.list_tags(Resource=value["EventSourceMappingArn"])["Tags"]
            elif kind == "AWS::Logs::LogGroup":
                arn = f"arn:aws:logs:{target['region']}:{target['account']}:log-group:{physical}"
                actual_tags = client("logs").list_tags_for_resource(resourceArn=arn)["tags"]
            elif kind == "AWS::SQS::Queue":
                c = client("sqs")
                actual_tags = c.list_queue_tags(QueueUrl=physical)["Tags"]
                checks["encrypted"] = c.get_queue_attributes(QueueUrl=physical, AttributeNames=["SqsManagedSseEnabled"])["Attributes"]["SqsManagedSseEnabled"] == "true"
            elif kind == "AWS::CloudFront::Distribution":
                c = client("cloudfront"); value = c.get_distribution(Id=physical)["Distribution"]
                actual_tags = c.list_tags_for_resource(Resource=value["ARN"])["Tags"]["Items"]
                checks.update(deployed=value["Status"] == "Deployed",
                              private_s3_origin=all(o.get("OriginAccessControlId") for o in value["DistributionConfig"]["Origins"]["Items"] if "S3OriginConfig" in o))
            elif kind == "AWS::CloudFront::Function":
                c = client("cloudfront"); value = c.describe_function(Name=physical.rsplit("/", 1)[-1], Stage="LIVE")
                actual_tags = c.list_tags_for_resource(Resource=value["FunctionSummary"]["FunctionMetadata"]["FunctionARN"])["Tags"]["Items"]
            elif kind == "AWS::ApiGatewayV2::Api":
                c = client("apigatewayv2")
                actual_tags = c.get_api(ApiId=physical).get("Tags", {})
                routes = c.get_routes(ApiId=physical)["Items"]
                checks["business_routes_authorized"] = all(r.get("AuthorizationType") != "NONE" for r in routes
                    if r["RouteKey"].startswith(("ANY /api", "GET /api", "POST /api")) and r["RouteKey"] != "POST /api/auth/logout")
            elif kind == "AWS::ApiGatewayV2::Stage":
                api_id, stage_name = physical.split("|") if "|" in physical else (state["app"]["outputs"]["ApiEndpoint"].split("//")[1].split(".")[0], "$default")
                actual_tags = client("apigatewayv2").get_stage(ApiId=api_id, StageName=stage_name).get("Tags", {})
            elif kind == "AWS::CloudWatch::Alarm":
                c = client("cloudwatch")
                value = c.describe_alarms(AlarmNames=[physical])["MetricAlarms"][0]
                actual_tags = c.list_tags_for_resource(ResourceARN=value["AlarmArn"])["Tags"]
            elif kind == "AWS::Cognito::UserPoolClient":
                outputs = state["app"]["outputs"]
                value = client("cognito-idp").describe_user_pool_client(UserPoolId=outputs["UserPoolId"], ClientId=physical)["UserPoolClient"]
                checks.update(code_flow_only=value["AllowedOAuthFlows"] == ["code"], no_client_secret="ClientSecret" not in value,
                              bound_callback_only=value["CallbackURLs"] == [outputs["ApplicationOrigin"] + "/auth/callback"])
            elif kind == "AWS::Cognito::UserPool":
                actual_tags = client("cognito-idp").describe_user_pool(UserPoolId=physical)["UserPool"].get("UserPoolTags", {})
            if kind in TAG_PROPERTIES:
                checks["retention_tag"] = actual_tags is not None and tags(actual_tags).get("auto-delete") == "no"
            if checks:
                record(label, checks)
    c = client("bedrock-agentcore-control")
    gateway = c.get_gateway(gatewayIdentifier=state["gateway"]["gatewayId"])
    record("Gateway", {"ready": gateway["status"] == "READY", "iam_inbound": gateway["authorizerType"] == "AWS_IAM",
                       "retention_tag": c.list_tags_for_resource(resourceArn=gateway["gatewayArn"])["tags"].get("auto-delete") == "no"})
    record("GatewayIdentity", {"retention_tag": c.list_tags_for_resource(resourceArn=state["gateway_identity_tagged"])["tags"].get("auto-delete") == "no"})
    registry = client("agent-registry-control")
    reg = registry.get_registry(registryId=state["registry"]["registryId"])
    record("Registry", {"ready": reg["status"] == "READY", "manual_approval": not reg.get("approvalConfiguration", {}).get("autoApprovalRules"),
                        "retention_tag": registry.list_tags_for_resource(resourceArn=reg["registryArn"])["tags"].get("auto-delete") == "no"})
    page = registry.list_registry_records(registryId=state["registry"]["registryId"])
    if page.get("nextToken"):
        raise ValueError("Registry audit pagination required")
    for entry in page["registryRecords"]:
        record("RegistryRecord/" + entry["recordId"], {"retention_tag": registry.list_tags_for_resource(resourceArn=entry["recordArn"])["tags"].get("auto-delete") == "no"})
    for connection in state["journeyPlatform"]["mcp_onboarding"]["connections"]:
        provider = connection["configuration"].get("credentialProvider", {})
        for configuration in provider.values():
            if configuration.get("providerArn"):
                record("CredentialConnection/" + connection["id"], {
                    "retention_tag": c.list_tags_for_resource(resourceArn=configuration["providerArn"])["tags"].get("auto-delete") == "no"})
    for secret in state["journeyPlatform"]["mcp_onboarding"].get("secret_arns", []):
        value = client("secretsmanager").describe_secret(SecretId=secret)
        record("CredentialSecret/" + value["Name"], {
            "retention_tag": tags(value.get("Tags", [])).get("auto-delete") == "no"})
    if existing and state["journeyPlatform"]["mcp_onboarding"].get("credential_prefix"):
        from backend.dynamo_store import DynamoStore
        from backend.mcp_credentials import CredentialCloud
        store = DynamoStore(state["app"]["outputs"]["StateTable"], session.resource("dynamodb"))
        with store.tx() as db:
            requests = [json.loads(r["body"]) for r in db.select("settings") if r["key"].startswith("mcp-auth-request:")]
        credential_cloud = CredentialCloud(state["journeyPlatform"], session=session)
        for request in requests:
            try:
                if request["phase"] == "DELETED":
                    record("DeletedSelfServiceCredential/" + request["id"], {
                        "provider_absent": credential_cloud.management_read("provider_delete", request, {}),
                        "secret_absent_or_recoverable": credential_cloud.management_read("secret_delete", request, {})})
                    continue
                secret_arn = credential_cloud.read_secret(request)
                provider = credential_cloud.read_provider({**request, "secret_arn": secret_arn}) if secret_arn else None
                record("SelfServiceCredential/" + request["id"], {
                    "deployment_binding": request["deployment_prefix"] == state["journeyPlatform"]["mcp_onboarding"]["credential_prefix"],
                    "secret_identity_version_and_tags": bool(secret_arn),
                    "external_provider_binding_and_tags": bool(provider)})
            except Exception:
                record("SelfServiceCredential/" + request["id"], {"native_binding_verified": False})
    if runtime_id:
        value = c.get_agent_runtime(agentRuntimeId=runtime_id)
        if value["roleArn"] != state["journeyPlatform"]["runtime_role"]:
            raise ValueError("Runtime does not belong to the bound deployment role")
        endpoint = c.get_agent_runtime_endpoint(agentRuntimeId=runtime_id, endpointName="DEFAULT")
        record("TestAgentRuntime", {
            "ready": value["status"] == "READY",
            "iam_invocation": not value.get("authorizerConfiguration"),
            "retention_tag": c.list_tags_for_resource(resourceArn=value["agentRuntimeArn"])["tags"].get("auto-delete") == "no"})
        record("TestAgentEndpoint", {
            "ready": endpoint["status"] == "READY" and endpoint["liveVersion"] == value["agentRuntimeVersion"],
            "retention_tag": c.list_tags_for_resource(resourceArn=endpoint["agentRuntimeEndpointArn"])["tags"].get("auto-delete") == "no"})
        record("TestAgentIdentity", {
            "retention_tag": c.list_tags_for_resource(resourceArn=value["workloadIdentityDetails"]["workloadIdentityArn"])["tags"].get("auto-delete") == "no"})
        log_name = "/aws/bedrock-agentcore/runtimes/" + runtime_id + "-DEFAULT"
        log_arn = f"arn:aws:logs:{target['region']}:{target['account']}:log-group:{log_name}"
        group = next(g for g in client("logs").describe_log_groups(logGroupNamePrefix=log_name)["logGroups"] if g["logGroupName"] == log_name)
        record("TestAgentLogs", {
            "retention_14_days": group.get("retentionInDays") == 14,
            "retention_tag": client("logs").list_tags_for_resource(resourceArn=log_arn)["tags"].get("auto-delete") == "no"})
    for role in ("admin", "business"):
        for part in ("username", "password"):
            name = state["qa-" + role]["parameterPrefix"] + "/" + part
            record("QAParameter/" + role + "/" + part, {"retention_tag": tags(client("ssm").list_tags_for_resource(ResourceType="Parameter", ResourceId=name)["TagList"]).get("auto-delete") == "no"})
    report = {"pass": all(r["pass"] for r in results), "resource_checks": results}
    (output or state_path.parent / "security-after.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({"pass": report["pass"], "resources_checked": len(results),
                      "failures": [r for r in results if not r["pass"]]}))
    if not report["pass"]:
        raise RuntimeError("Deployment security/tag validation failed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--runtime-id")
    parser.add_argument("--existing", action="store_true", help="Audit the existing canonical Studio deployment")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    audit(args.state.resolve(), args.runtime_id, args.existing, args.output)
