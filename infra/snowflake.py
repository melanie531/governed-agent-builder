"""Private credential storage; PAT values never enter CloudFormation."""


def credential_template():
    return {
        "AWSTemplateFormatVersion": "2010-09-09",
        "Description": "Governed Agent Builder Snowflake PAT storage",
        "Resources": {
            "SnowflakePAT": {
                "Type": "AWS::SecretsManager::Secret",
                "DeletionPolicy": "Retain",
                "UpdateReplacePolicy": "Retain",
                "Properties": {
                    "Name": "governed-agent-builder-serverless/snowflake-pat",
                    "Description": "Role-scoped Snowflake managed MCP PAT; JSON field pat",
                    "Tags": [
                        {"Key": "auto-delete", "Value": "no"},
                        {"Key": "project", "Value": "governed-agent-builder"},
                        {"Key": "journey", "Value": "create-agent"},
                    ],
                },
            },
        },
        "Outputs": {"SecretArn": {"Value": {"Ref": "SnowflakePAT"}}},
    }
