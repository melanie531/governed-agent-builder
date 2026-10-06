"""Enforce the current Studio catalog and grants at the Cognito Gateway.

The REQUEST interceptor returns a filtered tools/list directly and denies tool
calls before Gateway obtains an outbound token. It never logs headers or bodies.
"""
from functools import lru_cache
import json
import os
import time

import boto3
import jwt

from foundation_harness.config import digest
from .journey_catalog import resolve
from .foundation_runs import get


def deny(identifier=None):
    return {"interceptorOutputVersion": "1.0", "mcp": {"transformedGatewayResponse": {
        "statusCode": 403, "body": {"jsonrpc": "2.0", "id": identifier,
            "error": {"code": -32001, "message": "This tool is not available in your current Studio workspace."}}}}}


def intercept(event, db, subject, gateway_id):
    body = event.get("mcp", {}).get("gatewayRequest", {}).get("body", {})
    if not isinstance(body, dict):
        return deny()
    identifier = body.get("id")
    row = db.select("principals", where=[("id", "=", subject)]).fetchone()
    if not row or row["expires"] <= time.time():
        return deny(identifier)
    actor = json.loads(row["body"])
    if actor["id"] != subject or actor["role"] != "business":
        return deny(identifier)
    if body.get("method") in ("initialize", "notifications/initialized", "ping"):
        return {"interceptorOutputVersion": "1.0", "mcp": {"transformedGatewayRequest": {"body": body}}}
    if body.get("method") not in ("tools/list", "tools/call"):
        return deny(identifier)
    tools = {}
    for row in db.select("components"):
        item = json.loads(row["body"])
        binding = item.get("binding", {})
        if item.get("kind") != "tool" or binding.get("type") != "mcp" or binding.get("gateway_id") != gateway_id:
            continue
        try:
            resolved = resolve(db, actor, {"tool": [item["id"]], "mcp_server": [item["parent_id"]]})
            parent = resolved[item["parent_id"]]["binding"]
            if (parent["gateway_id"] != gateway_id or parent["target_id"] != binding["target_id"]
                    or digest(binding["inputSchema"]) != binding["schema_digest"]):
                continue
        except Exception:
            continue
        tools[binding["name"]] = {"name": binding["name"], "description": item["description"],
                                  "inputSchema": binding["inputSchema"]}
    if body["method"] == "tools/list":
        return {"interceptorOutputVersion": "1.0", "mcp": {"transformedGatewayResponse": {
            "statusCode": 200, "body": {"jsonrpc": "2.0", "id": identifier,
                                       "result": {"tools": sorted(tools.values(), key=lambda t: t["name"])}}}}}
    if body.get("params", {}).get("name") not in tools:
        return deny(identifier)
    return {"interceptorOutputVersion": "1.0", "mcp": {"transformedGatewayRequest": {"body": body}}}


@lru_cache(maxsize=1)
def jwks():
    return jwt.PyJWKClient(os.environ["COGNITO_ISSUER"] + "/.well-known/jwks.json", timeout=5)


def handler(event, context):
    from .dynamo_store import DynamoUnit
    try:
        headers = {k.lower(): v for k, v in event["mcp"]["gatewayRequest"]["headers"].items()}
        auth = headers.get("authorization", "")
        if not auth.startswith("Bearer ") or len(auth) > 4103:
            return deny()
        token = auth[7:]
        claims = jwt.decode(token, jwks().get_signing_key_from_jwt(token).key, algorithms=["RS256"],
            issuer=os.environ["COGNITO_ISSUER"], options={"verify_aud": False, "require": [
                "exp", "iat", "sub", "iss", "client_id", "token_use"]})
        if claims["client_id"] != os.environ["COGNITO_CLIENT"] or claims["token_use"] != "access":
            return deny()
        table = boto3.resource("dynamodb").Table(os.environ["STATE_TABLE"])
        db = DynamoUnit(table)
        config = get(db, "mcp-onboarding") or {}
        gateway_id = config.get("oauth_gateway", {}).get("gateway_id", "")
        if not gateway_id.startswith(os.environ["GATEWAY_NAME"] + "-"):
            return deny()
        response = intercept(event, db, claims["sub"], gateway_id)
        # Consistent reads plus the shared governance fence prevent a concurrent
        # revocation from being accepted using a mixed catalog/grant snapshot.
        revision = table.get_item(Key={"pk": "_revision", "sk": "_revision"}, ConsistentRead=True).get("Item", {}).get("revision", 0)
        if int(revision) != db.revision:
            return deny()
        return response
    except Exception:
        return deny()
