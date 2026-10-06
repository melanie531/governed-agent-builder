"""Verify IAM MCP discovery; optional OAuth queries take an ephemeral Studio user token."""
import argparse
import getpass
import json
from pathlib import Path
from uuid import uuid4

import boto3
from botocore.config import Config
import httpx
from snowflake_mcp.identity import USER_TOKEN_HEADER


def verify(state, *, query=False, table=None, user_token=None):
    if query and (not isinstance(user_token, str) or len(user_token) > 4096 or user_token.count(".") != 2):
        raise ValueError("OAuth query verification requires the consenting Studio user's current access token")
    config = state["config"]
    session = boto3.Session(profile_name=config["profile"], region_name=config["region"])
    if session.client("sts").get_caller_identity()["Account"] != config["account"]:
        raise ValueError("Verification account mismatch")
    data = session.client("bedrock-agentcore", config=Config(
        retries={"total_max_attempts": 1}, connect_timeout=10, read_timeout=70))
    sid = str(uuid4())
    sequence = 0

    def rpc(method, params=None, *, user=False):
        nonlocal sequence
        sequence += 1
        event = "before-sign.bedrock-agentcore.InvokeAgentRuntime"
        def header(request, **_):
            request.headers[USER_TOKEN_HEADER] = user_token
        if user:
            data.meta.events.register(event, header, unique_id="verification-user-context")
        try:
            response = data.invoke_agent_runtime(
                agentRuntimeArn=state["runtime"]["outputs"]["RuntimeArn"], qualifier="DEFAULT",
                runtimeSessionId=sid, contentType="application/json", accept="application/json, text/event-stream",
                payload=json.dumps({"jsonrpc": "2.0", "id": sequence, "method": method,
                                    **({"params": params} if params is not None else {})}).encode())
        finally:
            if user:
                data.meta.events.unregister(event, unique_id="verification-user-context")
        try:
            raw = response["response"].read(100001)
        finally:
            response["response"].close()
        if len(raw) > 100000:
            raise ValueError("MCP response exceeded the verification limit")
        text = raw.decode()
        if text.startswith("event:") or text.startswith("data:"):
            text = next(line[6:] for line in text.splitlines() if line.startswith("data: "))
        value = json.loads(text)
        if value.get("id") != sequence or "error" in value:
            raise RuntimeError("MCP protocol request failed; inspect bounded runtime diagnostics")
        return value["result"]

    def content(response):
        try:
            value = response.get("structuredContent") or json.loads(response["content"][0]["text"])
            return value if isinstance(value, dict) else {}
        except (KeyError, ValueError, TypeError):
            return {}

    result = {"runtime_arn": state["runtime"]["outputs"]["RuntimeArn"]}
    anonymous = httpx.post(state["endpoint"], json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, timeout=20)
    result["anonymous_denied"] = anonymous.status_code in (401, 403)
    initialized = rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                     "clientInfo": {"name": "runtime-mcp-verification", "version": "1.0"}})
    result["initialized"] = bool(initialized.get("protocolVersion"))
    listed = rpc("tools/list")
    result["tools"] = sorted(t["name"] for t in listed["tools"])
    result["discovery_pass"] = result["tools"] == sorted([
        "test_connection", "list_databases", "list_schemas", "list_tables",
        "list_views", "describe_table", "query"])
    rejected = rpc("tools/call", {"name": "query", "arguments": {"sql": "SELECT 1; SELECT 2"}})
    result["multiple_statements_rejected"] = bool(rejected.get("isError"))
    missing = rpc("tools/call", {"name": "query", "arguments": {"sql": "SELECT 1"}})
    result["missing_user_rejected"] = bool(missing.get("isError"))
    result["oauth_query"] = "NOT_RUN"
    if query:
        response = rpc("tools/call", {"name": "query", "arguments": {"sql": "SELECT 1 AS MCP_CONNECTION_CHECK", "max_rows": 1}}, user=True)
        value = content(response)
        result["oauth_query"] = "PASS" if not response.get("isError") and value.get("rows") == [[1]] else "FAIL"
        result["query_id"] = value.get("query_id")
        if result["oauth_query"] == "PASS":
            response = rpc("tools/call", {"name": "test_connection", "arguments": {}}, user=True)
            rows = content(response).get("rows", [])
            result["configured_role_active"] = bool(rows and rows[0][2] == config["snowflake_role"])
            response = rpc("tools/call", {"name": "list_databases", "arguments": {}}, user=True)
            result["metadata_discovery"] = not response.get("isError") and "rows" in content(response)
            if table:
                from snowflake_mcp.database import identifier
                parts = table.split(".")
                if len(parts) != 3:
                    raise ValueError("Use a fully qualified database.schema.table")
                sql = "SELECT COUNT(*) AS ROW_COUNT FROM " + ".".join(identifier(p) for p in parts)
                response = rpc("tools/call", {"name": "query", "arguments": {"sql": sql, "max_rows": 1}}, user=True)
                value = content(response)
                result["table_read"] = not response.get("isError") and len(value.get("rows", [])) == 1
                result["table_query_id"] = value.get("query_id")
    result["pass"] = all(result[k] for k in ("anonymous_denied", "initialized", "discovery_pass", "multiple_statements_rejected", "missing_user_rejected"))
    if query:
        result["pass"] = result["pass"] and result["oauth_query"] == "PASS" and result.get("configured_role_active") and result.get("metadata_discovery")
        if table:
            result["pass"] = bool(result["pass"] and result.get("table_read"))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--query", action="store_true")
    parser.add_argument("--table", help="With --query, verify COUNT(*) over an approved database.schema.table without recording its data")
    args = parser.parse_args()
    if args.table and not args.query:
        parser.error("--table requires --query")
    result = verify(json.loads(args.state.read_text()), query=args.query, table=args.table,
                    user_token=getpass.getpass("Current consenting Studio user's access JWT (hidden): ") if args.query else None)
    args.output.write_text(json.dumps(result, indent=2))
    print(json.dumps(result))
    if not result["pass"]:
        raise SystemExit(1)
