"""Authenticated HTTP API -> IAM Runtime adapter for native Gateway OAuth.

API Gateway invokes authorize before invoke. No request/header logging, redirects,
credential acquisition, or retry of a submitted MCP operation occurs here.
"""
import base64
from functools import lru_cache
import hashlib
import hmac
import json
import logging
import os
import re
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

import boto3
from botocore.config import Config

TOKEN_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Custom-Snowflake-Token"
ACCESS_TOKEN_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Custom-Access-Token"
NO_RETRY = Config(retries={"total_max_attempts": 1}, connect_timeout=3, read_timeout=27)
for _logger in ("botocore", "urllib3"):
    logging.getLogger(_logger).disabled = True


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


urlopen = build_opener(ProxyHandler({}), NoRedirect()).open


@lru_cache(maxsize=1)
def origin_secret():
    return boto3.client("secretsmanager", config=NO_RETRY).get_secret_value(
        SecretId=os.environ["ORIGIN_SECRET_ARN"])["SecretString"]


def bearer(event):
    value = event.get("headers", {}).get("authorization", "")
    if not re.fullmatch(r"Bearer [A-Za-z0-9._~+/-]{16,16384}={0,2}", value):
        raise ValueError("Authorization required")
    return value[7:]


def binding(event):
    if not os.getenv("STATE_TABLE"):
        return {key: os.environ[env] for key, env in {
            "runtime_arn": "RUNTIME_ARN", "snowflake_account": "SNOWFLAKE_ACCOUNT",
            "snowflake_role": "SNOWFLAKE_ROLE", "warehouse": "SNOWFLAKE_WAREHOUSE",
        }.items() if env in os.environ}
    match = re.fullmatch(r"/mcp/([a-f0-9]{32})", event.get("rawPath", ""))
    if not match:
        raise ValueError("Unknown Python MCP route")
    sid = match[1]
    item = boto3.client("dynamodb", config=NO_RETRY).get_item(
        TableName=os.environ["STATE_TABLE"], ConsistentRead=True,
        Key={"pk": {"S": "settings"}, "sk": {"S": json.dumps(["mcp-python:" + sid], separators=(",", ":"))}}).get("Item")
    state = json.loads(json.loads(item["body"]["S"])["body"]) if item else {}
    if (state.get("id") != sid or state.get("phase") != "READY" or state.get("runtime_version") != "1"
            or not re.fullmatch(re.escape(os.environ["RUNTIME_ARN_PREFIX"] + sid[:24]) + r"-[A-Za-z0-9]+",
                                state.get("runtime_arn", ""))
            or state.get("runtime_id") != state["runtime_arn"].rsplit("/", 1)[-1]):
        raise ValueError("Python MCP is not ready at its owned Runtime")
    value = {k: state[k] for k in ("runtime_arn", "runtime_id", "runtime_version")}
    if state.get("connection_mode") == "PACKAGE":
        tool = state.get("bearer_validation_tool", "")
        if not isinstance(tool, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}", tool):
            raise ValueError("Package has no bearer authorization contract")
        value.update(connection_mode="PACKAGE", bearer_validation_tool=tool)
    else:
        value.update({k: state[k] for k in ("snowflake_account", "snowflake_role", "warehouse")})
    value["python_id"] = sid
    value["binding_digest"] = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    return value


def authorize(event, context):
    try:
        if not hmac.compare_digest(event.get("headers", {}).get("x-studio-origin", ""), origin_secret()):
            return {"isAuthorized": False}
        token = bearer(event)
        configured = binding(event)
        if configured.get("connection_mode") == "PACKAGE":
            status, raw, _ = invoke_runtime(configured, token, json.dumps({
                "jsonrpc": "2.0", "id": "studio-package-auth", "method": "tools/call",
                "params": {"name": configured["bearer_validation_tool"], "arguments": {}},
            }).encode(), maximum=16384)
            value = json.loads(raw)
            result = value.get("result", {})
            if (not 200 <= status < 300 or value.get("jsonrpc") != "2.0"
                    or value.get("id") != "studio-package-auth" or "error" in value
                    or result.get("isError") or result.get("structuredContent") != {"authorized": True}
                    or result["structuredContent"]["authorized"] is not True):
                return {"isAuthorized": False}
            return {"isAuthorized": True, "context": {"package": "verified",
                **{k: configured[k] for k in ("python_id", "binding_digest")}}}
        account, role = configured["snowflake_account"], configured["snowflake_role"]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", account):
            return {"isAuthorized": False}
        origin = f"https://{account}.snowflakecomputing.com"
        headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json",
                   "Accept": "application/json", "X-Snowflake-Authorization-Token-Type": "OAUTH"}
        request = Request(origin + "/api/v2/statements", method="POST", headers=headers, data=json.dumps({
            "statement": "SELECT CURRENT_USER(), CURRENT_ROLE()", "timeout": 5,
            "role": role, "warehouse": configured["warehouse"],
        }).encode())
        with urlopen(request, timeout=8) as response:
            value = json.loads(response.read(16385))
            if response.status != 200:
                return {"isAuthorized": False}
        rows = value.get("data")
        if (not isinstance(rows, list) or len(rows) != 1 or len(rows[0]) != 2
                or not isinstance(rows[0][0], str) or not rows[0][0] or rows[0][1] != role):
            return {"isAuthorized": False}
        return {"isAuthorized": True, "context": {"snowflake": "verified",
            **{k: configured[k] for k in ("python_id", "binding_digest") if k in configured}}}
    except Exception:
        return {"isAuthorized": False}


