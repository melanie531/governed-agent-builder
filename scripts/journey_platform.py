"""Publish the platform Catalog and deploy its owned AgentCore integration.

Every command requires an explicit account/profile/region and the existing bound
release-state file. Credentials go directly to AgentCore Identity, never to files.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.parse
import zipfile

from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.dynamo_store import DynamoStore
from backend.foundation_approval import iam_sigv4_credentials, runtime_mcp_endpoint
from backend.foundation_runs import get, put
from backend.journey_catalog import records
from backend.live_catalog import grant_scope, visibility
from foundation_harness.config import digest
from foundation_harness.journey_mcp import GatewayMCP
from infra.journey import template as platform_template
from infra.serverless import template as app_template
from scripts.deployment_target import DeploymentTarget, target_arguments

STACK = "governed-agent-builder-journey"
TAGS = {"project": "governed-agent-builder", "journey": "create-agent"}


def wait(read, *, timeout=900):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = read()
        status = value.get("status", value.get("StackStatus"))
        print("Resource status:", status, flush=True)
        if status in ("READY", "CREATE_COMPLETE", "UPDATE_COMPLETE"):
            return value
        if status in ("FAILED", "CREATE_FAILED", "UPDATE_FAILED", "UPDATE_ROLLBACK_COMPLETE", "ROLLBACK_COMPLETE"):
            raise RuntimeError("Resource failed: " + json.dumps(value.get("statusReasons", [])))
        time.sleep(10)
    raise TimeoutError("Resource did not become ready")


def upload(target):
    bucket = target.state["artifacts"]["outputs"]["Bucket"]
    source = ROOT / "artifacts/serverless-release.zip"
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    key = f"releases/{sha}/lambda.zip"
    from botocore.config import Config
    from boto3.s3.transfer import TransferConfig
    # Sign the payload hash instead of streaming an optional checksum trailer.
    # Some operator HTTPS proxies stall large aws-chunked request bodies.
    s3 = target.session.client("s3", config=Config(
        connect_timeout=5, read_timeout=60, retries={"max_attempts": 2},
        request_checksum_calculation="when_required", response_checksum_validation="when_required",
        s3={"payload_signing_enabled": True}))
    transfer = TransferConfig(multipart_threshold=5 * 1024 * 1024, multipart_chunksize=5 * 1024 * 1024, max_concurrency=2)
    runtime = ROOT / "artifacts/journey-runtime.zip"
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(runtime, "w", zipfile.ZIP_DEFLATED) as output:
        for info in original.infolist():
            if info.filename.startswith(("backend/", "scripts/", "tools/", "foundations/")) or info.filename == "uv.lock":
                continue
            output.writestr(info, original.read(info))
        output.writestr(zipfile.ZipInfo("main.py", date_time=(2026, 1, 1, 0, 0, 0)),
                        "from runtime.journey.main import create_app\ncreate_app().run()\n")
    runtime_sha = hashlib.sha256(runtime.read_bytes()).hexdigest()
    runtime_key = f"journey/foundation/{runtime_sha}.zip"
    previous = target.state.get("journeyRelease", {})
    if (previous.get("lambdaSha256") == sha and previous.get("artifact", {}).get("sha256") == runtime_sha
            and previous["artifact"]["bucket"] == bucket and previous["artifact"]["key"] == runtime_key):
        artifact = previous["artifact"]
        s3.head_object(Bucket=bucket, Key=runtime_key, VersionId=artifact["version_id"])
        print("Reusing verified release artifact", flush=True)
        return bucket, key, artifact
    print("Uploading versioned deployment artifacts", flush=True)
    s3.upload_file(str(source), bucket, key, ExtraArgs={"ServerSideEncryption": "AES256"}, Config=transfer)
    print("Lambda artifact uploaded", flush=True)
    if previous.get("artifact", {}).get("sha256") == runtime_sha and previous["artifact"]["bucket"] == bucket:
        artifact = previous["artifact"]
        s3.head_object(Bucket=bucket, Key=runtime_key, VersionId=artifact["version_id"])
    else:
        s3.upload_file(str(runtime), bucket, runtime_key, ExtraArgs={"ServerSideEncryption": "AES256"}, Config=transfer)
        uploaded = s3.head_object(Bucket=bucket, Key=runtime_key)
        if uploaded.get("VersionId") in (None, "null"):
            raise RuntimeError("Foundation artifact bucket must be versioned")
        artifact = {"bucket": bucket, "key": runtime_key, "version_id": uploaded["VersionId"], "sha256": runtime_sha}
    target.save("journeyRelease", {"lambdaKey": key, "lambdaSha256": sha, "artifact": artifact})
    return bucket, key, artifact


def credential(target, control):
    saved = target.state.get("journeyCredential")
    if saved:
        current = control.get_api_key_credential_provider(name=saved["name"])
        if current["credentialProviderArn"] != saved["provider_arn"]:
            raise RuntimeError("Credential provider identity changed")
        return saved
    key = os.environ.get("TAVILY_API_KEY")
    if not key:
        # Tavily is optional: without a key its target, tools and template are not published.
        print("TAVILY_API_KEY not set: Tavily web tools and the Research template are unavailable", flush=True)
        return None
    result = control.create_api_key_credential_provider(name="gab-journey-tavily", apiKey=key)
    secret = result["apiKeySecretArn"]
    saved = {"name": result["name"], "provider_arn": result["credentialProviderArn"],
             "secret_arn": secret["secretArn"] if isinstance(secret, dict) else secret}
    target.save("journeyCredential", saved)
    print("Tavily credential stored in AgentCore Identity", flush=True)
    return saved


def specialist(target, runtime_arn):
    """Platform-curated specialist MCP Runtime in this account/region, or None."""
    saved = target.state.get("journeySpecialist")
    if runtime_arn is None:
        return saved
    account, region = target.binding["account"], target.binding["region"]
    if not re.fullmatch(rf"arn:aws:bedrock-agentcore:{region}:{account}:runtime/[A-Za-z0-9_][A-Za-z0-9_-]{{0,99}}", runtime_arn):
        raise RuntimeError("Specialist runtime must be an AgentCore Runtime in this platform account and region")
    endpoint = (f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{urllib.parse.quote(runtime_arn, safe='')}"
                "/invocations?qualifier=DEFAULT")
    if not runtime_mcp_endpoint(endpoint, account):
        raise RuntimeError("Specialist endpoint is not this account's Runtime MCP URL")
    saved = {"runtime_arn": runtime_arn, "endpoint": endpoint}
    target.save("journeySpecialist", saved)
    return saved


def stack(target, credential, bucket, key, specialist=None):
    body = platform_template(credential and credential["provider_arn"], credential and credential["secret_arn"],
                             [specialist["runtime_arn"]] if specialist else ())
    target.cf.validate_template(TemplateBody=json.dumps(body))
    request = {"StackName": STACK, "TemplateBody": json.dumps(body), "Capabilities": ["CAPABILITY_IAM"],
               "Tags": [{"Key": key, "Value": value} for key, value in TAGS.items()],
               "Parameters": [{"ParameterKey": "ArtifactBucket", "ParameterValue": bucket},
                              {"ParameterKey": "ArtifactKey", "ParameterValue": key}]}
    try:
        previous = target.cf.describe_stacks(StackName=STACK)["Stacks"][0]
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ValidationError" or "does not exist" not in exc.response["Error"]["Message"]:
            raise
        previous = None
    if previous:
        if {t["Key"]: t["Value"] for t in previous["Tags"]} != TAGS:
            raise RuntimeError("Refusing an unowned platform stack")
        if previous["StackStatus"].endswith("IN_PROGRESS"):
            raise RuntimeError("Platform stack already updating")
        try:
            target.cf.update_stack(**request)
        except ClientError as exc:
            if "No updates are to be performed" not in exc.response["Error"]["Message"]:
                raise
    else:
        target.cf.create_stack(**request)
    result = wait(lambda: target.cf.describe_stacks(StackName=STACK)["Stacks"][0])
    outputs = {entry["OutputKey"]: entry["OutputValue"] for entry in result["Outputs"]}
    target.save("journeyStack", {"id": result["StackId"], "outputs": outputs})
    return outputs


def gateway(target, control, outputs, credential, specialist=None):
    saved = target.state.get("journeyGateway")
    if not saved:
        created = control.create_gateway(name="gab-journey-tools", roleArn=outputs["GatewayRole"],
            authorizerType="AWS_IAM", protocolType="MCP", tags=TAGS,
            protocolConfiguration={"mcp": {"supportedVersions": ["2025-03-26"]}},
            clientToken=digest([target.binding, "journey-gateway"]))
        saved = {"id": created["gatewayId"], "arn": created["gatewayArn"]}
        target.save("journeyGateway", saved)
    current = wait(lambda: control.get_gateway(gatewayIdentifier=saved["id"]))
    if current["roleArn"] != outputs["GatewayRole"] or current["authorizerType"] != "AWS_IAM":
        raise RuntimeError("Gateway security binding changed")
    configurations = {}
    if credential:
        configurations["tavily"] = {
            "targetConfiguration": {"mcp": {"mcpServer": {"endpoint": "https://mcp.tavily.com/mcp/"}}},
            "credentialProviderConfigurations": [{"credentialProviderType": "API_KEY", "credentialProvider": {
                "apiKeyCredentialProvider": {"providerArn": credential["provider_arn"],
                    "credentialParameterName": "Authorization", "credentialPrefix": "Bearer", "credentialLocation": "HEADER"}}}]}
    if specialist:
        # Outbound SigV4 as the Gateway role, which may invoke only this Runtime (infra/journey.py).
        configurations["risk-analyst-specialist"] = {
            "targetConfiguration": {"mcp": {"mcpServer": {"endpoint": specialist["endpoint"]}}},
            "credentialProviderConfigurations": [{"credentialProviderType": "GATEWAY_IAM_ROLE", "credentialProvider": {
                "iamCredentialProvider": {"service": "bedrock-agentcore", "region": target.binding["region"]}}}]}
        if not iam_sigv4_credentials(configurations["risk-analyst-specialist"]["credentialProviderConfigurations"]):
            raise RuntimeError("Specialist target requires complete IAM SigV4 credentials")
    from tools.knowledge_search import SCHEMA
    configurations["knowledge"] = {
        "targetConfiguration": {"mcp": {"lambda": {"lambdaArn": outputs["KnowledgeFunction"],
            "toolSchema": {"inlinePayload": [SCHEMA]}}}},
        "credentialProviderConfigurations": [{"credentialProviderType": "GATEWAY_IAM_ROLE"}]}
    targets = target.state.get("journeyTargets", {})
    for name, config in configurations.items():
        if name not in targets:
            result = control.create_gateway_target(gatewayIdentifier=saved["id"], name=name,
                clientToken=digest([saved["id"], name]), **config)
            targets[name] = result["targetId"]
            target.save("journeyTargets", targets)
        try:
            result = wait(lambda: control.get_gateway_target(gatewayIdentifier=saved["id"], targetId=targets[name]))
        except RuntimeError:
            if name != "risk-analyst-specialist":
                raise
            # The first tool sync can race IAM propagation of the new Gateway role statement.
            print("Specialist target sync failed; resynchronizing once", flush=True)
            time.sleep(20)
            control.synchronize_gateway_targets(gatewayIdentifier=saved["id"], targetIdList=[targets[name]])
            result = wait(lambda: control.get_gateway_target(gatewayIdentifier=saved["id"], targetId=targets[name]))
        if result["targetConfiguration"] != config["targetConfiguration"]:
            raise RuntimeError("Gateway target changed")
    saved["url"] = current["gatewayUrl"]
    target.save("journeyGateway", saved)
    client = GatewayMCP(target.session, current["gatewayUrl"])
    tools = client.discover()
    print("Gateway tools discovered:", [tool["name"] for tool in tools], flush=True)
    target.save("journeyDiscovery", {"tools": tools, "time": time.time()})
    probes = []
    for publication in json.loads((ROOT / "examples/journey/tool-publications.json").read_text()):
        if publication["target"] not in targets:
            continue
        name = publication["target"] + "___" + publication["operation"]
        text = client.call(name, publication["probe_arguments"])
        probes.append({"name": name, "success": bool(text.strip()), "time": time.time()})
        print("Gateway tool invocation verified:", name, flush=True)
    target.save("journeyToolProbes", probes)
    return saved, targets, tools


def publish(target, outputs, artifact, gateway, targets, discovered):
    control = target.session.client("bedrock-agentcore-control")
    evaluator = control.get_evaluator(evaluatorId="Builtin.Correctness")
    data = ROOT / "examples/journey"
    settings = {"enabled": True, **{key: target.binding[key] for key in ("account", "region")},
                "foundation": json.loads((data / "foundation.json").read_text()), "artifact": artifact,
                "runtime_role": outputs["RuntimeRole"], "gateway_id": gateway["id"], "gateway_url": gateway["url"],
                "bucket": outputs["EvidenceBucket"], "log_group": outputs["TraceLogGroup"],
                "network": {"networkMode": "PUBLIC"}, "evaluator_id": evaluator["evaluatorId"],
                "evaluator_arn": evaluator["evaluatorArn"]}
    if target.state.get("platformAdmin"):
        settings["admin_enabled"] = True
        if target.state["platformAdmin"].get("registry_arn"):
            settings["registry_arn"] = target.state["platformAdmin"]["registry_arn"]
    items = []
    def item(cid, name, kind, description, binding, workspaces, **extra):
        return {"id": cid, "name": name, "kind": kind, "description": description, "binding": binding,
                "binding_digest": digest(binding), "catalog": "journey", "version": "1", "approved": True,
                "fixture": False, "external": False, "origin": "AI Catalog", "execution_ready": True,
                "integration_ready": True, "supported": True, "requestable": True,
                "discoverable_workspaces": workspaces, "default_grant_workspaces": workspaces, **extra}
    for model in json.loads((data / "models.json").read_text()):
        binding = {"type": "bedrock-converse", "model_id": model["model_id"],
                   "supports_temperature": model["supports_temperature"]}
        # Publish only a model that can actually run in this target account.
        inference = {"maxTokens": 64}
        if model["supports_temperature"]:
            inference["temperature"] = 0
        result = target.session.client("bedrock-runtime").converse(modelId=model["model_id"],
            messages=[{"role": "user", "content": [{"text": "Reply READY."}]}], inferenceConfig=inference)
        if not result.get("output", {}).get("message", {}).get("content"):
            raise RuntimeError("Model did not return content")
        items.append(item(model["id"], model["name"], "model", model["description"], binding, model["workspaces"],
                          provider=model["provider"], model_id=model["model_id"]))
    for skill in json.loads((data / "skills.json").read_text()):
        binding = {"type": "instructions", "instructions": skill["instruction"], "content_digest": digest(skill["instruction"])}
        items.append(item(skill["id"], skill["name"], "skill", skill["description"], binding,
                          ["research", "operations"], provider="Platform skill library", protocol="instructions"))
    tool_inputs = [tool for tool in json.loads((data / "tool-publications.json").read_text()) if tool["target"] in targets]
    published_tools = {tool["id"] for tool in tool_inputs}
    for server in json.loads((data / "mcp-servers.json").read_text()):
        if server["target"] not in targets:
            continue
        binding = {"type": "mcp-server", "gateway_id": gateway["id"], "target_id": targets[server["target"]]}
        items.append(item(server["id"], server["name"], "mcp_server", server["description"], binding, server["workspaces"],
                          provider=server["provider"], protocol="MCP", external=server["external"],
                          approved_external_workspaces=server["workspaces"] if server["external"] else [],
                          data_handling=server["data_handling"], default_tool_ids=server["default_tool_ids"]))
    for tool in tool_inputs:
        from foundation_harness.journey_tools import model_schema
        name = tool["target"] + "___" + tool["operation"]
        descriptor = next((entry for entry in discovered if entry["name"] == name), None)
        if descriptor is None:
            raise RuntimeError("Approved tool was not discovered through Gateway: " + name)
        binding = {"type": "mcp", "gateway_id": gateway["id"], "target_id": targets[tool["target"]],
                   "name": name, "inputSchema": descriptor["inputSchema"], "schema_digest": digest(descriptor["inputSchema"])}
        if tool.get("argument_controls"):
            binding["argument_controls"] = tool["argument_controls"]
        effective_schema = model_schema(binding)
        items.append(item(tool["id"], tool["name"], "tool", descriptor["description"], binding, tool["workspaces"],
                          provider=tool["target"], protocol="MCP", parent_id="mcp-" + tool["target"],
                          operation=tool["operation"], inputSchema=effective_schema, external=tool["external"],
                          approved_external_workspaces=tool["workspaces"] if tool["external"] else [],
                          data_handling=tool["data_handling"]))
    store = DynamoStore(target.state["app"]["outputs"]["StateTable"], target.session.resource("dynamodb"))
    with store.tx() as db:
        existing = {entry["id"]: entry for entry in records(db)}
        for entry in items:
            prior = existing.get(entry["id"])
            if prior and prior.get("managed_by") == "platform-admin":
                if prior["binding_digest"] != entry["binding_digest"]:
                    raise RuntimeError("An administrator-managed Catalog binding needs review: " + entry["id"])
                continue
            if prior:
                comparable = {key: value for key, value in prior.items() if key != "version"}
                entry["version"] = str(int(prior["version"]) + (comparable != {key: value for key, value in entry.items() if key != "version"}))
            db.insert("components", {"id": entry["id"], "body": json.dumps(entry)}, upsert=True)
        for entry in json.loads((data / "templates.json").read_text()):
            stored = db.select("foundations", where=[("id", "=", entry["id"])]).fetchone()
            if stored and json.loads(stored["body"]).get("managed_by") == "platform-admin":
                continue
            # A template whose tools are not published (e.g. no Tavily key) is unavailable.
            entry.update(catalog="journey", version="1", approved=set(entry["tools"]) <= published_tools,
                         workspaces=["research", "operations"], requires_tool=entry["id"] == "knowledge")
            db.insert("foundations", {"id": entry["id"], "body": json.dumps(entry)}, upsert=True)
        put(db, "journey-platform", settings)
        if not get(db, "journey-grants-migrated"):
            for row in db.select("principals"):
                actor = json.loads(row["body"])
                for entry in items:
                    if actor["workspace"] in entry["default_grant_workspaces"] and visibility(entry, actor):
                        db.insert("grants", {"persona": actor["id"], "component": entry["id"]}, ignore=True)
                        put(db, grant_scope(actor, entry["id"]), True)
            put(db, "journey-grants-migrated", True)
    target.save("journeyPlatform", settings)
    print("Published backend Catalog:", len(items), "capabilities", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["prepare", "activate", "publish-ui"])
    parser.add_argument("--specialist-runtime-arn", help="Platform-curated specialist MCP Runtime to register as a Gateway target")
    target_arguments(parser)
    args = parser.parse_args()
    target = DeploymentTarget(args.expected_account, args.profile, args.region, args.state)
    if args.action == "prepare":
        control = target.session.client("bedrock-agentcore-control")
        provider = credential(target, control)
        curated = specialist(target, args.specialist_runtime_arn)
        bucket, key, artifact = upload(target)
        outputs = stack(target, provider, bucket, key, curated)
        gw, targets, tools = gateway(target, control, outputs, provider, curated)
        publish(target, outputs, artifact, gw, targets, tools)
    else:
        from scripts import serverless_deploy as release
        release.TARGET, release.SESSION, release.CF, release.STATE = target, target.session, target.cf, target.path
        if args.action == "activate":
            settings = target.state["journeyPlatform"]
            release.deploy("app", app_template(journey=settings), {
                "ArtifactBucket": target.state["artifacts"]["outputs"]["Bucket"],
                "ArtifactKey": target.state["journeyRelease"]["lambdaKey"]})
            target.save("releaseSha256", target.state["journeyRelease"]["lambdaSha256"])
        else:
            release.main("publish")


if __name__ == "__main__":
    main()
