"""Scoped package-transfer and Gateway permissions for the existing Studio."""
import re


NAMES = ("McpPackageUploadPolicy", "McpPackageReadPolicy", "McpPackageGatewayPolicy", "McpPackageDeletionPolicy")


def policies(settings):
    value = settings.get("mcp_package_upload")
    account, region = settings["account"], settings["region"]
    if (not isinstance(value, dict) or set(value) != {"bucket", "runtime_prefix", "gateway_role_arn"}
            or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{2,62}", value["bucket"])
            or not re.fullmatch(r"[a-z][a-z0-9_]{2,19}", value["runtime_prefix"])
            or not re.fullmatch(re.escape(f"arn:aws:iam::{account}:role/") + r"[A-Za-z0-9+=,.@_-]+", value["gateway_role_arn"])):
        raise ValueError("Invalid MCP package deployment binding")
    bucket = "arn:aws:s3:::" + value["bucket"]
    prefix = "mcp/python/uploads/*/part-*"
    read = [{"Effect": "Allow", "Action": ["s3:GetObject", "s3:GetObjectVersion"], "Resource": bucket + "/" + prefix},
            {"Effect": "Allow", "Action": ["s3:ListBucket"], "Resource": bucket,
             "Condition": {"StringLike": {"s3:prefix": prefix}}}]
    write = [
        {"Effect": "Allow", "Action": ["s3:PutObject"], "Resource": bucket + "/" + prefix, "Condition": {
            "StringEquals": {"s3:x-amz-server-side-encryption": "AES256", "s3:RequestObjectTag/auto-delete": "no"},
            "Null": {"s3:if-none-match": "false"}}},
        {"Effect": "Allow", "Action": ["s3:PutObjectTagging"], "Resource": bucket + "/" + prefix,
         "Condition": {"StringEquals": {"s3:RequestObjectTag/auto-delete": "no"}}},
    ]
    runtime = f"arn:aws:bedrock-agentcore:{region}:{account}:runtime/{value['runtime_prefix']}_*"
    def policy(description, roles, statements):
        return {"Type": "AWS::IAM::ManagedPolicy", "Properties": {"Description": description, "Roles": roles,
            "PolicyDocument": {"Version": "2012-10-17", "Statement": statements}}}
    return {
        NAMES[0]: policy("Immutable complete MCP package upload parts", [{"Ref": "BusinessRole"}], [*read, *write]),
        NAMES[1]: policy("Read frozen complete MCP package parts for deployment", [{"Ref": "WorkerRole"}], read),
        NAMES[2]: policy("Invoke Studio-deployed MCP packages from the bound Gateway",
            [value["gateway_role_arn"].split(":role/")[1]], [{
                "Effect": "Allow", "Action": ["bedrock-agentcore:InvokeAgentRuntime"],
                "Resource": [runtime, runtime + "/runtime-endpoint/DEFAULT"],
                "Condition": {"StringEquals": {"aws:ResourceTag/auto-delete": "no",
                    "aws:ResourceTag/project": "governed-agent-builder"}}}]),
        NAMES[3]: policy("Retire receipt-bound Studio MCP deployments", [{"Ref": "WorkerRole"}], [
            {"Effect": "Allow", "Action": ["bedrock-agentcore:DeleteAgentRuntime",
                                           "bedrock-agentcore:DeleteAgentRuntimeEndpoint"],
             "Resource": [runtime, runtime + "/runtime-endpoint/DEFAULT"],
             "Condition": {"StringEquals": {"aws:ResourceTag/auto-delete": "no",
                 "aws:ResourceTag/project": "governed-agent-builder"}}},
            {"Effect": "Allow", "Action": ["bedrock-agentcore:GetWorkloadIdentity", "bedrock-agentcore:DeleteWorkloadIdentity"],
             "Resource": f"arn:aws:bedrock-agentcore:{region}:{account}:workload-identity-directory/default/workload-identity/{value['runtime_prefix']}_*"},
            {"Effect": "Allow", "Action": ["s3:GetObjectVersion", "s3:GetObjectVersionTagging", "s3:DeleteObjectVersion"],
             "Resource": [bucket + "/" + prefix, bucket + "/mcp/python/*/runtime.zip"]},
            {"Effect": "Allow", "Action": ["s3:ListBucketVersions"], "Resource": bucket,
             "Condition": {"StringLike": {"s3:prefix": [prefix, "mcp/python/*/runtime.zip"]}}},
        ]),
    }