def result(status, body=""):
    return {"statusCode": status, "headers": {"content-type": "application/json", "cache-control": "no-store"},
            "body": body if isinstance(body, str) else json.dumps(body)}


def invoke_runtime(configured, token, payload, maximum=512 * 1024):
    if "python_id" in configured:
        endpoint = boto3.client("bedrock-agentcore-control", config=NO_RETRY).get_agent_runtime_endpoint(
            agentRuntimeId=configured["runtime_id"], endpointName="DEFAULT")
        if (endpoint.get("status") != "READY" or endpoint.get("agentRuntimeArn") != configured["runtime_arn"]
                or endpoint.get("liveVersion") != configured["runtime_version"]
                or endpoint.get("targetVersion", configured["runtime_version"]) != configured["runtime_version"]):
            raise ValueError("The MCP Runtime version changed")
    # A per-request hook never reuses another caller's token in a warm Lambda.
    client = boto3.client("bedrock-agentcore", config=NO_RETRY)
    def private_header(request, **kwargs):
        header = ACCESS_TOKEN_HEADER if configured.get("connection_mode") == "PACKAGE" else TOKEN_HEADER
        request.headers[header] = token
    client.meta.events.register("before-sign.bedrock-agentcore.InvokeAgentRuntime", private_header)
    response = client.invoke_agent_runtime(
        agentRuntimeArn=configured["runtime_arn"], qualifier="DEFAULT",
        runtimeSessionId="sf-" + hashlib.sha256(token.encode()).hexdigest(), contentType="application/json",
        accept="application/json, text/event-stream", payload=payload)
    stream = response["response"]
    try:
        data = stream.read(maximum + 1)
    finally:
        stream.close()
    if len(data) > maximum:
        raise ValueError("MCP response exceeds the bound")
    return response.get("statusCode", 200), data, response.get("contentType", "application/json")


def invoke(event, context):
    authorization = event.get("requestContext", {}).get("authorizer", {}).get("lambda", {})
    if not any(authorization.get(k) == "verified" for k in ("snowflake", "package")):
        return result(403)
    if not os.getenv("STATE_TABLE") and event.get("rawPath") != "/mcp":
        return result(404)
    try:
        configured = binding(event)
        if authorization.get("package" if configured.get("connection_mode") == "PACKAGE" else "snowflake") != "verified":
            return result(403)
        if any(authorization.get(k) != configured[k] for k in ("python_id", "binding_digest") if k in configured):
            return result(403)
    except Exception:
        return result(403)
    if event.get("requestContext", {}).get("http", {}).get("method") != "POST":
        return result(405)
    try:
        token = bearer(event)
        body = event.get("body", "")
        if len(body) > 180_000:
            return result(413)
        payload = base64.b64decode(body, validate=True) if event.get("isBase64Encoded") else body.encode()
        if len(payload) > 128 * 1024:
            return result(413)
        message = json.loads(payload)
        if (not isinstance(message, dict) or message.get("jsonrpc") != "2.0"
                or not isinstance(message.get("method"), str)):
            return result(400)
    except Exception:
        return result(400)
    try:
        status, data, content_type = invoke_runtime(configured, token, payload)
        if not 200 <= status < 300:
            return result(502)
        return {**result(status, data.decode()), "headers": {
            "content-type": content_type,
            "cache-control": "no-store"}}
    except Exception:
        return result(502, {"error": "The MCP Runtime could not complete the request."})
