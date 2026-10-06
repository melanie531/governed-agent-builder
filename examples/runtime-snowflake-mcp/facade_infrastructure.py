"""Optional, authenticated MCP facade for a private IAM Runtime."""
import re
import tempfile

from aws_cdk import App, BootstraplessSynthesizer, CfnOutput, CfnParameter, CfnResource, Environment, Stack
from aws_cdk.assertions import Template

from snowflake_mcp.database import Settings


def template(*, prefix, account, region, runtime_arn=None, snowflake_account=None,
             snowflake_role=None, warehouse=None, python_onboarding=None, reserved_concurrency=10):
    if (not re.fullmatch(r"[a-z][a-z0-9-]{2,29}", prefix)
            or not re.fullmatch(r"\d{12}", account)
            or not re.fullmatch(r"[a-z]{2}-[a-z]+-\d", region)):
        raise ValueError("Use an exact Runtime in the selected account and region")
    if reserved_concurrency is not None and (
            type(reserved_concurrency) is not int or not 1 <= reserved_concurrency <= 1000):
        raise ValueError("Reserved concurrency must be a positive integer or null for unreserved capacity")
    root = f"arn:aws:bedrock-agentcore:{region}:{account}:"
    if python_onboarding:
        config = python_onboarding
        artifact = config.get("artifact", {})
        if (set(config) != {"table_name", "runtime_prefix", "deployment_prefix", "worker_role_name", "artifact"}
                or not re.fullmatch(r"[A-Za-z0-9_.-]{3,255}", config["table_name"])
                or not re.fullmatch(r"[a-z][a-z0-9_]{2,19}", config["runtime_prefix"])
                or not re.fullmatch(r"[a-z][a-z0-9-]{2,45}", config["deployment_prefix"])
                or not re.fullmatch(r"[A-Za-z0-9+=,.@_-]{1,64}", config["worker_role_name"])
                or set(artifact) != {"bucket", "key", "version_id"}
                or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{2,62}", artifact["bucket"])
                or not re.fullmatch(r"mcp/python/[A-Za-z0-9/_-]+\.zip", artifact["key"])
                or not artifact["version_id"] or artifact["version_id"] == "null"
                or any(v is not None for v in (runtime_arn, snowflake_account, snowflake_role, warehouse))):
            raise ValueError("Bind Python onboarding to the existing Studio table, worker and versioned dependency bundle")
        runtime_scope = root + "runtime/" + config["runtime_prefix"] + "_*"
        route_path = "/mcp/{python_id}"
    else:
        if not isinstance(runtime_arn, str) or not re.fullmatch(re.escape(root + "runtime/") + r"[A-Za-z0-9_-]+", runtime_arn):
            raise ValueError("Use an exact Runtime in the selected account and region")
        Settings(snowflake_account, snowflake_role, warehouse)
        runtime_scope, route_path = runtime_arn, "/mcp"
    with tempfile.TemporaryDirectory(prefix="mcp-facade-cdk-") as out:
        app = App(outdir=out, analytics_reporting=False)
        stack = Stack(app, "Facade", stack_name=prefix + "-facade",
                      env=Environment(account=account, region=region),
                      synthesizer=BootstraplessSynthesizer())
        tags = [{"Key": "auto-delete", "Value": "no"}, {"Key": "project", "Value": prefix}]
        if python_onboarding:
            tags = [{"Key": k, "Value": v} for k, v in {
                "auto-delete": "no", "project": "governed-agent-builder", "deployment": config["deployment_prefix"]}.items()]
        def resource(name, kind, props):
            return CfnResource(stack, name, type=kind, properties=props)
        def arn(item):
            return item.get_att("Arn").to_string()
        code = {k: CfnParameter(stack, k, type="String").value_as_string
                for k in ("ArtifactBucket", "ArtifactKey", "ArtifactVersion")}
        secret = resource("OriginSecret", "AWS::SecretsManager::Secret", {
            "Name": prefix + "/mcp/origin", "GenerateSecretString": {"PasswordLength": 48, "ExcludePunctuation": True},
            "Tags": tags})
        functions = {}
        for name, handler in (("Authorizer", "authorize"), ("Bridge", "invoke")):
            function_name = prefix + "-mcp-" + name.lower()
            group = resource(name + "Logs", "AWS::Logs::LogGroup", {
                "LogGroupName": "/aws/lambda/" + function_name, "RetentionInDays": 14, "Tags": tags})
            statements = [{
                "Effect": "Allow", "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
                "Resource": arn(group),
            }]
            statements.append({"Effect": "Allow", "Action": ["secretsmanager:GetSecretValue"],
                "Resource": secret.ref} if name == "Authorizer" else {
                "Effect": "Allow", "Action": ["bedrock-agentcore:InvokeAgentRuntime"],
                "Resource": [runtime_scope, runtime_scope + "/runtime-endpoint/DEFAULT"]})
            if python_onboarding:
                statements.append({"Effect": "Allow", "Action": ["dynamodb:GetItem"],
                    "Resource": f"arn:aws:dynamodb:{region}:{account}:table/{config['table_name']}",
                    "Condition": {"ForAllValues:StringEquals": {"dynamodb:LeadingKeys": ["settings"]}}})
                statements.append({"Effect": "Allow", "Action": [
                    "bedrock-agentcore:GetAgentRuntimeEndpoint",
                    *(["bedrock-agentcore:InvokeAgentRuntime"] if name == "Authorizer" else [])],
                    "Resource": [runtime_scope, runtime_scope + "/runtime-endpoint/DEFAULT"]})
            role = resource(name + "Role", "AWS::IAM::Role", {
                "AssumeRolePolicyDocument": {"Version": "2012-10-17", "Statement": [{
                    "Effect": "Allow", "Action": "sts:AssumeRole", "Principal": {"Service": "lambda.amazonaws.com"}}]},
                "Policies": [{"PolicyName": "ScopedExecution", "PolicyDocument": {
                    "Version": "2012-10-17", "Statement": statements}}], "Tags": tags})
            functions[name] = resource(name, "AWS::Lambda::Function", {
                "FunctionName": function_name, "Runtime": "python3.13", "Handler": "facade." + handler,
                "Role": arn(role), "Timeout": 12 if name == "Authorizer" and not python_onboarding else 29,
                "MemorySize": 256,
                **({"ReservedConcurrentExecutions": reserved_concurrency}
                   if reserved_concurrency is not None else {}),
                "Code": {"S3Bucket": code["ArtifactBucket"], "S3Key": code["ArtifactKey"],
                         "S3ObjectVersion": code["ArtifactVersion"]},
                "Environment": {"Variables": (
                    {"STATE_TABLE": config["table_name"], "RUNTIME_ARN_PREFIX": runtime_scope[:-1],
                     **({"ORIGIN_SECRET_ARN": secret.ref} if name == "Authorizer" else {})}
                    if python_onboarding else
                    ({"ORIGIN_SECRET_ARN": secret.ref, "SNOWFLAKE_ACCOUNT": snowflake_account,
                        "SNOWFLAKE_ROLE": snowflake_role, "SNOWFLAKE_WAREHOUSE": warehouse}
                       if name == "Authorizer" else {"RUNTIME_ARN": runtime_arn}))}, "Tags": tags})
        api = resource("Api", "AWS::ApiGatewayV2::Api", {
            "Name": prefix + "-mcp", "ProtocolType": "HTTP",
            "Tags": {t["Key"]: t["Value"] for t in tags}})
        authorizer = resource("ApiAuthorizer", "AWS::ApiGatewayV2::Authorizer", {
            "ApiId": api.ref, "AuthorizerType": "REQUEST", "Name": "SnowflakeOAuth",
            "AuthorizerPayloadFormatVersion": "2.0", "EnableSimpleResponses": True,
            "AuthorizerResultTtlInSeconds": 0,
            "IdentitySource": ["$request.header.Authorization", "$request.header.X-Studio-Origin"],
            "AuthorizerUri": f"arn:aws:apigateway:{region}:lambda:path/2015-03-31/functions/"
                             + arn(functions["Authorizer"]) + "/invocations"})
        integration = resource("Integration", "AWS::ApiGatewayV2::Integration", {
            "ApiId": api.ref, "IntegrationType": "AWS_PROXY", "PayloadFormatVersion": "2.0",
            "IntegrationUri": arn(functions["Bridge"]), "TimeoutInMillis": 29000})
        route = resource("Route", "AWS::ApiGatewayV2::Route", {
            "ApiId": api.ref, "RouteKey": "ANY " + route_path, "Target": "integrations/" + integration.ref,
            "AuthorizationType": "CUSTOM", "AuthorizerId": authorizer.ref})
        stage = resource("Stage", "AWS::ApiGatewayV2::Stage", {
            "ApiId": api.ref, "StageName": "$default", "AutoDeploy": True,
            "DefaultRouteSettings": {"ThrottlingBurstLimit": 10, "ThrottlingRateLimit": 5},
            "Tags": {t["Key"]: t["Value"] for t in tags}})
        stage.add_dependency(route)
        for name, suffix in (("Authorizer", "/authorizers/" + authorizer.ref),
                             ("Bridge", "/*/*/mcp/*" if python_onboarding else "/*/*/mcp")):
            resource(name + "Permission", "AWS::Lambda::Permission", {
                "Action": "lambda:InvokeFunction", "FunctionName": arn(functions[name]),
                "Principal": "apigateway.amazonaws.com", "SourceAccount": account,
                "SourceArn": f"arn:aws:execute-api:{region}:{account}:" + api.ref + suffix})
        distribution = resource("Distribution", "AWS::CloudFront::Distribution", {
            "DistributionConfig": {
                "Enabled": True, "Comment": prefix + " authenticated MCP", "HttpVersion": "http2",
                "Origins": [{"Id": "api", "DomainName": api.ref + f".execute-api.{region}.amazonaws.com",
                    "CustomOriginConfig": {"OriginProtocolPolicy": "https-only", "OriginSSLProtocols": ["TLSv1.2"]},
                    "OriginCustomHeaders": [{"HeaderName": "X-Studio-Origin",
                        "HeaderValue": "{{resolve:secretsmanager:" + secret.ref + ":SecretString}}"}]}],
                "DefaultCacheBehavior": {
                    "TargetOriginId": "api", "ViewerProtocolPolicy": "https-only",
                    "AllowedMethods": ["GET", "HEAD", "OPTIONS", "PUT", "PATCH", "POST", "DELETE"],
                    "CachedMethods": ["GET", "HEAD"], "Compress": False,
                    "CachePolicyId": "4135ea2d-6df8-44a3-9df3-4b5a84be39ad",
                    "OriginRequestPolicyId": "b689b0a8-53d0-40ab-baf2-68738e2966ac"},
                "ViewerCertificate": {"CloudFrontDefaultCertificate": True, "MinimumProtocolVersion": "TLSv1.2_2021"}},
            "Tags": tags})
        distribution.add_dependency(stage)
        CfnOutput(stack, "McpEndpoint", value="https://" + distribution.get_att("DomainName").to_string() + "/mcp")
        CfnOutput(stack, "ApiId", value=api.ref)
        CfnOutput(stack, "DistributionId", value=distribution.ref)
        if python_onboarding:
            bucket = "arn:aws:s3:::" + artifact["bucket"]
            packages = bucket + "/mcp/python/*/runtime.zip"
            logs_scope = f"arn:aws:logs:{region}:{account}:log-group:/aws/bedrock-agentcore/runtimes/{config['runtime_prefix']}_*"
            execution = resource("PythonRuntimeRole", "AWS::IAM::Role", {
                "RoleName": prefix + "-runtime", "AssumeRolePolicyDocument": {"Version": "2012-10-17", "Statement": [{
                    "Effect": "Allow", "Principal": {"Service": "bedrock-agentcore.amazonaws.com"}, "Action": "sts:AssumeRole",
                    "Condition": {"StringEquals": {"aws:SourceAccount": account}, "ArnLike": {"aws:SourceArn": runtime_scope}}}]},
                "Policies": [{"PolicyName": "PythonPackageAndLogs", "PolicyDocument": {"Version": "2012-10-17", "Statement": [
                    {"Effect": "Allow", "Action": ["s3:GetObject", "s3:GetObjectVersion"], "Resource": packages},
                    {"Effect": "Allow", "Action": ["logs:CreateLogStream", "logs:DescribeLogStreams", "logs:PutLogEvents"],
                     "Resource": logs_scope + ":*"}]}}], "Tags": tags})
            required = {"aws:RequestTag/" + t["Key"]: t["Value"] for t in tags}
            directory = root + "workload-identity-directory/default"
            statements = [
                {"Effect": "Allow", "Action": ["bedrock-agentcore:CreateAgentRuntime"], "Resource": "*",
                 "Condition": {"StringEquals": {**required, "aws:RequestedRegion": region}}},
                {"Effect": "Allow", "Action": ["bedrock-agentcore:CreateAgentRuntimeEndpoint", "bedrock-agentcore:TagResource"],
                 "Resource": root + "runtime/*", "Condition": {"StringEquals": required}},
                {"Effect": "Allow", "Action": ["bedrock-agentcore:CreateWorkloadIdentity", "bedrock-agentcore:TagResource"],
                 "Resource": [directory, directory + "/workload-identity/*"], "Condition": {"StringEquals": required}},
                {"Effect": "Allow", "Action": ["bedrock-agentcore:ListAgentRuntimes", "logs:DescribeLogGroups"],
                 "Resource": "*", "Condition": {"StringEquals": {"aws:RequestedRegion": region}}},
                {"Effect": "Allow", "Action": ["bedrock-agentcore:GetAgentRuntime", "bedrock-agentcore:GetAgentRuntimeEndpoint",
                                              "bedrock-agentcore:ListTagsForResource", "bedrock-agentcore:InvokeAgentRuntime"],
                 "Resource": [runtime_scope, runtime_scope + "/runtime-endpoint/DEFAULT"]},
                {"Effect": "Allow", "Action": ["bedrock-agentcore:ListTagsForResource"],
                 "Resource": directory + "/workload-identity/" + config["runtime_prefix"] + "_*"},
                {"Effect": "Allow", "Action": ["iam:PassRole"], "Resource": arn(execution),
                 "Condition": {"StringEquals": {"iam:PassedToService": "bedrock-agentcore.amazonaws.com"}}},
                {"Effect": "Allow", "Action": ["s3:GetObjectVersion"], "Resource": bucket + "/" + artifact["key"],
                 "Condition": {"StringEquals": {"s3:VersionId": artifact["version_id"]}}},
                {"Effect": "Allow", "Action": ["s3:GetObject", "s3:GetObjectVersion"], "Resource": packages},
                {"Effect": "Allow", "Action": ["s3:ListBucket"], "Resource": bucket,
                 "Condition": {"StringLike": {"s3:prefix": "mcp/python/*/runtime.zip"}}},
                {"Effect": "Allow", "Action": ["s3:PutObject"], "Resource": packages,
                 "Condition": {"StringEquals": {"s3:x-amz-server-side-encryption": "AES256", "s3:RequestObjectTag/auto-delete": "no"},
                               "Null": {"s3:if-none-match": "false"}}},
                {"Effect": "Allow", "Action": ["s3:PutObjectTagging"], "Resource": packages,
                 "Condition": {"StringEquals": {"s3:RequestObjectTag/auto-delete": "no"}}},
                {"Effect": "Allow", "Action": ["logs:CreateLogGroup", "logs:TagResource", "logs:ListTagsForResource",
                                              "logs:PutRetentionPolicy"], "Resource": [logs_scope, logs_scope + ":*"]},
            ]
            policy = resource("PythonDeploymentPolicy", "AWS::IAM::ManagedPolicy", {
                "ManagedPolicyName": prefix + "-deployment", "Roles": [config["worker_role_name"]],
                "PolicyDocument": {"Version": "2012-10-17", "Statement": statements}})
            # CloudFormation has no ManagedPolicy Tags field. The installer must
            # tag and verify this policy before enabling Python uploads.
            CfnOutput(stack, "DeploymentPolicyArn", value=policy.ref)
            CfnOutput(stack, "RuntimeRoleArn", value=arn(execution))
        return Template.from_stack(stack).to_json()
