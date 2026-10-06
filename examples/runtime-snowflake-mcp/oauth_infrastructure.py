"""Optional, account-local OAuth identity. Not installed by the platform."""
import json
import re
import tempfile
from urllib.parse import urlsplit

from aws_cdk import App, CfnOutput, CfnParameter, Environment, Stack, Tags
from aws_cdk import aws_bedrockagentcore as agentcore, aws_secretsmanager as secretsmanager
from aws_cdk.assertions import Template

from snowflake_mcp.identity import OAuthSettings


def names(config):
    prefix, name = config["studio_prefix"], config["name"]
    if (not re.fullmatch(r"[a-z][a-z0-9-]{2,63}", prefix)
            or not re.fullmatch(r"[a-z0-9-]{1,48}", name)):
        raise ValueError("Use the Studio deployment prefix and a lowercase OAuth connection name")
    return prefix + "-mcp-users-" + name, prefix + "-mcp-oauth-" + name


def runtime_oauth(account, region, config, role):
    workload, provider = names(config)
    scopes = ["session:role:" + role, "refresh_token"]
    OAuthSettings(region, config["issuer"], config["client_id"], workload, provider, tuple(scopes))
    origin = urlsplit(config["origin"])
    if (origin.scheme != "https" or not origin.hostname or origin.username or origin.password
            or origin.port not in (None, 443) or origin.path or origin.query or origin.fragment):
        raise ValueError("Use the exact HTTPS Studio origin without a trailing slash")
    env = {"OAUTH_AWS_REGION": region, "STUDIO_TOKEN_ISSUER": config["issuer"], "STUDIO_TOKEN_CLIENT_ID": config["client_id"],
           "OAUTH_WORKLOAD_NAME": workload, "OAUTH_PROVIDER_NAME": provider, "OAUTH_SCOPES": json.dumps(scopes)}
    root = f"arn:aws:bedrock-agentcore:{region}:{account}:"
    directory = root + "workload-identity-directory/default"
    identity = directory + "/workload-identity/" + workload
    vault = root + "token-vault/default"
    return env, [
        {"Effect": "Allow", "Action": ["bedrock-agentcore:GetWorkloadAccessToken",
                                     "bedrock-agentcore:GetWorkloadAccessTokenForJWT"],
         "Resource": [directory, identity]},
        {"Effect": "Allow", "Action": ["bedrock-agentcore:GetResourceOauth2Token"],
         "Resource": [directory, identity, vault, vault + "/oauth2credentialprovider/" + provider]},
        {"Effect": "Allow", "Action": ["secretsmanager:GetSecretValue"],
         "Resource": f"arn:aws:secretsmanager:{region}:{account}:secret:"
                     + config["studio_prefix"] + "/mcp/oauth/" + config["name"] + "-??????"},
        {"Effect": "Deny", "Action": ["bedrock-agentcore:GetWorkloadAccessTokenForUserId"], "Resource": "*"},
    ]


def oauth_template(prefix, account, region, snowflake_account, config):
    workload, provider_name = names(config)
    runtime_oauth(account, region, config, "VALIDATION_ONLY")
    if not re.fullmatch(r"[a-z0-9]+-[a-z0-9]+", snowflake_account):
        raise ValueError("Use the Snowflake organization-account identifier")
    with tempfile.TemporaryDirectory(prefix="runtime-oauth-cdk-") as out:
        app = App(outdir=out, analytics_reporting=False)
        stack = Stack(app, "OAuth", stack_name=prefix + "-oauth", env=Environment(account=account, region=region))
        client_id = CfnParameter(stack, "OAuthClientId", type="String", min_length=1,
                                 description="Snowflake OAuth integration client ID; setup-required only while obtaining the callback")
        secret = secretsmanager.Secret(stack, "ClientSecret",
            secret_name=config["studio_prefix"] + "/mcp/oauth/" + config["name"],
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template="{}", generate_string_key="client_secret", password_length=48))
        identity = agentcore.CfnWorkloadIdentity(stack, "UserIdentity", name=workload,
            allowed_resource_oauth2_return_urls=[config["origin"] + "/oauth/callback"])
        host = "https://" + snowflake_account + ".snowflakecomputing.com"
        provider = agentcore.CfnOAuth2CredentialProvider(stack, "Provider",
            name=provider_name, credential_provider_vendor="CustomOauth2",
            oauth2_provider_config_input=agentcore.CfnOAuth2CredentialProvider.Oauth2ProviderConfigInputProperty(
                custom_oauth2_provider_config=agentcore.CfnOAuth2CredentialProvider.CustomOauth2ProviderConfigInputProperty(
                    client_id=client_id.value_as_string, client_secret_source="EXTERNAL",
                    client_secret_config=agentcore.CfnOAuth2CredentialProvider.SecretReferenceProperty(
                        secret_id=secret.secret_arn, json_key="client_secret"),
                    client_authentication_method="CLIENT_SECRET_BASIC",
                    oauth_discovery=agentcore.CfnOAuth2CredentialProvider.Oauth2DiscoveryProperty(
                        authorization_server_metadata=agentcore.CfnOAuth2CredentialProvider.Oauth2AuthorizationServerMetadataProperty(
                            issuer=host, authorization_endpoint=host + "/oauth/authorize",
                            token_endpoint=host + "/oauth/token-request", response_types=["code"])))))
        for key, value in {"auto-delete": "no", "project": "governed-agent-builder",
                           "deployment": config["studio_prefix"]}.items():
            Tags.of(stack).add(key, value)
        CfnOutput(stack, "CallbackUrl", value=provider.attr_callback_url)
        CfnOutput(stack, "ProviderArn", value=provider.attr_credential_provider_arn)
        CfnOutput(stack, "WorkloadArn", value=identity.attr_workload_identity_arn)
        CfnOutput(stack, "ClientSecretArn", value=secret.secret_arn)
        return Template.from_stack(stack).to_json()
