"""Provider-neutral metadata for Gateway-owned user OAuth."""
import re

from foundation_harness.config import digest


def gateway_configuration(value, settings):
    region = settings["region"]
    if (not isinstance(value, dict) or set(value) != {"gateway_id", "gateway_url", "issuer", "client_id"}
            or not re.fullmatch(r"[a-z0-9-]{1,100}", value["gateway_id"])
            or value["gateway_url"] != f"https://{value['gateway_id']}.gateway.bedrock-agentcore.{region}.amazonaws.com/mcp"
            or not re.fullmatch(re.escape(f"https://cognito-idp.{region}.amazonaws.com/{region}_") + r"[A-Za-z0-9]+", value["issuer"])
            or not re.fullmatch(r"[a-z0-9]{1,128}", value["client_id"])):
        raise ValueError("Bind the user Gateway to this Studio's Cognito issuer and client")
    return value


def validate_connection(connection, config, settings):
    from .mcp_onboarding import endpoint_origin
    gateway_configuration(config.get("oauth_gateway"), settings)
    user = connection.get("user_authorization", {})
    if set(user) != {"mode", "provider_name", "provider_digest", "authorization_origin", "return_url", "scopes"}:
        raise ValueError("Invalid Gateway user OAuth binding")
    prefix = config.get("credential_prefix", "")
    if (not prefix or user["mode"] != "gateway"
            or not re.fullmatch(re.escape(prefix + "-mcp-oauth-") + r"[a-z0-9-]{1,48}", user["provider_name"])
            or not re.fullmatch(r"[a-f0-9]{64}", user["provider_digest"])
            or not isinstance(user["scopes"], list) or not 1 <= len(user["scopes"]) <= 10
            or len(set(user["scopes"])) != len(user["scopes"])
            or any(not isinstance(s, str) or not re.fullmatch(r"\S{1,128}", s) for s in user["scopes"])
            or endpoint_origin(user["authorization_origin"]) != user["authorization_origin"]
            or user["return_url"] != endpoint_origin(user["return_url"]) + "/oauth/callback"):
        raise ValueError("Invalid Gateway OAuth provider, scopes or callback")
    arn = (f"arn:aws:bedrock-agentcore:{settings['region']}:{settings['account']}:"
           "token-vault/default/oauth2credentialprovider/" + user["provider_name"])
    if connection["configuration"] != {"credentialProviderType": "OAUTH", "credentialProvider": {
        "oauthCredentialProvider": {"providerArn": arn, "grantType": "AUTHORIZATION_CODE",
            "scopes": user["scopes"], "defaultReturnUrl": user["return_url"]}}}:
        raise ValueError("Native Gateway OAuth configuration differs from its reviewed binding")
    return user


def gateway_for(connection, config, settings):
    if connection.get("user_authorization", {}).get("mode") == "gateway":
        return gateway_configuration(config["oauth_gateway"], settings)
    return {"gateway_id": settings["gateway_id"], "gateway_url": settings["gateway_url"]}


def target_configuration(state):
    value = {"endpoint": state["endpoint"]}
    if state.get("tool_schema"):
        import json
        value["mcpToolSchema"] = {"inlinePayload": json.dumps({"tools": state["tool_schema"]}, sort_keys=True)}
    return {"mcp": {"mcpServer": value}}
