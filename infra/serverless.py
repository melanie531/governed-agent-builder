"""Isolated managed-services CloudFormation. No VPC or policy exceptions."""
import copy
from infra.identity import template as identity_template
from infra.resource_tags import apply_resource_tags


def ref(name): return {"Ref": name}
def attr(name, field="Arn"): return {"Fn::GetAtt": [name, field]}
def sub(value): return {"Fn::Sub": value}


def bucket():
    return {"Type": "AWS::S3::Bucket", "DeletionPolicy": "Retain", "UpdateReplacePolicy": "Retain", "Properties": {
        "PublicAccessBlockConfiguration": {"BlockPublicAcls": True, "BlockPublicPolicy": True, "IgnorePublicAcls": True, "RestrictPublicBuckets": True},
        "BucketEncryption": {"ServerSideEncryptionConfiguration": [{"ServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]},
        "OwnershipControls": {"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]},
        "VersioningConfiguration": {"Status": "Enabled"}}}


def tls_policy(name, statements=None):
    return {"Type": "AWS::S3::BucketPolicy", "Properties": {"Bucket": ref(name), "PolicyDocument": {"Version": "2012-10-17", "Statement": [
        {"Effect": "Deny", "Principal": "*", "Action": "s3:*", "Resource": [attr(name), sub("${"+name+".Arn}/*")], "Condition": {"Bool": {"aws:SecureTransport": "false"}}}, *(statements or [])]}}}


def artifacts_template():
    return {"AWSTemplateFormatVersion": "2010-09-09", "Description": "Private retained serverless release artifacts only", "Resources": apply_resource_tags({"Releases": bucket(), "ReleaseTLS": tls_policy("Releases")}), "Outputs": {"Bucket": {"Value": ref("Releases")}}}


def template(*, foundation_deployment=None, foundation_producer=None, journey=None):
    resources = {"Web": bucket(), "Exports": bucket(), "ExportTLS": tls_policy("Exports"),
        "State": {"Type": "AWS::DynamoDB::Table", "DeletionPolicy": "Retain", "UpdateReplacePolicy": "Retain", "Properties": {
            "BillingMode": "PAY_PER_REQUEST", "AttributeDefinitions": [{"AttributeName": k, "AttributeType": "S"} for k in ("pk", "sk")],
            "KeySchema": [{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
            "SSESpecification": {"SSEEnabled": True}, "PointInTimeRecoverySpecification": {"PointInTimeRecoveryEnabled": True},
            "DeletionProtectionEnabled": True, "StreamSpecification": {"StreamViewType": "NEW_IMAGE"}}},
        "DeadLetters": {"Type": "AWS::SQS::Queue", "DeletionPolicy": "Retain", "Properties": {"SqsManagedSseEnabled": True, "MessageRetentionPeriod": 1209600}},
        "DispatchFailures": {"Type": "AWS::SQS::Queue", "DeletionPolicy": "Retain", "Properties": {"SqsManagedSseEnabled": True, "MessageRetentionPeriod": 1209600}},
        "Jobs": {"Type": "AWS::SQS::Queue", "Properties": {"SqsManagedSseEnabled": True, "VisibilityTimeout": 360, "MessageRetentionPeriod": 86400, "RedrivePolicy": {"deadLetterTargetArn": attr("DeadLetters"), "maxReceiveCount": 5}}},
        "Api": {"Type": "AWS::ApiGatewayV2::Api", "Properties": {"Name": "governed-agent-builder-serverless", "ProtocolType": "HTTP", "DisableExecuteApiEndpoint": False}},
        "OAC": {"Type": "AWS::CloudFront::OriginAccessControl", "Properties": {"OriginAccessControlConfig": {"Name": sub("${AWS::StackName}-s3-${AWS::Region}"), "OriginAccessControlOriginType": "s3", "SigningBehavior": "always", "SigningProtocol": "sigv4"}}},
        "SPA": {"Type": "AWS::CloudFront::Function", "Properties": {"Name": sub("${AWS::StackName}-spa-${AWS::Region}"), "AutoPublish": True, "FunctionConfig": {"Comment": "Static UI routes only", "Runtime": "cloudfront-js-2.0"}, "FunctionCode": "function handler(event) { var r=event.request; if (r.uri.indexOf('.')===-1) r.uri='/index.html'; return r; }"}},
        "Headers": {"Type": "AWS::CloudFront::ResponseHeadersPolicy", "Properties": {"ResponseHeadersPolicyConfig": {"Name": sub("${AWS::StackName}-headers-${AWS::Region}"), "SecurityHeadersConfig": {
            "ContentTypeOptions": {"Override": True}, "FrameOptions": {"FrameOption": "DENY", "Override": True},
            "ReferrerPolicy": {"ReferrerPolicy": "no-referrer", "Override": True},
            "StrictTransportSecurity": {"AccessControlMaxAgeSec": 31536000, "IncludeSubdomains": True, "Override": True},
            "ContentSecurityPolicy": {"ContentSecurityPolicy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'", "Override": True}}}}},
    }
    resources["Exports"]["Properties"]["LifecycleConfiguration"] = {"Rules": [{"Id": "ExpireEvidence", "Status": "Enabled", "ExpirationInDays": 7, "NoncurrentVersionExpiration": {"NoncurrentDays": 7}}]}
    for name in ("Jobs", "DeadLetters", "DispatchFailures"):
        resources[name+"TLS"] = {"Type": "AWS::SQS::QueuePolicy", "Properties": {"Queues": [ref(name)], "PolicyDocument": {"Version": "2012-10-17", "Statement": [{"Effect": "Deny", "Principal": "*", "Action": "sqs:*", "Resource": attr(name), "Condition": {"Bool": {"aws:SecureTransport": "false"}}}]}}}
    api_behavior = {"TargetOriginId": "managed-api", "ViewerProtocolPolicy": "https-only", "AllowedMethods": ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"], "CachedMethods": ["GET", "HEAD"], "CachePolicyId": "4135ea2d-6df8-44a3-9df3-4b5a84be39ad", "OriginRequestPolicyId": "b689b0a8-53d0-40ab-baf2-68738e2966ac", "ResponseHeadersPolicyId": ref("Headers"), "Compress": True}
    resources["Distribution"] = {"Type": "AWS::CloudFront::Distribution", "Properties": {"DistributionConfig": {
        "Enabled": True, "Comment": "Governed Agent Builder managed serverless demo", "DefaultRootObject": "index.html", "HttpVersion": "http2and3", "PriceClass": "PriceClass_100", "ViewerCertificate": {"CloudFrontDefaultCertificate": True},
        "Origins": [{"Id": "private-web", "DomainName": attr("Web", "RegionalDomainName"), "OriginAccessControlId": ref("OAC"), "S3OriginConfig": {"OriginAccessIdentity": ""}},
                    {"Id": "managed-api", "DomainName": sub("${Api}.execute-api.${AWS::Region}.amazonaws.com"), "CustomOriginConfig": {"OriginProtocolPolicy": "https-only", "OriginSSLProtocols": ["TLSv1.2"]}}],
        "DefaultCacheBehavior": {"TargetOriginId": "private-web", "ViewerProtocolPolicy": "redirect-to-https", "AllowedMethods": ["GET", "HEAD", "OPTIONS"], "CachedMethods": ["GET", "HEAD"], "CachePolicyId": "658327ea-f89d-4fab-a63d-7e88639e58f6", "ResponseHeadersPolicyId": ref("Headers"), "Compress": True, "FunctionAssociations": [{"EventType": "viewer-request", "FunctionARN": attr("SPA", "FunctionARN")}]},
        "CacheBehaviors": [{"PathPattern": p, **copy.deepcopy(api_behavior)} for p in ("api", "api/*", "auth/*", "studio-config.json")],
    }}}
    resources["WebPolicy"] = tls_policy("Web", [{"Effect": "Allow", "Principal": {"Service": "cloudfront.amazonaws.com"}, "Action": "s3:GetObject", "Resource": sub("${Web.Arn}/*"), "Condition": {"StringEquals": {"AWS:SourceArn": sub("arn:${AWS::Partition}:cloudfront::${AWS::AccountId}:distribution/${Distribution}")}}}])
    identity = identity_template("https://replace.example.test")["Resources"]
    def replace(value):
        if isinstance(value, dict): return {k: replace(v) for k,v in value.items()}
        if isinstance(value, list): return [replace(v) for v in value]
        if isinstance(value, str):
            if value.startswith("https://replace.example.test"): return sub(value.replace("https://replace.example.test", "https://${Distribution.DomainName}"))
            if value == "governed-agent-builder-studio": return "governed-agent-builder-serverless-studio"
        return value
    resources.update(replace(identity))
    resources["Verification"] = {"Type": "AWS::DynamoDB::Table", "Properties": {
        "BillingMode": "PAY_PER_REQUEST", "AttributeDefinitions": [{"AttributeName": "id", "AttributeType": "S"}],
        "KeySchema": [{"AttributeName": "id", "KeyType": "HASH"}], "SSESpecification": {"SSEEnabled": True},
        "TimeToLiveSpecification": {"AttributeName": "expires", "Enabled": True}}}
    resources["Client"]["Properties"]["AllowedOAuthScopes"].append("aws.cognito.signin.user.admin")
    resources["Domain"]["Properties"]["Domain"] = {"Fn::Join": ["-", ["gab-serverless", {"Fn::Select": [2, {"Fn::Split": ["/", ref("AWS::StackId")]}]}]]}
    env = {"HOSTED_PREVIEW": "1", "EXECUTION_MODE": "local", "PUBLIC_URL": sub("https://${Distribution.DomainName}"), "STATE_TABLE": ref("State"), "EXPORT_BUCKET": ref("Exports"), "COGNITO_REGION": ref("AWS::Region"), "COGNITO_USER_POOL_ID": ref("Pool"), "COGNITO_CLIENT_ID": ref("Client"), "COGNITO_DOMAIN": sub("https://${Domain}.auth.${AWS::Region}.amazoncognito.com"), "JOB_QUEUE_URL": ref("Jobs")}
    read = {"Effect": "Allow", "Action": ["dynamodb:GetItem", "dynamodb:Query", "dynamodb:ConditionCheckItem"], "Resource": attr("State")}
    write = {"Effect": "Allow", "Action": ["dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem"], "Resource": attr("State")}
    for name, handler, timeout in [("Business", "api_handler", 29), ("Auth", "auth_handler", 29), ("Authorizer", "authorizer", 15), ("Worker", "worker_handler", 60), ("Dispatcher", "dispatch_handler", 30)]:
        logs = name+"Logs"
        resources[logs] = {"Type": "AWS::Logs::LogGroup", "DeletionPolicy": "Retain", "Properties": {"LogGroupName": sub("/governed-agent-builder-serverless/"+name.lower()), "RetentionInDays": 14}}
        statements = [{"Effect": "Allow", "Action": ["logs:CreateLogStream", "logs:PutLogEvents"], "Resource": attr(logs)}]
        if name != "Dispatcher":
            entity_keys = {
                "Business": ["_revision", "components", "foundations", "catalog_history", "grants", "agents", "versions", "jobs", "events", "requests", "audit", "settings", "hosted_sessions", "principals", "job_authority", "general_requests"],
                "Auth": ["_revision", "grants", "oidc_flows", "hosted_sessions", "principals"],
                "Authorizer": ["_revision", "grants", "hosted_sessions", "principals"],
                "Worker": ["_revision", "components", "foundations", "grants", "agents", "versions", "jobs", "events", "settings", "audit", "hosted_sessions", "principals", "job_authority"],
            }[name]
            for permission in (read, write):
                scoped = copy.deepcopy(permission)
                scoped["Condition"] = {"ForAllValues:StringEquals": {"dynamodb:LeadingKeys": entity_keys}}
                statements.append(scoped)
        if name == "Auth": statements.append({"Effect": "Allow", "Action": ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem"], "Resource": attr("Verification")})
        if name == "Business": statements.append({"Effect": "Allow", "Action": ["s3:PutObject"], "Resource": sub("${Exports.Arn}/exports/*")})
        if name == "Worker" and foundation_deployment is not None:
            statements.extend(foundation_worker_statements(**foundation_deployment))
        if name == "Worker" and foundation_producer is not None:
            statements.extend(foundation_producer_statements(**foundation_producer))
        if name == "Worker": statements.append({"Effect": "Allow", "Action": ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes", "sqs:SendMessage"], "Resource": attr("Jobs")})
        if name == "Dispatcher": statements.extend([
            {"Effect": "Allow", "Action": ["dynamodb:DescribeStream", "dynamodb:GetRecords", "dynamodb:GetShardIterator"], "Resource": attr("State", "StreamArn")},
            {"Effect": "Allow", "Action": "dynamodb:ListStreams", "Resource": attr("State", "StreamArn")},
            {"Effect": "Allow", "Action": "sqs:SendMessage", "Resource": [attr("Jobs"), attr("DispatchFailures")]}])
        resources[name+"Role"] = {"Type": "AWS::IAM::Role", "Properties": {"AssumeRolePolicyDocument": {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]}, "Policies": [{"PolicyName": "ScopedRuntime", "PolicyDocument": {"Version": "2012-10-17", "Statement": statements}}]}}
        resources[name] = {"Type": "AWS::Lambda::Function", "Properties": {"Runtime": "python3.13", "Architectures": ["arm64"], "Handler": "backend.serverless."+handler, "Role": attr(name+"Role"), "MemorySize": 512, "Timeout": timeout, "Code": {"S3Bucket": ref("ArtifactBucket"), "S3Key": ref("ArtifactKey")}, "Environment": {"Variables": {**env, **({"VERIFICATION_TABLE": ref("Verification")} if name == "Auth" else {})} if name != "Dispatcher" else {"JOB_QUEUE_URL": ref("Jobs")}}, "LoggingConfig": {"LogGroup": ref(logs)}}}
    # Dedicated workload integration, deliberately outside /api and CloudFront.
    # Uses the existing authority table; no session auth or model permission.
    exchange_keys = ["_revision", "components", "foundations", "grants", "agents", "versions",
                     "settings", "hosted_sessions", "principals", "job_authority"]
    exchange_read = copy.deepcopy(read)
    exchange_read["Condition"] = {"ForAllValues:StringEquals": {"dynamodb:LeadingKeys": exchange_keys}}
    exchange_write = copy.deepcopy(write)
    exchange_write["Action"] = ["dynamodb:PutItem", "dynamodb:UpdateItem"]
    exchange_write["Condition"] = {"ForAllValues:StringEquals": {"dynamodb:LeadingKeys": ["_revision", "settings"]}}
    resources["FoundationExchangeLogs"] = {"Type": "AWS::Logs::LogGroup", "DeletionPolicy": "Retain",
        "Properties": {"LogGroupName": "/governed-agent-builder-serverless/foundation-exchange", "RetentionInDays": 14}}
    resources["FoundationExchangeRole"] = {"Type": "AWS::IAM::Role", "Properties": {
        "AssumeRolePolicyDocument": {"Version": "2012-10-17", "Statement": [{"Effect": "Allow",
            "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]},
        "Policies": [{"PolicyName": "FoundationAdmissionOnly", "PolicyDocument": {"Version": "2012-10-17",
            "Statement": [exchange_read, exchange_write, {"Effect": "Allow",
                "Action": ["logs:CreateLogStream", "logs:PutLogEvents"], "Resource": attr("FoundationExchangeLogs")} ]}}]}}
    resources["FoundationExchange"] = {"Type": "AWS::Lambda::Function", "Properties": {
        "Runtime": "python3.13", "Architectures": ["arm64"], "Handler": "backend.serverless.foundation_exchange_handler",
        "Role": attr("FoundationExchangeRole"), "MemorySize": 512, "Timeout": 15,
        "Code": {"S3Bucket": ref("ArtifactBucket"), "S3Key": ref("ArtifactKey")},
        "Environment": {"Variables": {"STATE_TABLE": ref("State"), "FOUNDATION_API_ID": ref("Api"),
                                      "FOUNDATION_ADMISSION_ENABLED": "1"}},
        "LoggingConfig": {"LogGroup": ref("FoundationExchangeLogs")}}}
    resources["FoundationExchangeIntegration"] = {"Type": "AWS::ApiGatewayV2::Integration", "Properties": {
        "ApiId": ref("Api"), "IntegrationType": "AWS_PROXY", "IntegrationMethod": "POST",
        "IntegrationUri": attr("FoundationExchange"), "PayloadFormatVersion": "2.0", "TimeoutInMillis": 15000}}
    resources["FoundationExchangeRoute"] = {"Type": "AWS::ApiGatewayV2::Route", "Properties": {
        "ApiId": ref("Api"), "RouteKey": "POST /internal/foundation/exchange", "AuthorizationType": "AWS_IAM",
        "Target": {"Fn::Join": ["/", ["integrations", ref("FoundationExchangeIntegration")]]}}}
    resources["FoundationExchangePermission"] = {"Type": "AWS::Lambda::Permission", "Properties": {
        "FunctionName": ref("FoundationExchange"), "Action": "lambda:InvokeFunction",
        "Principal": "apigateway.amazonaws.com", "SourceAccount": ref("AWS::AccountId"),
        "SourceArn": sub("arn:${AWS::Partition}:execute-api:${AWS::Region}:${AWS::AccountId}:${Api}/$default/POST/internal/foundation/exchange")}}
    resources["StreamMapping"] = {"Type": "AWS::Lambda::EventSourceMapping", "Properties": {"EventSourceArn": attr("State", "StreamArn"), "FunctionName": ref("Dispatcher"), "StartingPosition": "TRIM_HORIZON", "BatchSize": 10, "MaximumBatchingWindowInSeconds": 1, "BisectBatchOnFunctionError": True, "FunctionResponseTypes": ["ReportBatchItemFailures"], "MaximumRetryAttempts": 10, "MaximumRecordAgeInSeconds": 86400, "DestinationConfig": {"OnFailure": {"Destination": attr("DispatchFailures")}}, "FilterCriteria": {"Filters": [{"Pattern": '{"eventName":["INSERT"],"dynamodb":{"NewImage":{"pk":{"S":["jobs"]}}}}'}]}}}
    # Opt-in source configuration only; neither live nor producer is enabled by
    # default. Runtime IAM remains independently scoped by foundation_deployment.
    if foundation_producer is not None:
        resources["Worker"]["Properties"]["Environment"]["Variables"]["FOUNDATION_PRODUCER_ENABLED"] = "1"
        resources["Worker"]["Properties"]["MemorySize"] = 1024
    resources["WorkerMapping"] = {"Type": "AWS::Lambda::EventSourceMapping", "Properties": {"EventSourceArn": attr("Jobs"), "FunctionName": ref("Worker"), "BatchSize": 1, "FunctionResponseTypes": ["ReportBatchItemFailures"], "ScalingConfig": {"MaximumConcurrency": 2}}}
    resources["SessionAuthorizer"] = {"Type": "AWS::ApiGatewayV2::Authorizer", "Properties": {"ApiId": ref("Api"), "Name": "server-session", "AuthorizerType": "REQUEST", "AuthorizerPayloadFormatVersion": "2.0", "EnableSimpleResponses": True, "AuthorizerResultTtlInSeconds": 0, "IdentitySource": ["$request.header.Cookie"], "AuthorizerUri": sub("arn:${AWS::Partition}:apigateway:${AWS::Region}:lambda:path/2015-03-31/functions/${Authorizer.Arn}/invocations")}}
    for name in ("Business", "Auth"):
        resources[name+"Integration"] = {"Type": "AWS::ApiGatewayV2::Integration", "Properties": {"ApiId": ref("Api"), "IntegrationType": "AWS_PROXY", "IntegrationMethod": "POST", "IntegrationUri": attr(name), "PayloadFormatVersion": "2.0", "TimeoutInMillis": 29000}}
    # An exact logout route takes precedence over the protected API wildcard.
    # Its Auth handler enforces same-origin revocation without a valid session.
    for index, route in enumerate(("ANY /api", "ANY /api/{proxy+}", "GET /auth/login", "GET /auth/callback", "GET /studio-config.json", "GET /auth/verification/status", "POST /auth/verification/send", "POST /auth/verification/verify", "POST /api/auth/logout")):
        protected = index < 2
        resources["Route"+str(index)] = {"Type": "AWS::ApiGatewayV2::Route", "Properties": {"ApiId": ref("Api"), "RouteKey": route, "AuthorizationType": "CUSTOM" if protected else "NONE", "Target": {"Fn::Join": ["/", ["integrations", ref("BusinessIntegration" if protected else "AuthIntegration")]]}, **({"AuthorizerId": ref("SessionAuthorizer")} if protected else {})}}
    for name in ("Business", "Auth", "Authorizer"):
        paths = ["authorizers/*"] if name == "Authorizer" else ["*/*/api", "*/*/api/*"] if name == "Business" else ["*/GET/auth/login", "*/GET/auth/callback", "*/GET/studio-config.json", "*/GET/auth/verification/status", "*/POST/auth/verification/send", "*/POST/auth/verification/verify", "*/POST/api/auth/logout"]
        for index, path in enumerate(paths):
            resources[name+"Permission"+str(index)] = {"Type": "AWS::Lambda::Permission", "Properties": {"FunctionName": ref(name), "Action": "lambda:InvokeFunction", "Principal": "apigateway.amazonaws.com", "SourceAccount": ref("AWS::AccountId"), "SourceArn": sub("arn:${AWS::Partition}:execute-api:${AWS::Region}:${AWS::AccountId}:${Api}/"+path)}}
    resources["ApiLogs"] = {"Type": "AWS::Logs::LogGroup", "DeletionPolicy": "Retain", "Properties": {"LogGroupName": "/governed-agent-builder-serverless/http-api", "RetentionInDays": 14}}
    resources["Stage"] = {"Type": "AWS::ApiGatewayV2::Stage", "Properties": {"ApiId": ref("Api"), "StageName": "$default", "AutoDeploy": True, "DefaultRouteSettings": {"ThrottlingBurstLimit": 10, "ThrottlingRateLimit": 5}, "AccessLogSettings": {"DestinationArn": attr("ApiLogs"), "Format": '{"requestId":"$context.requestId","route":"$context.routeKey","status":"$context.status"}'}}}
    for name in ("DeadLetters", "DispatchFailures"):
        resources[name+"Alarm"] = {"Type": "AWS::CloudWatch::Alarm", "Properties": {"AlarmDescription": "Operator recovery required; do not discard queued evidence", "Namespace": "AWS/SQS", "MetricName": "ApproximateNumberOfMessagesVisible", "Dimensions": [{"Name": "QueueName", "Value": attr(name, "QueueName")}], "Statistic": "Maximum", "Period": 60, "EvaluationPeriods": 1, "Threshold": 1, "ComparisonOperator": "GreaterThanOrEqualToThreshold", "TreatMissingData": "notBreaching"}}
    if journey is not None:
        from infra.journey import configure_app
        configure_app(resources, journey)
    apply_resource_tags(resources)
    return {"AWSTemplateFormatVersion": "2010-09-09", "Description": "Isolated private S3 OAC + managed HTTPS API Cognito Lambda DynamoDB SQS; no VPC dependencies", "Parameters": {"ArtifactBucket": {"Type": "String"}, "ArtifactKey": {"Type": "String"}}, "Resources": resources, "Outputs": {"ApplicationOrigin": {"Value": sub("https://${Distribution.DomainName}")}, "ApiEndpoint": {"Value": attr("Api", "ApiEndpoint")}, "DistributionId": {"Value": ref("Distribution")}, "FrontendBucket": {"Value": ref("Web")}, "StateTable": {"Value": ref("State")}, "UserPoolId": {"Value": ref("Pool")}, "ClientId": {"Value": ref("Client")}, "CognitoDomain": {"Value": sub("https://${Domain}.auth.${AWS::Region}.amazoncognito.com")}, "WorkerFunction": {"Value": ref("Worker")}}}


def foundation_worker_statements(*, role, artifact, runtime_name, subnets, groups):
    """Explicit per-package allowlist. No IAM mutation, update, delete or discovery.

    CreateAgentRuntime has no resource-level IAM support (AWS SAR); constrain its
    request using mandatory VPC keys plus an exact PassRole. Never attach by default.
    """
    import re
    match = re.fullmatch(r'arn:aws:iam::(\d{12}):role/([A-Za-z0-9_+=,.@-]+)', role)
    if (not match or not re.fullmatch(r'gab_foundation_[a-f0-9]{24}', runtime_name)
            or not re.fullmatch(r'arn:aws:s3:::[a-z0-9.-]+/approved/[A-Za-z0-9/_.-]+\.zip', artifact)
            or not subnets or not groups
            or any(not re.fullmatch(r'subnet-[a-z0-9]+', x) for x in subnets)
            or any(not re.fullmatch(r'sg-[a-z0-9]+', x) for x in groups)):
        raise ValueError('EXACT_PACKAGE_IAM_BINDINGS_REQUIRED')
    scope = f'arn:aws:bedrock-agentcore:us-west-2:{match[1]}:runtime/{runtime_name}-*'
    return [
        {"Effect": "Allow", "Action": ["bedrock-agentcore:CreateAgentRuntime"], "Resource": "*",
         "Condition": {"ForAllValues:StringEquals": {"bedrock-agentcore:subnets": subnets,
                         "bedrock-agentcore:securityGroups": groups},
                       "Null": {"bedrock-agentcore:subnets": "false", "bedrock-agentcore:securityGroups": "false"}}},
        {"Effect": "Allow", "Action": ["bedrock-agentcore:CreateAgentRuntimeEndpoint",
            "bedrock-agentcore:GetAgentRuntime", "bedrock-agentcore:InvokeAgentRuntime"],
         "Resource": [scope, scope + '/runtime-endpoint/DEFAULT']},
        {"Effect": "Allow", "Action": ["iam:PassRole"], "Resource": role,
         "Condition": {"StringEquals": {"iam:PassedToService": "bedrock-agentcore.amazonaws.com"}}},
        {"Effect": "Allow", "Action": ["s3:GetObjectVersion"], "Resource": artifact}]


def foundation_producer_statements(*, bucket, base_key, base_version, producer_role):
    """Operator-only explicit storage/role binding, never business request fields.

    The deployed WorkerRole ARN must equal producer_role in protected settings.
    IAM permits only the approved base version and content-addressed releases.
    """
    import re
    if (not re.fullmatch(r'[a-z0-9][a-z0-9.-]{2,62}', bucket)
            or not re.fullmatch(r'approved/[A-Za-z0-9/_-]+\.zip', base_key)
            or not base_version or base_version == 'null'
            or not re.fullmatch(r'arn:aws:iam::[0-9]{12}:role/[A-Za-z0-9_+=,.@-]+', producer_role)):
        raise ValueError('EXACT_PRODUCER_STORAGE_ROLE_REQUIRED')
    arn = 'arn:aws:s3:::' + bucket
    return [
        {'Effect': 'Allow', 'Action': ['s3:GetBucketVersioning', 's3:GetBucketPublicAccessBlock'], 'Resource': arn},
        {'Effect': 'Allow', 'Action': ['s3:GetObjectVersion'], 'Resource': arn + '/' + base_key,
         'Condition': {'StringEquals': {'s3:VersionId': base_version}}},
        {'Effect': 'Allow', 'Action': ['s3:GetObject', 's3:GetObjectVersion'], 'Resource': arn + '/releases/*/foundation.zip'},
        {'Effect': 'Allow', 'Action': ['s3:PutObject'], 'Resource': arn + '/releases/*/foundation.zip',
         'Condition': {'StringEquals': {'s3:x-amz-server-side-encryption': 'AES256'},
                       'Null': {'s3:if-none-match': 'false'}}}]
