"""Deploy Path A: governed specialist MCP server -> AgentCore Runtime (MCP) -> AgentCore Gateway mcpServer target.

Idempotent create-or-update, account/region pinned. Outbound Gateway->Runtime auth is the Gateway
service role via SigV4 (GATEWAY_IAM_ROLE); inbound Gateway auth is AWS_IAM. No secrets are created.
Tool bodies stay LOCAL SIMULATION (synthetic fixtures) inside the container.

Run from repo root: python scripts/deploy_mcp_specialist.py --state <state.json>
"""
import argparse
import json
import subprocess
import time
import urllib.parse
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

ACCOUNT, REGION = "534409838809", "us-west-2"
REPO = "governed-agent/mcp-specialist"
RUNTIME_ROLE = "governed-mcp-specialist-runtime"
GATEWAY_NAME = "governed-mcp-specialist-gw"
GATEWAY_ROLE = "governed-mcp-specialist-gateway"
# (runtime name, server-side caller scope profile, gateway target name); same image for both.
DEPLOYMENTS = [("governed_mcp_specialist", "full", "risk-analyst-specialist"),
               ("governed_mcp_specialist_nodata", "no-data", "risk-analyst-specialist-nodata")]


def trust():
    # AgentCore-documented pattern; the service validates the role before a runtime/gateway id exists.
    source_arn = f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT}:*"
    return json.dumps({"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
        "Action": "sts:AssumeRole", "Condition": {"StringEquals": {"aws:SourceAccount": ACCOUNT}, "ArnLike": {"aws:SourceArn": source_arn}}}]})


def runtime_policy():
    logs = f"arn:aws:logs:{REGION}:{ACCOUNT}:log-group:/aws/bedrock-agentcore/runtimes/*"
    return json.dumps({"Version": "2012-10-17", "Statement": [
        {"Sid": "EcrPullThisRepo", "Effect": "Allow", "Action": ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
         "Resource": f"arn:aws:ecr:{REGION}:{ACCOUNT}:repository/{REPO}"},
        {"Sid": "EcrToken", "Effect": "Allow", "Action": "ecr:GetAuthorizationToken", "Resource": "*"},
        {"Sid": "Logs", "Effect": "Allow", "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"],
         "Resource": [logs, logs + ":log-stream:*"]},
        {"Sid": "LogGroupsDescribe", "Effect": "Allow", "Action": "logs:DescribeLogGroups", "Resource": f"arn:aws:logs:{REGION}:{ACCOUNT}:log-group:*"}]})


def gateway_policy(runtime_arns):
    return json.dumps({"Version": "2012-10-17", "Statement": [{"Sid": "InvokeOnlyTheseRuntimes", "Effect": "Allow",
        "Action": "bedrock-agentcore:InvokeAgentRuntime", "Resource": [a + suffix for a in runtime_arns for suffix in ("", "/*")]}]})


def ensure_role(iam, name, trust_doc, policy_doc, description):
    try:
        arn = iam.create_role(RoleName=name, AssumeRolePolicyDocument=trust_doc, Description=description, MaxSessionDuration=3600)["Role"]["Arn"]
        time.sleep(10)  # IAM propagation before AgentCore validates the role
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "EntityAlreadyExists":
            raise
        iam.update_assume_role_policy(RoleName=name, PolicyDocument=trust_doc)
        arn = iam.get_role(RoleName=name)["Role"]["Arn"]
    iam.put_role_policy(RoleName=name, PolicyName=name + "-inline", PolicyDocument=policy_doc)
    return arn


def wait(fetch, ready, failed, label, timeout=900):
    delay, deadline = 5, time.time() + timeout
    while True:
        item = fetch()
        status = item.get("status")
        print(f"  {label}: {status}", flush=True)
        if status in ready:
            return item
        if status in failed or time.time() > deadline:
            raise SystemExit(f"{label} did not become ready: {status} {item.get('failureReason') or item.get('statusReasons')}")
        time.sleep(delay)
        delay = min(delay * 2, 30)


