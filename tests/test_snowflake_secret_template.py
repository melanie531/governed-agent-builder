def test_snowflake_pat_has_a_retained_tagged_secret_without_a_template_value():
    from infra.snowflake import credential_template

    body = credential_template()
    assert set(body["Resources"]) == {"SnowflakePAT"}
    secret = body["Resources"]["SnowflakePAT"]
    assert secret["Type"] == "AWS::SecretsManager::Secret"
    assert secret["DeletionPolicy"] == secret["UpdateReplacePolicy"] == "Retain"
    properties = secret["Properties"]
    assert properties["Name"] == "governed-agent-builder-serverless/snowflake-pat"
    assert {"Key": "auto-delete", "Value": "no"} in properties["Tags"]
    assert "SecretString" not in properties
    assert "GenerateSecretString" not in properties
    assert body["Outputs"]["SecretArn"]["Value"] == {"Ref": "SnowflakePAT"}
