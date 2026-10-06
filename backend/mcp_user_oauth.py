"""Generic Runtime user authorization backed by the native AgentCore token vault."""
from functools import cached_property
import json
import re
from urllib.parse import urlsplit

import boto3
from botocore.config import Config

from foundation_harness.config import digest
from foundation_harness.journey_mcp import USER_TOKEN_HEADER


def validate_user_configuration(value, settings, prefix):
    expected = {"issuer", "client_id", "workload_name", "provider_name", "scopes",
                "return_url", "provider_digest", "authorization_origin"}
    if set(value) != expected:
        raise ValueError("Invalid Runtime user authorization configuration")
    for key, suffix in (("workload_name", "-mcp-users-"), ("provider_name", "-mcp-oauth-")):
        if not re.fullmatch(re.escape(prefix + suffix) + r"[a-z0-9-]{1,48}", value[key]):
            raise ValueError("Use this installation's customer-managed OAuth identity and provider")
    region = re.escape(settings["region"])
    if (not re.fullmatch(r"https://cognito-idp\." + region + r"\.amazonaws\.com/" + region
                        + r"_[A-Za-z0-9]+", value["issuer"])
            or not re.fullmatch(r"[a-z0-9]{1,128}", value["client_id"])
            or not re.fullmatch(r"[a-f0-9]{64}", value["provider_digest"])
            or not isinstance(value["scopes"], list) or not 1 <= len(value["scopes"]) <= 10
            or any(not isinstance(s, str) or not 1 <= len(s) <= 128 or re.search(r"\s", s)
                   for s in value["scopes"]) or len(set(value["scopes"])) != len(value["scopes"])):
        raise ValueError("Invalid Runtime identity or OAuth scope binding")
    for field in ("return_url", "authorization_origin"):
        u = urlsplit(value[field])
        if (u.scheme != "https" or not u.hostname or u.username or u.password
                or u.port not in (None, 443) or u.query or u.fragment
                or u.path != ("/oauth/callback" if field == "return_url" else "")):
            raise ValueError("Use exact HTTPS OAuth callback and authorization origins")
    return value