def push_image(ecr, build_time):
    try:
        ecr.create_repository(repositoryName=REPO, imageTagMutability="IMMUTABLE", imageScanningConfiguration={"scanOnPush": True})
    except ecr.exceptions.RepositoryAlreadyExistsException:
        pass
    registry = f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com"
    tag = build_time.replace(":", "").replace("-", "")
    uri = f"{registry}/{REPO}:{tag}"
    token = subprocess.run(["aws", "ecr", "get-login-password", "--region", REGION], check=True, capture_output=True, text=True).stdout
    subprocess.run(["docker", "login", "--username", "AWS", "--password-stdin", registry], input=token, check=True, capture_output=True, text=True)
    subprocess.run(["docker", "buildx", "build", "--platform", "linux/arm64", "--provenance=false", "-f", "runtime/mcp_specialist/Dockerfile",
                    "--build-arg", f"IMAGE_BUILD={build_time}", "-t", uri, "--push", "."], check=True)
    image = ecr.describe_images(repositoryName=REPO, imageIds=[{"imageTag": tag}])["imageDetails"][0]
    return uri, image["imageDigest"], image["imagePushedAt"].isoformat()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    args = ap.parse_args()
    session = boto3.Session(region_name=REGION)
    assert session.client("sts").get_caller_identity()["Account"] == ACCOUNT, "wrong account"
    ecr, iam, ctl = session.client("ecr"), session.client("iam"), session.client("bedrock-agentcore-control")
    build_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state = {"account": ACCOUNT, "region": REGION, "build_time_utc": build_time}

    print("1/5 image", flush=True)
    state["image_uri"], state["image_digest"], state["image_pushed_at"] = push_image(ecr, build_time)
    image_ref = f"{state['image_uri'].rsplit(':', 1)[0]}@{state['image_digest']}"

    print("2/5 runtime role + runtimes", flush=True)
    runtime_role = ensure_role(iam, RUNTIME_ROLE, trust(), runtime_policy(),
                               "Execution role for the governed specialist MCP runtime: ECR pull + logs only")
    state["runtime_role_arn"], state["deployments"] = runtime_role, {}
    existing = {r["agentRuntimeName"]: r["agentRuntimeId"] for r in ctl.list_agent_runtimes(maxResults=100)["agentRuntimes"]}
    for runtime_name, profile, _ in DEPLOYMENTS:
        spec = dict(agentRuntimeArtifact={"containerConfiguration": {"containerUri": image_ref}}, roleArn=runtime_role,
                    networkConfiguration={"networkMode": "PUBLIC"}, protocolConfiguration={"serverProtocol": "MCP"},
                    environmentVariables={"EXECUTION_MODE": "local", "CALLER_SCOPE_PROFILE": profile},
                    description=f"Governed specialist MCP server, caller scope profile {profile} (LOCAL SIMULATION tool bodies), Path A")
        if runtime_name in existing:
            runtime_id = existing[runtime_name]
            ctl.update_agent_runtime(agentRuntimeId=runtime_id, **spec)
        else:
            runtime_id = ctl.create_agent_runtime(agentRuntimeName=runtime_name, **spec)["agentRuntimeId"]
        runtime = wait(lambda: ctl.get_agent_runtime(agentRuntimeId=runtime_id), {"READY"}, {"CREATE_FAILED", "UPDATE_FAILED"}, runtime_name)
        state["deployments"][profile] = dict(runtime_id=runtime_id, runtime_arn=runtime["agentRuntimeArn"], runtime_version=runtime["agentRuntimeVersion"],
                                             runtime_qualifier="DEFAULT", runtime_last_updated_at=runtime["lastUpdatedAt"].isoformat())

    print("3/5 gateway role + gateway", flush=True)
    gateway_role = ensure_role(iam, GATEWAY_ROLE, trust(), gateway_policy([d["runtime_arn"] for d in state["deployments"].values()]),
                               "Gateway service role: SigV4 InvokeAgentRuntime on the governed specialist runtimes only")
    gateways = [g for g in ctl.list_gateways(maxResults=100)["items"] if g["name"] == GATEWAY_NAME]
    gateway_id = gateways[0]["gatewayId"] if gateways else ctl.create_gateway(
        name=GATEWAY_NAME, roleArn=gateway_role, protocolType="MCP", authorizerType="AWS_IAM",
        description="Path A: governed specialist MCP runtime behind AWS_IAM Gateway")["gatewayId"]
    gateway = wait(lambda: ctl.get_gateway(gatewayIdentifier=gateway_id), {"READY"}, {"FAILED"}, "gateway")
    state.update(gateway_id=gateway_id, gateway_url=gateway["gatewayUrl"], gateway_role_arn=gateway_role)

    print("4/5 mcpServer targets", flush=True)
    targets = {t["name"]: t["targetId"] for t in ctl.list_gateway_targets(gatewayIdentifier=gateway_id, maxResults=100)["items"]}
    for _, profile, target_name in DEPLOYMENTS:
        deployment = state["deployments"][profile]
        endpoint = (f"https://bedrock-agentcore.{REGION}.amazonaws.com/runtimes/{urllib.parse.quote(deployment['runtime_arn'], safe='')}"
                    f"/invocations?qualifier=DEFAULT")
        target = dict(gatewayIdentifier=gateway_id, name=target_name, description=f"Governed specialist agent-as-tool, caller scope {profile}",
                      targetConfiguration={"mcp": {"mcpServer": {"endpoint": endpoint}}},
                      credentialProviderConfigurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE",
                          "credentialProvider": {"iamCredentialProvider": {"service": "bedrock-agentcore", "region": REGION}}}])
        if target_name in targets:
            target_id = ctl.update_gateway_target(targetId=targets[target_name], **target)["targetId"]
        else:
            target_id = ctl.create_gateway_target(**target)["targetId"]
        fetch = lambda: ctl.get_gateway_target(gatewayIdentifier=gateway_id, targetId=target_id)
        item = wait(fetch, {"READY", "FAILED"}, set(), target_name)
        if item["status"] == "FAILED":
            # First tool sync can race IAM propagation of the new gateway role policy
            # ("Authorization error when sending message"); one resync after propagation resolves it.
            print(f"  first sync failed: {item.get('statusReasons')}; resyncing", flush=True)
            time.sleep(20)
            ctl.synchronize_gateway_targets(gatewayIdentifier=gateway_id, targetIdList=[target_id])
            wait(fetch, {"READY"}, {"FAILED", "SYNCHRONIZE_UNSUCCESSFUL"}, target_name)
        deployment.update(target_name=target_name, target_id=target_id, target_endpoint=endpoint)

    print("5/5 state", flush=True)
    with open(args.state, "w") as f:
        json.dump(state, f, indent=1)
    print(json.dumps(state, indent=1))


if __name__ == "__main__":
    main()
