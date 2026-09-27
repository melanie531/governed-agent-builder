"""Project-owned Foundation Runtime, evidence storage and Gateway workload roles."""
from infra.serverless import attr, bucket, ref, sub, tls_policy


def statement(actions, resource):
    return {"Effect": "Allow", "Action": actions, "Resource": resource}


def role(service, statements, source):
    return {"Type": "AWS::IAM::Role", "Properties": {
        "AssumeRolePolicyDocument": {"Version": "2012-10-17", "Statement": [{
            "Effect": "Allow", "Principal": {"Service": service}, "Action": "sts:AssumeRole",
            "Condition": {"StringEquals": {"aws:SourceAccount": ref("AWS::AccountId")},
                          "ArnLike": {"aws:SourceArn": source}}}]},
        "Policies": [{"PolicyName": "JourneyScope", "PolicyDocument": {"Version": "2012-10-17", "Statement": statements}}]}}


def template(provider_arn=None, secret_arn=None, specialist_runtime_arns=()):
    """provider/secret are the optional Tavily API-key binding; specialist_runtime_arns are
    platform-curated MCP Runtimes the Gateway may SigV4-invoke as mcpServer targets."""
    resources = {"Evidence": bucket(), "EvidenceTLS": tls_policy("Evidence"),
                 "Traces": {"Type": "AWS::Logs::LogGroup", "DeletionPolicy": "Retain",
                            "Properties": {"LogGroupName": "/governed-agent-builder/journey/traces", "RetentionInDays": 14}},
                 "KnowledgeLogs": {"Type": "AWS::Logs::LogGroup", "DeletionPolicy": "Retain",
                                   "Properties": {"LogGroupName": "/governed-agent-builder/journey/knowledge", "RetentionInDays": 14}}}
    resources["Evidence"]["Properties"]["LifecycleConfiguration"] = {"Rules": [{
        "Id": "EvaluationRetention", "Status": "Enabled", "Prefix": "journey/evidence/",
        "ExpirationInDays": 30, "NoncurrentVersionExpiration": {"NoncurrentDays": 30}}]}
    resources["RuntimeRole"] = role("bedrock-agentcore.amazonaws.com", [
        statement(["s3:GetObject", "s3:GetObjectVersion"], sub("${Evidence.Arn}/journey/manifests/*")),
        statement(["s3:PutObject"], sub("${Evidence.Arn}/journey/evidence/*")),
        statement(["s3:GetObject", "s3:GetObjectVersion"], sub("arn:${AWS::Partition}:s3:::${ArtifactBucket}/journey/foundation/*")),
        statement(["logs:CreateLogStream", "logs:PutLogEvents"], attr("Traces")),
        statement(["logs:CreateLogGroup"], sub("arn:${AWS::Partition}:logs:${AWS::Region}:${AWS::AccountId}:log-group:/aws/bedrock-agentcore/runtimes/gab_journey_*")),
        statement(["logs:CreateLogStream", "logs:DescribeLogStreams", "logs:PutLogEvents"],
                  sub("arn:${AWS::Partition}:logs:${AWS::Region}:${AWS::AccountId}:log-group:/aws/bedrock-agentcore/runtimes/gab_journey_*:*")),
        # Platform admission pins approved model IDs; these model-only permissions
        # let operators publish additional Converse routes without replacing code.
        statement(["bedrock:InvokeModel"], [
            sub("arn:${AWS::Partition}:bedrock:*::foundation-model/*"),
            sub("arn:${AWS::Partition}:bedrock:*:${AWS::AccountId}:inference-profile/*"),
            sub("arn:${AWS::Partition}:bedrock:*:${AWS::AccountId}:application-inference-profile/*")]),
        statement(["bedrock-agentcore:InvokeGateway"],
                  sub("arn:${AWS::Partition}:bedrock-agentcore:${AWS::Region}:${AWS::AccountId}:gateway/gab-journey-tools-*")),
    ], sub("arn:${AWS::Partition}:bedrock-agentcore:${AWS::Region}:${AWS::AccountId}:runtime/gab_journey_*"))
    # Lambda assume-role does not supply SourceArn; invocation is separately scoped.
    resources["KnowledgeRole"] = {"Type": "AWS::IAM::Role", "Properties": {
        "AssumeRolePolicyDocument": {"Version": "2012-10-17", "Statement": [{
            "Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]},
        "Policies": [{"PolicyName": "KnowledgeLogs", "PolicyDocument": {"Version": "2012-10-17", "Statement": [
            statement(["logs:CreateLogStream", "logs:PutLogEvents"], attr("KnowledgeLogs"))]}}]}}
    resources["Knowledge"] = {"Type": "AWS::Lambda::Function", "Properties": {
        "Runtime": "python3.13", "Architectures": ["arm64"], "Handler": "tools.knowledge_search.handler",
        "Role": attr("KnowledgeRole"), "MemorySize": 128, "Timeout": 15,
        "Code": {"S3Bucket": ref("ArtifactBucket"), "S3Key": ref("ArtifactKey")},
        "LoggingConfig": {"LogGroup": ref("KnowledgeLogs")}}}
    api_key = [
        statement(["bedrock-agentcore:GetResourceApiKey"], [
            provider_arn,
            sub("arn:${AWS::Partition}:bedrock-agentcore:${AWS::Region}:${AWS::AccountId}:token-vault/default"),
            sub("arn:${AWS::Partition}:bedrock-agentcore:${AWS::Region}:${AWS::AccountId}:workload-identity-directory/default"),
            sub("arn:${AWS::Partition}:bedrock-agentcore:${AWS::Region}:${AWS::AccountId}:workload-identity-directory/default/workload-identity/gab-journey-tools-*")]),
        statement(["secretsmanager:GetSecretValue"], secret_arn)] if provider_arn else []
    specialists = [statement(["bedrock-agentcore:InvokeAgentRuntime"],
                             [arn + suffix for arn in specialist_runtime_arns for suffix in ("", "/*")])] if specialist_runtime_arns else []
    resources["GatewayRole"] = role("bedrock-agentcore.amazonaws.com", [
        statement(["lambda:InvokeFunction"], attr("Knowledge")),
        *api_key, *specialists,
        statement(["bedrock-agentcore:GetWorkloadAccessToken"], [
            sub("arn:${AWS::Partition}:bedrock-agentcore:${AWS::Region}:${AWS::AccountId}:workload-identity-directory/default"),
            sub("arn:${AWS::Partition}:bedrock-agentcore:${AWS::Region}:${AWS::AccountId}:workload-identity-directory/default/workload-identity/gab-journey-tools-*")]),
    ], sub("arn:${AWS::Partition}:bedrock-agentcore:${AWS::Region}:${AWS::AccountId}:gateway/gab-journey-tools-*"))
    return {"AWSTemplateFormatVersion": "2010-09-09", "Description": "Governed Agent Builder self-service Foundation and Gateway scope",
            "Parameters": {"ArtifactBucket": {"Type": "String"}, "ArtifactKey": {"Type": "String"}},
            "Resources": resources, "Outputs": {
                "EvidenceBucket": {"Value": ref("Evidence")}, "RuntimeRole": {"Value": attr("RuntimeRole")},
                "GatewayRole": {"Value": attr("GatewayRole")}, "KnowledgeFunction": {"Value": attr("Knowledge")},
                "TraceLogGroup": {"Value": ref("Traces")}}}


def configure_app(resources, settings):
    """Attach the reviewed Journey permissions to the existing hosted application."""
    account, region = settings["account"], settings["region"]
    runtime = f"arn:aws:bedrock-agentcore:{region}:{account}:runtime/gab_journey_*"
    bucket_arn = "arn:aws:s3:::" + settings["bucket"]
    worker = resources["Worker"]["Properties"]
    worker["Timeout"] = 300
    worker["MemorySize"] = 1024
    resources["Jobs"]["Properties"]["VisibilityTimeout"] = 1800
    for name in ("Business", "Auth", "Authorizer", "Worker"):
        resources[name]["Properties"]["Environment"]["Variables"]["JOURNEY_ENABLED"] = "1"
    worker_statements = [
        statement(["bedrock-agentcore:CreateAgentRuntime"], "*"),
        statement(["bedrock-agentcore:GetAgentRuntime", "bedrock-agentcore:GetAgentRuntimeEndpoint", "bedrock-agentcore:TagResource",
                   "bedrock-agentcore:CreateAgentRuntimeEndpoint", "bedrock-agentcore:DeleteAgentRuntime",
                   "bedrock-agentcore:DeleteAgentRuntimeEndpoint",
                   "bedrock-agentcore:ListTagsForResource",
                   "bedrock-agentcore:InvokeAgentRuntime"], [runtime, runtime + "/runtime-endpoint/*"]),
        statement(["iam:PassRole"], settings["runtime_role"]),
        statement(["s3:GetObject", "s3:GetObjectVersion", "s3:PutObject"], bucket_arn + "/journey/*"),
        # Built-in metadata uses a global ARN, while IAM also checks the
        # region/account-scoped evaluator ARN for GetEvaluator/Evaluate.
        statement(["bedrock-agentcore:Evaluate", "bedrock-agentcore:GetEvaluator"], [
            settings["evaluator_arn"],
            f"arn:aws:bedrock-agentcore:{region}:{account}:evaluator/{settings['evaluator_id']}"]),
        statement(["s3:GetObject", "s3:GetObjectVersion"],
                  "arn:aws:s3:::" + settings["artifact"]["bucket"] + "/journey/foundation/*"),
        statement(["bedrock-agentcore:ListAgentRuntimes"], "*"),
        {**statement(["s3:ListBucketVersions"], bucket_arn),
         "Condition": {"StringLike": {"s3:prefix": ["journey/manifests/*", "journey/evidence/*", "journey/evaluations/*"]}}},
        statement(["s3:DeleteObject", "s3:DeleteObjectVersion"], [
            bucket_arn + "/journey/manifests/*", bucket_arn + "/journey/evidence/*", bucket_arn + "/journey/evaluations/*"]),
        {**statement(["logs:DescribeLogGroups"], "*"), "Condition": {"StringEquals": {"aws:RequestedRegion": region}}},
        statement(["logs:DeleteLogGroup"], f"arn:aws:logs:{region}:{account}:log-group:/aws/bedrock-agentcore/runtimes/gab_journey_*:*"),
        statement(["logs:DeleteLogStream"], f"arn:aws:logs:{region}:{account}:log-group:{settings['log_group']}:log-stream:agent-*"),
        # CreateAgentRuntime authorizes DEFAULT endpoint creation and tagging
        # against runtime/* before its ID exists. Keep mandatory project tags.
        {**statement(["bedrock-agentcore:CreateAgentRuntimeEndpoint", "bedrock-agentcore:TagResource"],
                     f"arn:aws:bedrock-agentcore:{region}:{account}:runtime/*"),
         "Condition": {"StringEquals": {"aws:RequestTag/project": "governed-agent-builder",
                                         "aws:RequestTag/journey": "create-agent"}}},
        {**statement(["bedrock-agentcore:TagResource", "bedrock-agentcore:CreateWorkloadIdentity"],
                     f"arn:aws:bedrock-agentcore:{region}:{account}:workload-identity-directory/default/workload-identity/*"),
         "Condition": {"StringEquals": {"aws:RequestTag/project": "governed-agent-builder",
                                         "aws:RequestTag/journey": "create-agent"}}},
        {**statement(["bedrock-agentcore:CreateWorkloadIdentity", "bedrock-agentcore:TagResource"],
                     f"arn:aws:bedrock-agentcore:{region}:{account}:workload-identity-directory/default"),
         "Condition": {"StringEquals": {"aws:RequestTag/project": "governed-agent-builder",
                                         "aws:RequestTag/journey": "create-agent"}}},
        statement(["bedrock-agentcore:DeleteWorkloadIdentity"], [
            f"arn:aws:bedrock-agentcore:{region}:{account}:workload-identity-directory/default",
            f"arn:aws:bedrock-agentcore:{region}:{account}:workload-identity-directory/default/workload-identity/gab_journey_*"]),
    ]
    worker_statements[0]["Condition"] = {"StringEquals": {"aws:RequestTag/project": "governed-agent-builder",
                                                          "aws:RequestTag/journey": "create-agent"}}
    worker_statements[2]["Condition"] = {"StringEquals": {"iam:PassedToService": "bedrock-agentcore.amazonaws.com"}}
    resources["WorkerRole"]["Properties"]["Policies"].append({
        "PolicyName": "JourneyDeployment", "PolicyDocument": {"Version": "2012-10-17", "Statement": worker_statements}})
    registry = settings.get("registry_arn")
    if settings.get("admin_enabled") or registry:
        expected = f"arn:aws:agent-registry:{region}:{account}:registry/"
        if registry and (not registry.startswith(expected) or "/" in registry[len(expected):]):
            raise ValueError("Registry must belong to this platform account and region")
        registry_permissions = [statement(["agent-registry:GetRegistry", "agent-registry:CreateRegistryRecord",
            "agent-registry:GetRegistryRecord", "agent-registry:SubmitRegistryRecordForApproval",
            "agent-registry:UpdateRegistryRecordStatus"], [registry, registry + "/record/*"])] if registry else []
        resources["BusinessRole"]["Properties"]["Policies"].append({
            "PolicyName": "PlatformAdministration", "PolicyDocument": {"Version": "2012-10-17", "Statement": [
                *registry_permissions,
                statement(["bedrock:ListFoundationModels", "bedrock:ListInferenceProfiles",
                           "cloudwatch:GetMetricData", "ce:GetCostAndUsage", "ce:ListCostAllocationTags"], "*"),
                statement(["bedrock:InvokeModel"], [
                    "arn:aws:bedrock:*::foundation-model/*",
                    f"arn:aws:bedrock:*:{account}:inference-profile/*",
                    f"arn:aws:bedrock:*:{account}:application-inference-profile/*"]),
            ]}})
    # Initial workspace grants are sourced from the published Catalog, not code.
    for name in ("Auth", "Authorizer", "Business", "Worker"):
        resources[name + "Role"]["Properties"]["Policies"].append({
            "PolicyName": "JourneyCatalogGrants", "PolicyDocument": {"Version": "2012-10-17", "Statement": [
                {**statement(["dynamodb:GetItem", "dynamodb:Query", "dynamodb:ConditionCheckItem"], attr("State")),
                 "Condition": {"ForAllValues:StringEquals": {"dynamodb:LeadingKeys": ["components", "settings"]}}},
                {**statement(["dynamodb:PutItem", "dynamodb:UpdateItem"], attr("State")),
                 "Condition": {"ForAllValues:StringEquals": {"dynamodb:LeadingKeys": ["settings"]}}},
            ]}})
