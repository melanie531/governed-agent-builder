"""Isolated managed-services CloudFormation. No VPC or policy exceptions."""
import copy
from infra.identity import template as identity_template


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
    return {"AWSTemplateFormatVersion": "2010-09-09", "Description": "Private retained serverless release artifacts only", "Resources": {"Releases": bucket(), "ReleaseTLS": tls_policy("Releases")}, "Outputs": {"Bucket": {"Value": ref("Releases")}}}


def template():
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
        "OAC": {"Type": "AWS::CloudFront::OriginAccessControl", "Properties": {"OriginAccessControlConfig": {"Name": sub("${AWS::StackName}-s3"), "OriginAccessControlOriginType": "s3", "SigningBehavior": "always", "SigningProtocol": "sigv4"}}},
        "SPA": {"Type": "AWS::CloudFront::Function", "Properties": {"Name": sub("${AWS::StackName}-spa"), "AutoPublish": True, "FunctionConfig": {"Comment": "Static UI routes only", "Runtime": "cloudfront-js-2.0"}, "FunctionCode": "function handler(event) { var r=event.request; if (r.uri.indexOf('.')===-1) r.uri='/index.html'; return r; }"}},
        "Headers": {"Type": "AWS::CloudFront::ResponseHeadersPolicy", "Properties": {"ResponseHeadersPolicyConfig": {"Name": sub("${AWS::StackName}-headers"), "SecurityHeadersConfig": {
            "ContentTypeOptions": {"Override": True}, "FrameOptions": {"FrameOption": "DENY", "Override": True},
            "ReferrerPolicy": {"ReferrerPolicy": "no-referrer", "Override": True},
            "StrictTransportSecurity": {"AccessControlMaxAgeSec": 31536000, "IncludeSubdomains": True, "Override": True},
            "ContentSecurityPolicy": {"ContentSecurityPolicy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'", "Override": True}}}}},
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
                "Business": ["_revision", "components", "foundations", "catalog_history", "grants", "agents", "versions", "jobs", "events", "requests", "audit", "settings", "hosted_sessions", "principals", "job_authority"],
                "Auth": ["_revision", "grants", "oidc_flows", "hosted_sessions", "principals"],
                "Authorizer": ["_revision", "grants", "hosted_sessions", "principals"],
                "Worker": ["_revision", "components", "foundations", "grants", "agents", "versions", "jobs", "events", "settings", "hosted_sessions", "principals", "job_authority"],
            }[name]
            for permission in (read, write):
                scoped = copy.deepcopy(permission)
                scoped["Condition"] = {"ForAllValues:StringEquals": {"dynamodb:LeadingKeys": entity_keys}}
                statements.append(scoped)
        if name == "Auth": statements.append({"Effect": "Allow", "Action": ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem"], "Resource": attr("Verification")})
        if name == "Business": statements.append({"Effect": "Allow", "Action": ["s3:PutObject"], "Resource": sub("${Exports.Arn}/exports/*")})
        if name == "Worker": statements.append({"Effect": "Allow", "Action": ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"], "Resource": attr("Jobs")})
        if name == "Dispatcher": statements.extend([
            {"Effect": "Allow", "Action": ["dynamodb:DescribeStream", "dynamodb:GetRecords", "dynamodb:GetShardIterator"], "Resource": attr("State", "StreamArn")},
            {"Effect": "Allow", "Action": "dynamodb:ListStreams", "Resource": attr("State", "StreamArn")},
            {"Effect": "Allow", "Action": "sqs:SendMessage", "Resource": [attr("Jobs"), attr("DispatchFailures")]}])
        resources[name+"Role"] = {"Type": "AWS::IAM::Role", "Properties": {"AssumeRolePolicyDocument": {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]}, "Policies": [{"PolicyName": "ScopedRuntime", "PolicyDocument": {"Version": "2012-10-17", "Statement": statements}}]}}
        resources[name] = {"Type": "AWS::Lambda::Function", "Properties": {"Runtime": "python3.13", "Architectures": ["arm64"], "Handler": "backend.serverless."+handler, "Role": attr(name+"Role"), "MemorySize": 512, "Timeout": timeout, "Code": {"S3Bucket": ref("ArtifactBucket"), "S3Key": ref("ArtifactKey")}, "Environment": {"Variables": {**env, **({"VERIFICATION_TABLE": ref("Verification")} if name == "Auth" else {})} if name != "Dispatcher" else {"JOB_QUEUE_URL": ref("Jobs")}}, "LoggingConfig": {"LogGroup": ref(logs)}}}
    resources["StreamMapping"] = {"Type": "AWS::Lambda::EventSourceMapping", "Properties": {"EventSourceArn": attr("State", "StreamArn"), "FunctionName": ref("Dispatcher"), "StartingPosition": "TRIM_HORIZON", "BatchSize": 10, "MaximumBatchingWindowInSeconds": 1, "BisectBatchOnFunctionError": True, "FunctionResponseTypes": ["ReportBatchItemFailures"], "MaximumRetryAttempts": 10, "MaximumRecordAgeInSeconds": 86400, "DestinationConfig": {"OnFailure": {"Destination": attr("DispatchFailures")}}, "FilterCriteria": {"Filters": [{"Pattern": '{"eventName":["INSERT"],"dynamodb":{"NewImage":{"pk":{"S":["jobs"]}}}}'}]}}}
    resources["WorkerMapping"] = {"Type": "AWS::Lambda::EventSourceMapping", "Properties": {"EventSourceArn": attr("Jobs"), "FunctionName": ref("Worker"), "BatchSize": 1, "FunctionResponseTypes": ["ReportBatchItemFailures"], "ScalingConfig": {"MaximumConcurrency": 2}}}
    resources["SessionAuthorizer"] = {"Type": "AWS::ApiGatewayV2::Authorizer", "Properties": {"ApiId": ref("Api"), "Name": "server-session", "AuthorizerType": "REQUEST", "AuthorizerPayloadFormatVersion": "2.0", "EnableSimpleResponses": True, "AuthorizerResultTtlInSeconds": 0, "IdentitySource": ["$request.header.Cookie"], "AuthorizerUri": sub("arn:${AWS::Partition}:apigateway:${AWS::Region}:lambda:path/2015-03-31/functions/${Authorizer.Arn}/invocations")}}
    for name in ("Business", "Auth"):
        resources[name+"Integration"] = {"Type": "AWS::ApiGatewayV2::Integration", "Properties": {"ApiId": ref("Api"), "IntegrationType": "AWS_PROXY", "IntegrationMethod": "POST", "IntegrationUri": attr(name), "PayloadFormatVersion": "2.0", "TimeoutInMillis": 29000}}
    for index, route in enumerate(("ANY /api", "ANY /api/{proxy+}", "GET /auth/login", "GET /auth/callback", "GET /studio-config.json", "GET /auth/verification/status", "POST /auth/verification/send", "POST /auth/verification/verify")):
        protected = index < 2
        resources["Route"+str(index)] = {"Type": "AWS::ApiGatewayV2::Route", "Properties": {"ApiId": ref("Api"), "RouteKey": route, "AuthorizationType": "CUSTOM" if protected else "NONE", "Target": {"Fn::Join": ["/", ["integrations", ref("BusinessIntegration" if protected else "AuthIntegration")]]}, **({"AuthorizerId": ref("SessionAuthorizer")} if protected else {})}}
    for name in ("Business", "Auth", "Authorizer"):
        paths = ["authorizers/*"] if name == "Authorizer" else ["*/*/api", "*/*/api/*"] if name == "Business" else ["*/GET/auth/login", "*/GET/auth/callback", "*/GET/studio-config.json", "*/GET/auth/verification/status", "*/POST/auth/verification/send", "*/POST/auth/verification/verify"]
        for index, path in enumerate(paths):
            resources[name+"Permission"+str(index)] = {"Type": "AWS::Lambda::Permission", "Properties": {"FunctionName": ref(name), "Action": "lambda:InvokeFunction", "Principal": "apigateway.amazonaws.com", "SourceAccount": ref("AWS::AccountId"), "SourceArn": sub("arn:${AWS::Partition}:execute-api:${AWS::Region}:${AWS::AccountId}:${Api}/"+path)}}
    resources["ApiLogs"] = {"Type": "AWS::Logs::LogGroup", "DeletionPolicy": "Retain", "Properties": {"LogGroupName": "/governed-agent-builder-serverless/http-api", "RetentionInDays": 14}}
    resources["Stage"] = {"Type": "AWS::ApiGatewayV2::Stage", "Properties": {"ApiId": ref("Api"), "StageName": "$default", "AutoDeploy": True, "DefaultRouteSettings": {"ThrottlingBurstLimit": 10, "ThrottlingRateLimit": 5}, "AccessLogSettings": {"DestinationArn": attr("ApiLogs"), "Format": '{"requestId":"$context.requestId","route":"$context.routeKey","status":"$context.status"}'}}}
    for name in ("DeadLetters", "DispatchFailures"):
        resources[name+"Alarm"] = {"Type": "AWS::CloudWatch::Alarm", "Properties": {"AlarmDescription": "Operator recovery required; do not discard queued evidence", "Namespace": "AWS/SQS", "MetricName": "ApproximateNumberOfMessagesVisible", "Dimensions": [{"Name": "QueueName", "Value": attr(name, "QueueName")}], "Statistic": "Maximum", "Period": 60, "EvaluationPeriods": 1, "Threshold": 1, "ComparisonOperator": "GreaterThanOrEqualToThreshold", "TreatMissingData": "notBreaching"}}
    return {"AWSTemplateFormatVersion": "2010-09-09", "Description": "Isolated private S3 OAC + managed HTTPS API Cognito Lambda DynamoDB SQS; no VPC dependencies", "Parameters": {"ArtifactBucket": {"Type": "String"}, "ArtifactKey": {"Type": "String"}}, "Resources": resources, "Outputs": {"ApplicationOrigin": {"Value": sub("https://${Distribution.DomainName}")}, "ApiEndpoint": {"Value": attr("Api", "ApiEndpoint")}, "DistributionId": {"Value": ref("Distribution")}, "FrontendBucket": {"Value": ref("Web")}, "StateTable": {"Value": ref("State")}, "UserPoolId": {"Value": ref("Pool")}, "ClientId": {"Value": ref("Client")}, "CognitoDomain": {"Value": sub("https://${Domain}.auth.${AWS::Region}.amazoncognito.com")}, "WorkerFunction": {"Value": ref("Worker")}}}