class OAuthCloud:
    def __init__(self, settings, *, control=None, data=None):
        self.settings, self._control, self._data = settings, control, data

    @cached_property
    def control(self):
        return self._control or boto3.client("bedrock-agentcore-control", region_name=self.settings["region"],
            config=Config(retries={"total_max_attempts": 1}, connect_timeout=5, read_timeout=20))

    @cached_property
    def data(self):
        return self._data or boto3.client("bedrock-agentcore", region_name=self.settings["region"],
            config=Config(retries={"total_max_attempts": 1}, connect_timeout=5, read_timeout=20))

    def configuration(self, runtime_arn, prefix, auth):
        runtime = self.control.get_agent_runtime(agentRuntimeId=runtime_arn.rsplit("/", 1)[-1])
        env = runtime.get("environmentVariables", {})
        if (runtime.get("agentRuntimeArn") != runtime_arn or runtime.get("status") != "READY"
                or USER_TOKEN_HEADER.lower() not in [h.lower() for h in
                    runtime.get("requestHeaderConfiguration", {}).get("requestHeaderAllowlist", [])]
                or env.get("STUDIO_TOKEN_ISSUER") != auth.issuer
                or env.get("STUDIO_TOKEN_CLIENT_ID") != auth.client_id):
            raise ValueError("Runtime must allow and validate this Studio's user token")
        value = {"issuer": auth.issuer, "client_id": auth.client_id,
                 "workload_name": env.get("OAUTH_WORKLOAD_NAME", ""),
                 "provider_name": env.get("OAUTH_PROVIDER_NAME", ""),
                 "scopes": json.loads(env.get("OAUTH_SCOPES", "[]")),
                 "return_url": auth.public_url + "/oauth/callback",
                 "provider_digest": "0" * 64, "authorization_origin": auth.public_url}
        validate_user_configuration(value, self.settings, prefix)
        root = f"arn:aws:bedrock-agentcore:{self.settings['region']}:{self.settings['account']}:"
        identity_arn = root + "workload-identity-directory/default/workload-identity/" + value["workload_name"]
        provider_arn = root + "token-vault/default/oauth2credentialprovider/" + value["provider_name"]
        workload = self.control.get_workload_identity(name=value["workload_name"])
        provider = self.control.get_oauth2_credential_provider(name=value["provider_name"])
        if (workload.get("workloadIdentityArn") != identity_arn
                or workload.get("allowedResourceOauth2ReturnUrls") != [value["return_url"]]
                or provider.get("credentialProviderArn") != provider_arn
                or provider.get("status", "READY") != "READY"):
            raise ValueError("OAuth workload, callback or provider is not ready")
        expected_tags = {"auto-delete": "no", "deployment": prefix, "project": "governed-agent-builder"}
        for arn in (identity_arn, provider_arn):
            tags = self.control.list_tags_for_resource(resourceArn=arn)["tags"]
            if any(tags.get(k) != v for k, v in expected_tags.items()):
                raise ValueError("OAuth resources must belong to this deployment and retain its tags")
        config = provider["oauth2ProviderConfigOutput"]
        secret_arn = provider.get("clientSecretArn", {}).get("secretArn", "")
        if (provider.get("clientSecretSource") != "EXTERNAL" or not provider.get("clientSecretJsonKey")
                or not re.fullmatch(re.escape(f"arn:aws:secretsmanager:{self.settings['region']}:"
                    f"{self.settings['account']}:secret:{prefix}/mcp/") + r"[A-Za-z0-9/_+=.@-]+", secret_arn)):
            raise ValueError("OAuth client credentials must use this deployment's Secrets Manager resources")
        variants = list(config.values())
        if len(variants) != 1:
            raise ValueError("OAuth provider configuration is ambiguous")
        metadata = variants[0]["oauthDiscovery"].get("authorizationServerMetadata", {})
        endpoint = urlsplit(metadata.get("authorizationEndpoint", ""))
        if endpoint.scheme != "https" or not endpoint.hostname or endpoint.username or endpoint.password:
            raise ValueError("Use explicit HTTPS authorization server metadata")
        value["authorization_origin"] = "https://" + endpoint.netloc
        value["provider_digest"] = digest({"arn": provider_arn, "configuration": config,
            "secret_arn": secret_arn, "secret_json_key": provider["clientSecretJsonKey"]})
        return validate_user_configuration(value, self.settings, prefix)

    def token(self, user_token, config, **flow):
        workload = self.data.get_workload_access_token_for_jwt(
            workloadName=config["workload_name"], userToken=user_token)["workloadAccessToken"]
        return self.data.get_resource_oauth2_token(workloadIdentityToken=workload,
            resourceCredentialProviderName=config["provider_name"], scopes=config["scopes"],
            oauth2Flow="USER_FEDERATION", **flow)

    def gateway_provider(self, name, prefix, auth, scopes):
        from .mcp_onboarding import endpoint_origin
        if not re.fullmatch(re.escape(prefix + "-mcp-oauth-") + r"[a-z0-9-]{1,48}", name):
            raise ValueError("Use this installation's OAuth provider")
        arn = (f"arn:aws:bedrock-agentcore:{self.settings['region']}:{self.settings['account']}:"
               "token-vault/default/oauth2credentialprovider/" + name)
        provider = self.control.get_oauth2_credential_provider(name=name)
        tags = self.control.list_tags_for_resource(resourceArn=arn)["tags"]
        secret = provider.get("clientSecretArn", {}).get("secretArn", "")
        if (provider.get("credentialProviderArn") != arn or provider.get("status") != "READY"
                or provider.get("clientSecretSource") != "EXTERNAL" or not provider.get("clientSecretJsonKey")
                or any(tags.get(k) != v for k, v in {
                    "auto-delete": "no", "deployment": prefix, "project": "governed-agent-builder"}.items())
                or not re.fullmatch(re.escape(f"arn:aws:secretsmanager:{self.settings['region']}:"
                    f"{self.settings['account']}:secret:{prefix}/mcp/") + r"[A-Za-z0-9/_+=.@-]+", secret)):
            raise ValueError("OAuth provider must be ready, tagged and backed by this installation's external secret")
        config = provider["oauth2ProviderConfigOutput"]
        if len(config) != 1:
            raise ValueError("Ambiguous OAuth provider configuration")
        metadata = next(iter(config.values()))["oauthDiscovery"].get("authorizationServerMetadata", {})
        origin = endpoint_origin(metadata["authorizationEndpoint"])
        return {"mode": "gateway", "provider_name": name, "scopes": scopes,
                "return_url": auth.public_url + "/oauth/callback", "authorization_origin": origin,
                "provider_digest": digest({"arn": arn, "configuration": config, "secret_arn": secret,
                    "secret_json_key": provider["clientSecretJsonKey"]})}

    def complete(self, user_token, session_uri):
        self.data.complete_resource_token_auth(userIdentifier={"userToken": user_token}, sessionUri=session_uri)
