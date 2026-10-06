"""Snowflake bearer supplied by the IAM-authenticated MCP facade.

This header is a private transport field, never an MCP argument. Runtime's IAM
authorizer and header allowlist are required; this module does not authenticate
public HTTP callers or acquire credentials.
"""
import re

SNOWFLAKE_TOKEN_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Custom-Snowflake-Token"
ACCESS_TOKEN_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Custom-Access-Token"


def supplied_token(headers):
    values = [value for name, value in headers.items()
              if name.lower() in (SNOWFLAKE_TOKEN_HEADER.lower(), ACCESS_TOKEN_HEADER.lower())]
    if len(values) != 1 or not isinstance(values[0], str) or not re.fullmatch(
            r"[A-Za-z0-9._~+/-]{16,16384}={0,2}", values[0]):
        raise ValueError("Snowflake authorization is required. Connect your account through Gateway.")
    return values[0]
