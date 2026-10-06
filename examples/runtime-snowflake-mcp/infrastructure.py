"""Independent CDK stacks. No platform installation hooks or bundled credentials."""
import re
import tempfile

from aws_cdk import App, CfnOutput, CfnParameter, Environment, Stack, Tags
from aws_cdk import aws_bedrockagentcore as agentcore, aws_iam as iam, aws_logs as logs, aws_s3 as s3
from aws_cdk.assertions import Template

from snowflake_mcp.database import Settings
from snowflake_mcp.identity import USER_TOKEN_HEADER
from snowflake_mcp.gateway_token import SNOWFLAKE_TOKEN_HEADER
from oauth_infrastructure import runtime_oauth


def templates(*, prefix, account, region, gateway_role_arn, snowflake_account, snowflake_role, warehouse,
              oauth=None, auth_source="runtime"):
    if not re.fullmatch(r"[a-z][a-z0-9-]{2,29}", prefix):
        raise ValueError("prefix must contain 3-30 lowercase letters, digits or hyphens")
    if not re.fullmatch(r"\d{12}", account) or not re.fullmatch(r"[a-z]{2}-[a-z]+-\d", region):
        raise ValueError("Invalid AWS account or region")
    if not re.fullmatch(r"arn:aws:iam::" + account + r":role/[A-Za-z0-9+=,.@_-]+", gateway_role_arn):
        raise ValueError("Use an existing Gateway role ARN in the selected account, without a role path")
    Settings(snowflake_account, snowflake_role, warehouse)
    if auth_source not in ("runtime", "gateway") or auth_source == "gateway" and oauth:
        raise ValueError("Gateway-supplied tokens and Runtime token acquisition are separate modes")
    user_env, user_policy = runtime_oauth(account, region, oauth, snowflake_role) if oauth else ({}, [])
    if auth_source == "gateway":
        user_env = {"SNOWFLAKE_AUTH_SOURCE": "gateway"}
    with tempfile.TemporaryDirectory(prefix="runtime-mcp-cdk-") as out:
        app = App(outdir=out, analytics_reporting=False)
        env = Environment(account=account, region=region)
        artifacts = Stack(app, "Artifacts", stack_name=prefix + "-artifacts", env=env)
        bucket = s3.Bucket(artifacts, "Artifacts", versioned=True,
                           encryption=s3.BucketEncryption.S3_MANAGED,
                           block_public_access=s3.BlockPublicAccess.BLOCK_ALL, enforce_ssl=True)
        CfnOutput(artifacts, "Bucket", value=bucket.bucket_name)
        stack = Stack(app, "Runtime", stack_name=prefix + "-runtime", env=env)
        bucket_name = CfnParameter(stack, "ArtifactBucket", type="String").value_as_string
        key = CfnParameter(stack, "ArtifactKey", type="String").value_as_string
        version = CfnParameter(stack, "ArtifactVersion", type="String").value_as_string
        runtime_name = prefix.replace("-", "_") + "_mcp"
        log_prefix = f"arn:aws:logs:{region}:{account}:log-group:/aws/bedrock-agentcore/runtimes/{runtime_name}-*"
        execution = iam.CfnRole(stack, "ExecutionRole", role_name=prefix + "-execution",
            assume_role_policy_document={"Version": "2012-10-17", "Statement": [{
                "Effect": "Allow", "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole", "Condition": {
                    "StringEquals": {"aws:SourceAccount": account},
                    "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{region}:{account}:runtime/{runtime_name}-*"}}}]},
            policies=[iam.CfnRole.PolicyProperty(policy_name="RuntimeArtifactsAndLogs",
                policy_document={"Version": "2012-10-17", "Statement": [
                    {"Effect": "Allow", "Action": ["s3:GetObject", "s3:GetObjectVersion"],
                     "Resource": Stack.of(stack).format_arn(service="s3", region="", account="", resource=bucket_name, resource_name=key)},
                    {"Effect": "Allow", "Action": ["logs:CreateLogStream", "logs:DescribeLogStreams", "logs:PutLogEvents"],
                     "Resource": log_prefix + ":*"}, *user_policy]})])
        runtime = agentcore.CfnRuntime(stack, "McpRuntime", agent_runtime_name=runtime_name,
            description="Optional read-only Snowflake MCP using per-user Snowflake OAuth",
            agent_runtime_artifact=agentcore.CfnRuntime.AgentRuntimeArtifactProperty(
                code_configuration=agentcore.CfnRuntime.CodeConfigurationProperty(
                    code=agentcore.CfnRuntime.CodeProperty(s3=agentcore.CfnRuntime.S3LocationProperty(
                        bucket=bucket_name, prefix=key, version_id=version)),
                    runtime="PYTHON_3_13", entry_point=["main.py"])),
            role_arn=execution.attr_arn, protocol_configuration="MCP",
            network_configuration=agentcore.CfnRuntime.NetworkConfigurationProperty(network_mode="PUBLIC"),
            lifecycle_configuration=agentcore.CfnRuntime.LifecycleConfigurationProperty(
                idle_runtime_session_timeout=60, max_lifetime=900),
            request_header_configuration=agentcore.CfnRuntime.RequestHeaderConfigurationProperty(
                request_header_allowlist=[SNOWFLAKE_TOKEN_HEADER if auth_source == "gateway" else USER_TOKEN_HEADER])
                if oauth or auth_source == "gateway" else None,
            environment_variables={"SNOWFLAKE_ACCOUNT": snowflake_account,
                                   "SNOWFLAKE_ROLE": snowflake_role, "SNOWFLAKE_WAREHOUSE": warehouse, **user_env},
            tags={"auto-delete": "no", "project": prefix})
        logs.LogGroup(stack, "RuntimeLogs",
            log_group_name="/aws/bedrock-agentcore/runtimes/" + runtime.attr_agent_runtime_id + "-DEFAULT",
            retention=logs.RetentionDays.TWO_WEEKS)
        iam.CfnPolicy(stack, "GatewayInvoke", policy_name=prefix + "-invoke",
            roles=[gateway_role_arn.rsplit("/", 1)[-1]],
            policy_document={"Version": "2012-10-17", "Statement": [{
                "Effect": "Allow", "Action": "bedrock-agentcore:InvokeAgentRuntime",
                "Resource": [runtime.attr_agent_runtime_arn, runtime.attr_agent_runtime_arn + "/runtime-endpoint/DEFAULT"]}]})
        CfnOutput(stack, "RuntimeArn", value=runtime.attr_agent_runtime_arn)
        CfnOutput(stack, "RuntimeId", value=runtime.attr_agent_runtime_id)
        CfnOutput(stack, "ExecutionRoleArn", value=execution.attr_arn)
        for current in (artifacts, stack):
            Tags.of(current).add("auto-delete", "no")
            Tags.of(current).add("project", prefix)
        return Template.from_stack(artifacts).to_json(), Template.from_stack(stack).to_json()
