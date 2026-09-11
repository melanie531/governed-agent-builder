"""Render isolated Cognito CloudFormation. No AWS calls, secrets or user creation.

python infra/identity.py --application-origin https://DISTRIBUTION.cloudfront.net
The origin is required: never create a login client with a placeholder callback.
"""
import argparse
import json
import re
from urllib.parse import urlparse


def template(origin):
    parsed = urlparse(origin)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/") or parsed.port not in (None, 443):
        raise ValueError("An exact HTTPS application origin is required")
    origin = origin.rstrip("/")
    tags = {"project": "governed-agent-builder", "owner": "melanie531", "managedBy": "cloudformation"}
    resources = {
        "Pool": {"Type": "AWS::Cognito::UserPool", "DeletionPolicy": "Retain", "UpdateReplacePolicy": "Retain", "Properties": {
            "UserPoolName": "governed-agent-builder-studio", "UserPoolTags": tags,
            "AdminCreateUserConfig": {"AllowAdminCreateUserOnly": True},
            "UsernameAttributes": ["email"], "UsernameConfiguration": {"CaseSensitive": False},
            "AutoVerifiedAttributes": ["email"], "AccountRecoverySetting": {"RecoveryMechanisms": [{"Name": "verified_email", "Priority": 1}]},
            "Policies": {"PasswordPolicy": {"MinimumLength": 14, "RequireLowercase": True, "RequireUppercase": True, "RequireNumbers": True, "RequireSymbols": True, "TemporaryPasswordValidityDays": 1}},
            "UserPoolTier": "ESSENTIALS", "MfaConfiguration": "OPTIONAL", "SoftwareTokenMfaConfiguration": {"Enabled": True},
            "DeletionProtection": "ACTIVE",
        }},
        "Client": {"Type": "AWS::Cognito::UserPoolClient", "Properties": {
            "UserPoolId": {"Ref": "Pool"}, "ClientName": "governed-agent-builder-studio-web", "GenerateSecret": False,
            "AllowedOAuthFlowsUserPoolClient": True, "AllowedOAuthFlows": ["code"], "AllowedOAuthScopes": ["openid", "email", "profile"],
            "CallbackURLs": [origin + "/auth/callback"], "LogoutURLs": [origin + "/"], "DefaultRedirectURI": origin + "/auth/callback",
            "SupportedIdentityProviders": ["COGNITO"], "PreventUserExistenceErrors": "ENABLED", "EnableTokenRevocation": True,
            "ExplicitAuthFlows": ["ALLOW_REFRESH_TOKEN_AUTH"],
            "AccessTokenValidity": 15, "IdTokenValidity": 15, "RefreshTokenValidity": 1,
            "TokenValidityUnits": {"AccessToken": "minutes", "IdToken": "minutes", "RefreshToken": "days"},
            "ReadAttributes": ["email", "email_verified"], "WriteAttributes": [],
        }},
        "Domain": {"Type": "AWS::Cognito::UserPoolDomain", "Properties": {
            "UserPoolId": {"Ref": "Pool"}, "Domain": {"Fn::Join": ["-", ["governed-agent-builder", {"Fn::Select": [2, {"Fn::Split": ["/", {"Ref": "AWS::StackId"}]}]}]]},
            "ManagedLoginVersion": 2,
        }},
        "LoginBranding": {"Type": "AWS::Cognito::ManagedLoginBranding", "Properties": {
            "UserPoolId": {"Ref": "Pool"}, "ClientId": {"Ref": "Client"}, "UseCognitoProvidedValues": True,
        }},
    }
    for logical, group, description in [
        ("ResearchMembers", "studio-research", "Research workspace members"),
        ("OperationsMembers", "studio-operations", "Operations workspace members"),
        ("Administrators", "studio-admin", "Governance administrators; does not grant access to private agent content"),
    ]:
        resources[logical] = {"Type": "AWS::Cognito::UserPoolGroup", "Properties": {"UserPoolId": {"Ref": "Pool"}, "GroupName": group, "Description": description}}
    return {"AWSTemplateFormatVersion": "2010-09-09", "Description": "Isolated invite-only Agent Studio identity; no existing pool mutations",
            "Resources": resources, "Outputs": {
                "UserPoolId": {"Value": {"Ref": "Pool"}}, "ClientId": {"Value": {"Ref": "Client"}},
                "CognitoDomain": {"Value": {"Fn::Sub": "https://${Domain}.auth.${AWS::Region}.amazoncognito.com"}},
                "ApplicationOrigin": {"Value": origin},
            }}

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--application-origin", required=True)
    args = parser.parse_args()
    print(json.dumps(template(args.application_origin), indent=2))
