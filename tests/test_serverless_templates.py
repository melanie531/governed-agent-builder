import json
from infra.serverless import template, artifacts_template
from scripts.serverless_deploy import safety


def test_no_vpc_dependencies_or_shared_resources():
    body = template()
    safety(body)
    assert all(not r["Type"].startswith("AWS::EC2") for r in body["Resources"].values())
    assert "AdministratorAccess" not in json.dumps(body)
    assert "AWS::CloudFront::VpcOrigin" not in json.dumps(body)
    for name in ("Web", "Exports"):
        assert all(body["Resources"][name]["Properties"]["PublicAccessBlockConfiguration"].values())
    assert body["Resources"]["State"]["Properties"]["SSESpecification"]["SSEEnabled"]
    assert body["Resources"]["State"]["Properties"]["PointInTimeRecoverySpecification"]["PointInTimeRecoveryEnabled"]


def test_business_authorizer_and_separate_auth_routes():
    r = template()["Resources"]
    auth = r["SessionAuthorizer"]["Properties"]
    assert auth["AuthorizerResultTtlInSeconds"] == 0
    routes = [x["Properties"] for x in r.values() if x["Type"] == "AWS::ApiGatewayV2::Route"]
    for route in routes:
        if "/api" in route["RouteKey"]:
            assert route["AuthorizationType"] == "CUSTOM"
        elif route["RouteKey"] == "POST /internal/foundation/exchange":
            assert route["AuthorizationType"] == "AWS_IAM"
            assert "FoundationExchangeIntegration" in str(route["Target"])
            assert "AuthorizerId" not in route
        else:
            assert route["RouteKey"] in ("GET /auth/login", "GET /auth/callback", "GET /studio-config.json", "GET /auth/verification/status", "POST /auth/verification/send", "POST /auth/verification/verify")
            assert "AuthIntegration" in str(route["Target"])
    assert r["Pool"]["Properties"]["AdminCreateUserConfig"]["AllowAdminCreateUserOnly"]
    assert not r["Client"]["Properties"]["GenerateSecret"]
    assert r["Pool"]["Properties"]["EnabledMfas"] == ["SOFTWARE_TOKEN_MFA"]
    assert "SoftwareTokenMfaConfiguration" not in r["Pool"]["Properties"]


def test_scoped_roles_and_durable_queue():
    r = template()["Resources"]
    for name in ("Auth", "Authorizer"):
        statements = r[name+"Role"]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
        keys = [s["Condition"]["ForAllValues:StringEquals"]["dynamodb:LeadingKeys"] for s in statements if "Condition" in s]
        assert keys and all("agents" not in allowed and "versions" not in allowed for allowed in keys)
    assert r["WorkerMapping"]["Properties"]["FunctionResponseTypes"] == ["ReportBatchItemFailures"]
    assert r["Jobs"]["Properties"]["VisibilityTimeout"] >= r["Worker"]["Properties"]["Timeout"] * 6
    assert r["StreamMapping"]["Properties"]["DestinationConfig"]["OnFailure"]
    assert r["DeadLettersAlarm"] and r["DispatchFailuresAlarm"]
    assert artifacts_template()["Resources"]["Releases"]["DeletionPolicy"] == "Retain"


def test_verification_table_isolated_and_scope_only_new_stack():
    from infra.identity import template as old_template
    r = template()["Resources"]
    v = r["Verification"]["Properties"]
    assert v["SSESpecification"]["SSEEnabled"]
    assert v["TimeToLiveSpecification"] == {"AttributeName": "expires", "Enabled": True}
    assert "StreamSpecification" not in v and "PointInTimeRecoverySpecification" not in v
    for name in ("Business", "Authorizer", "Worker", "Dispatcher"):
        assert "Verification" not in json.dumps(r[name+"Role"])
        assert "VERIFICATION_TABLE" not in r[name]["Properties"]["Environment"]["Variables"]
    assert "Verification" in json.dumps(r["AuthRole"])
    assert "cognito-idp:" not in json.dumps(r["AuthRole"])
    assert "aws.cognito.signin.user.admin" in r["Client"]["Properties"]["AllowedOAuthScopes"]
    assert "aws.cognito.signin.user.admin" not in old_template("https://example.test")["Resources"]["Client"]["Properties"]["AllowedOAuthScopes"]


def test_changeset_review_rejects_pool_changes_and_replacement():
    import pytest
    from scripts.serverless_deploy import review_verification_changes
    for change in ({"LogicalResourceId":"Pool","Action":"Modify","Replacement":"False"},
                   {"LogicalResourceId":"Auth","Action":"Modify","Replacement":"True"},
                   {"LogicalResourceId":"Client","Action":"Remove"}):
        with pytest.raises(RuntimeError): review_verification_changes([{"ResourceChange":change}], {"Pool","Auth","Client"})
    review_verification_changes([{"ResourceChange":{"LogicalResourceId":"Auth","Action":"Modify","Replacement":"False"}}], {"Auth"})


def test_changeset_allows_only_unchanged_lambda_reference_dependencies():
    import pytest
    from scripts.serverless_deploy import review_verification_changes
    resource = {"LogicalResourceId":"AuthIntegration","Action":"Modify","Replacement":"False", "Details":[{
        "ChangeSource":"ResourceAttribute","CausingEntity":"Auth.Arn","Target":{"Name":"IntegrationUri","RequiresRecreation":"Never"}}]}
    review_verification_changes([{"ResourceChange":resource}], {"AuthIntegration"})
    resource["Details"][0]["ChangeSource"] = "DirectModification"
    with pytest.raises(RuntimeError): review_verification_changes([{"ResourceChange":resource}], {"AuthIntegration"})
